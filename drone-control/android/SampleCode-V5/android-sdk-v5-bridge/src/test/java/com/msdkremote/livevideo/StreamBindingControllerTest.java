package com.msdkremote.livevideo;

import org.junit.Test;
import static org.junit.Assert.*;
import java.util.*;
import java.util.concurrent.Executor;
import java.util.function.Consumer;

public class StreamBindingControllerTest {
    static final class ManualExecutor implements Executor {
        final Queue<Runnable> tasks = new ArrayDeque<>();
        public void execute(Runnable command) { tasks.add(command); }
        void drain() { while (!tasks.isEmpty()) tasks.remove().run(); }
    }
    static final class Manager implements StreamBindingController.Manager<String, String> {
        StreamBindingController.CameraEvents<String> events;
        Consumer<String> receiver;
        int addAvailable, removeAvailable, addReceiver, removeReceiver, enables, depth, maxDepth;
        boolean inline, receiverRemoveThrows, receiverAddThrows;
        String enabledCamera;
        public Object identity() { return this; }
        public void addAvailable(StreamBindingController.CameraEvents<String> listener) {
            events = listener; addAvailable++;
            if (inline) { listener.enabled(Collections.singletonMap("MAIN", false)); listener.available(Arrays.asList("MAIN")); }
        }
        public void removeAvailable(StreamBindingController.CameraEvents<String> listener) { removeAvailable++; }
        public void addReceiver(String camera, Consumer<String> receiver) {
            this.receiver = receiver; addReceiver++;
            if (receiverAddThrows) throw new IllegalStateException("add failure");
        }
        public void removeReceiver(Consumer<String> receiver) {
            removeReceiver++;
            if (receiverRemoveThrows) throw new IllegalStateException("remove failure");
        }
        public void enable(String camera) {
            depth++; maxDepth = Math.max(depth, maxDepth);
            enables++; enabledCamera = camera;
            events.enabled(Collections.singletonMap(camera, false));
            depth--;
        }
    }
    static final class Sink implements StreamBindingController.FrameSink<String> {
        long generation;
        final List<String> frames = new ArrayList<>();
        public void beginGeneration(long value) { generation = value; frames.clear(); }
        public boolean add(String frame, long value) {
            if (value != generation) return false;
            frames.add(frame); return true;
        }
    }
    long now;
    Manager manager = new Manager();
    final Sink sink = new Sink();
    StreamBindingController<String, String> controller(Executor executor) {
        return new StreamBindingController<>(executor, () -> now, () -> manager,
                list -> list.contains("MAIN") ? "MAIN" : list.get(0), sink);
    }

    @Test public void delayedFalseReportEnablesWithoutDetachingAvailableListener() {
        StreamBindingController<String, String> c = controller(Runnable::run);
        c.start(); manager.events.available(Arrays.asList("MAIN"));
        manager.events.enabled(Collections.singletonMap("MAIN", false));
        for (int i=0; i<20; i++) c.ensure();
        assertEquals(1, manager.enables); assertEquals(1, manager.addAvailable);
        assertEquals(0, manager.removeAvailable);
        now = 3000; c.ensure(); assertEquals(2, manager.enables);
    }
    @Test public void synchronousCallbacksDoNotReenterEnableWithDirectOrQueuedExecutor() {
        for (boolean queued : new boolean[]{false, true}) {
            manager = new Manager(); manager.inline = true;
            ManualExecutor executor = new ManualExecutor();
            StreamBindingController<String, String> c = controller(queued ? executor : Runnable::run);
            c.start(); executor.drain();
            assertEquals(1, manager.enables); assertEquals(1, manager.maxDepth);
            assertEquals(1, manager.addAvailable);
        }
    }
    @Test public void earlyEnableMapUsesTheActuallySelectedCamera() {
        StreamBindingController<String, String> c = controller(Runnable::run); c.start();
        Map<String, Boolean> states = new HashMap<>(); states.put("MAIN", false); states.put("OTHER", true);
        manager.events.enabled(states); manager.events.available(Arrays.asList("OTHER"));
        assertEquals(0, manager.enables);
        assertEquals("OTHER", c.snapshot().get("camera_selected"));
        assertEquals("WAIT_RAW", c.snapshot().get("activation_state"));
    }
    @Test public void unknownReportIsNotClearedByEnsureAndRawIsNeededForStreaming() {
        StreamBindingController<String, String> c = controller(Runnable::run); c.start();
        manager.events.available(Arrays.asList("MAIN"));
        manager.events.enabled(Collections.singletonMap("MAIN", true));
        c.ensure(); assertEquals("WAIT_RAW", c.snapshot().get("activation_state"));
        manager.receiver.accept("new raw");
        assertEquals("STREAMING", c.snapshot().get("activation_state"));
        assertEquals(Arrays.asList("new raw"), sink.frames);
    }
    @Test public void managerReplacementDiscardsOldReportsAndRawWithoutWaitingForBackoff() {
        StreamBindingController<String, String> c = controller(Runnable::run); c.start();
        manager.events.available(Arrays.asList("MAIN"));
        Manager old = manager; Consumer<String> oldRaw = old.receiver;
        oldRaw.accept("old"); manager = new Manager(); c.ensure();
        old.events.enabled(Collections.singletonMap("MAIN", true)); oldRaw.accept("late old");
        assertEquals(1, old.removeAvailable); assertEquals(1, old.removeReceiver);
        assertEquals(1, manager.addAvailable); assertTrue(sink.frames.isEmpty());
        assertEquals("WAIT_CAMERA", c.snapshot().get("activation_state"));
        assertTrue(((Number)c.snapshot().get("old_callback_drops")).longValue() >= 2);
    }
    @Test public void stoppedCallbacksAreInvalidBeforeTheWorkerDrains() {
        ManualExecutor executor = new ManualExecutor();
        StreamBindingController<String, String> c = controller(executor); c.start(); executor.drain();
        manager.events.available(Arrays.asList("MAIN")); executor.drain();
        Consumer<String> late = manager.receiver;
        c.stop(); late.accept("copied before stop, delivered late");
        assertTrue(sink.frames.isEmpty()); assertEquals("STOPPED", c.snapshot().get("activation_state"));
        executor.drain(); assertEquals(1, manager.removeAvailable);
    }
    @Test public void receiverRemoveFailureDoesNotSkipAvailableRemove() {
        StreamBindingController<String, String> c = controller(Runnable::run); c.start();
        manager.events.available(Arrays.asList("MAIN")); manager.receiverRemoveThrows = true;
        c.stop(); assertEquals(1, manager.removeAvailable); assertEquals(1, manager.removeReceiver);
        assertEquals("STOPPED", c.snapshot().get("activation_state"));
    }
    @Test public void emptyCameraListInvalidatesOldRawAndRetainsAvailableSubscription() {
        StreamBindingController<String, String> c = controller(Runnable::run); c.start();
        manager.events.available(Arrays.asList("MAIN")); Consumer<String> old = manager.receiver;
        old.accept("first"); manager.events.available(Collections.emptyList()); old.accept("late");
        assertTrue(sink.frames.isEmpty()); assertEquals("WAIT_CAMERA", c.snapshot().get("activation_state"));
        assertEquals(0, manager.removeAvailable);
        manager.events.available(Arrays.asList("MAIN")); assertEquals(2, manager.addReceiver);
    }
    @Test public void restartAfterQueuedStopDoesNotReuseAnOldBinding() {
        ManualExecutor executor = new ManualExecutor(); StreamBindingController<String, String> c = controller(executor);
        c.start(); executor.drain(); manager.events.available(Arrays.asList("MAIN")); executor.drain();
        Consumer<String> old = manager.receiver;
        c.stop(); c.start(); executor.drain(); old.accept("old"); executor.drain();
        assertEquals(2, manager.addAvailable); assertTrue(sink.frames.isEmpty());
        assertEquals("WAIT_CAMERA", c.snapshot().get("activation_state"));
    }
    @Test public void receiverAddFailureRetriesWithoutReplacingTheAvailableListener() {
        StreamBindingController<String, String> c = controller(Runnable::run); c.start();
        manager.receiverAddThrows = true; manager.events.available(Arrays.asList("MAIN"));
        for (int i = 0; i < 10; i++) { c.ensure(); manager.events.available(Arrays.asList("MAIN")); }
        assertEquals(1, manager.addReceiver); assertEquals(0, manager.enables);
        now = 3000; manager.receiverAddThrows = false; c.ensure();
        assertEquals(2, manager.addReceiver); assertEquals(1, manager.addAvailable);
        assertEquals(0, manager.removeAvailable); assertEquals(1, manager.enables);
    }
}
