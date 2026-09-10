package com.msdkremote.livevideo;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.concurrent.Executor;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicLong;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.Consumer;
import java.util.function.Function;
import java.util.function.LongSupplier;
import java.util.function.Supplier;

/** SDK-free adapter: one worker owns effects; SDK callbacks only copy/enqueue data. */
final class StreamBindingController<C, F> {
    interface CameraEvents<C> {
        void available(List<C> cameras);
        void enabled(Map<C, Boolean> states);
    }
    interface Manager<C, F> {
        Object identity();
        void addAvailable(CameraEvents<C> listener);
        void removeAvailable(CameraEvents<C> listener);
        void addReceiver(C camera, Consumer<F> receiver);
        void removeReceiver(Consumer<F> receiver);
        void enable(C camera);
    }
    interface FrameSink<F> {
        void beginGeneration(long generation);
        boolean add(F frame, long generation);
    }

    private final SerialExecutor worker;
    private final LongSupplier clock;
    private final Supplier<Manager<C, F>> provider;
    private final Function<List<C>, C> selectCamera;
    private final FrameSink<F> sink;
    private final StreamActivationPolicy policy;
    private final AtomicBoolean desired = new AtomicBoolean();
    private final AtomicLong requestedEpoch = new AtomicLong(), cameraEpoch = new AtomicLong();
    private final AtomicLong oldCallbacks = new AtomicLong();
    private final AtomicReference<Map<String, Object>> published = new AtomicReference<>(Collections.emptyMap());
    private Manager<C, F> manager;
    private CameraEvents<C> availableListener;
    private Consumer<F> receiver;
    private C camera;
    private Map<C, Boolean> reports = Collections.emptyMap();
    private long activeEpoch = -1, bindingAttempts, nextBindAt;
    private long bindingRetryMs = 3000;
    private long nextReceiverAt, receiverRetryMs = 3000;
    private String lastError;

    StreamBindingController(Executor executor, LongSupplier clock, Supplier<Manager<C, F>> provider,
                            Function<List<C>, C> selectCamera, FrameSink<F> sink) {
        this.worker = new SerialExecutor(executor);
        this.clock = clock;
        this.provider = provider;
        this.selectCamera = selectCamera;
        this.sink = sink;
        this.policy = new StreamActivationPolicy(clock);
        policy.reset(false, false, false);
        publish();
    }

    void start() {
        if (desired.compareAndSet(false, true)) {
            requestedEpoch.incrementAndGet();
            invalidateCamera();
        }
        ensure();
    }

    void ensure() { worker.execute(this::ensureWorker); }

    void stop() {
        desired.set(false);
        long ticket = requestedEpoch.incrementAndGet();
        invalidateCamera(); // synchronous buffer barrier, including a raw callback copying right now
        worker.execute(() -> {
            if (ticket != requestedEpoch.get()) return;
            detach();
            policy.reset(false, false, false);
            publish();
        });
    }

    void recoverReceiver() {
        long ticket = requestedEpoch.get();
        worker.execute(() -> {
            if (!current(ticket, manager) || camera == null) return;
            C selected = camera;
            removeReceiver();
            bindCamera(selected);
            reconcile();
        });
    }

    private void ensureWorker() {
        if (!desired.get()) return;
        Manager<C, F> current;
        try { current = provider.get(); }
        catch (RuntimeException error) { error("manager lookup", error); publish(); return; }
        boolean replaced = manager != null && (current == null || manager.identity() != current.identity());
        if (replaced) {
            requestedEpoch.incrementAndGet();
            invalidateCamera();
            nextBindAt = 0;
        }
        if (replaced || activeEpoch != requestedEpoch.get()) detach();
        if (current == null) {
            policy.reset(true, false, false);
            publish();
            return;
        }
        if (manager == null && clock.getAsLong() >= nextBindAt) {
            manager = current;
            activeEpoch = requestedEpoch.get();
            policy.reset(true, true, false);
            final long ticket = activeEpoch;
            final Manager<C, F> owner = manager;
            availableListener = new CameraEvents<C>() {
                @Override public void available(List<C> list) {
                    final List<C> copy = new ArrayList<>(list);
                    worker.execute(() -> onAvailable(ticket, owner, copy));
                }
                @Override public void enabled(Map<C, Boolean> states) {
                    final Map<C, Boolean> copy = new HashMap<>(states);
                    worker.execute(() -> {
                        if (!current(ticket, owner)) { oldCallbacks.incrementAndGet(); return; }
                        reports = copy;
                        if (camera != null) policy.report(reports.get(camera));
                        reconcile();
                    });
                }
            };
            try {
                bindingAttempts++;
                manager.addAvailable(availableListener);
                bindingRetryMs = 3000;
                nextBindAt = 0;
            } catch (RuntimeException error) {
                error("available listener add", error);
                detach();
                nextBindAt = clock.getAsLong() + bindingRetryMs;
                bindingRetryMs = Math.min(30000, bindingRetryMs * 2);
                policy.reset(true, false, false);
            }
        }
        if (manager != null && camera != null && receiver == null && clock.getAsLong() >= nextReceiverAt) {
            bindCamera(camera);
        }
        reconcile();
    }

    private boolean current(long ticket, Manager<C, F> owner) {
        return desired.get() && ticket == requestedEpoch.get() && ticket == activeEpoch
                && owner != null && owner == manager;
    }

    private void onAvailable(long ticket, Manager<C, F> owner, List<C> list) {
        if (!current(ticket, owner)) { oldCallbacks.incrementAndGet(); return; }
        C selected = list.isEmpty() ? null : selectCamera.apply(list);
        if (!Objects.equals(camera, selected)) {
            removeReceiver();
            camera = null;
            nextReceiverAt = 0;
            receiverRetryMs = 3000;
            invalidateCamera();
            if (selected == null) policy.reset(true, true, false);
            else bindCamera(selected);
        }
        reconcile();
    }

    private void bindCamera(C selected) {
        camera = selected;
        final long generation = invalidateCamera();
        final long ticket = activeEpoch;
        final Manager<C, F> owner = manager;
        policy.reset(true, true, true);
        if (reports.containsKey(selected)) policy.report(reports.get(selected));
        receiver = frame -> {
            // F must already own its byte copy before this callback is entered.
            if (!desired.get() || ticket != requestedEpoch.get() || generation != cameraEpoch.get()) {
                oldCallbacks.incrementAndGet();
                return;
            }
            if (!sink.add(frame, generation)) { oldCallbacks.incrementAndGet(); return; }
            long at = clock.getAsLong();
            worker.execute(() -> {
                if (!current(ticket, owner) || generation != cameraEpoch.get()) return;
                policy.raw(at);
                lastError = null;
                publish();
            });
        };
        try {
            bindingAttempts++;
            manager.addReceiver(selected, receiver);
            nextReceiverAt = 0;
            receiverRetryMs = 3000;
        } catch (RuntimeException error) {
            error("receiver add", error);
            removeReceiver();
            nextReceiverAt = clock.getAsLong() + receiverRetryMs;
            receiverRetryMs = Math.min(30000, receiverRetryMs * 2);
        }
    }

    private void reconcile() {
        if (desired.get() && manager != null && camera != null && receiver != null &&
                policy.reconcile() == StreamActivationPolicy.Effect.ENABLE) {
            try { manager.enable(camera); }
            catch (RuntimeException error) { error("enable stream", error); }
        }
        publish();
    }

    private long invalidateCamera() {
        long value = cameraEpoch.incrementAndGet();
        sink.beginGeneration(value);
        return value;
    }

    private void removeReceiver() {
        Consumer<F> old = receiver;
        receiver = null;
        if (manager != null && old != null) {
            try { manager.removeReceiver(old); }
            catch (RuntimeException error) { error("receiver remove", error); }
        }
    }

    private void detach() {
        removeReceiver();
        CameraEvents<C> old = availableListener;
        availableListener = null;
        if (manager != null && old != null) {
            try { manager.removeAvailable(old); }
            catch (RuntimeException error) { error("available listener remove", error); }
        }
        camera = null;
        manager = null;
        reports = Collections.emptyMap();
        activeEpoch = -1;
        nextReceiverAt = 0;
        receiverRetryMs = 3000;
    }

    private void error(String stage, RuntimeException error) {
        lastError = stage + ": " + error.getClass().getSimpleName() + ": " + error.getMessage();
    }

    private void publish() {
        long now = clock.getAsLong();
        Map<String, Object> value = new LinkedHashMap<>();
        value.put("binding_running", desired.get());
        value.put("desired_enabled", desired.get());
        value.put("binding_generation", requestedEpoch.get());
        value.put("camera_generation", cameraEpoch.get());
        value.put("binding_attempts", bindingAttempts);
        value.put("manager_identity", manager == null ? null : System.identityHashCode(manager.identity()));
        value.put("camera_selected", camera == null ? null : camera.toString());
        value.put("receiver_registered", receiver != null);
        value.put("available_listener_registered", availableListener != null);
        value.put("reported_enabled", policy.reportedEnabled());
        value.put("stream_enabled", policy.reportedEnabled());
        value.put("activation_state", policy.state().name());
        value.put("activation_attempts", policy.attempts());
        value.put("last_activation_at_ms", policy.lastActivationAt());
        value.put("next_activation_at_ms", policy.nextActivationAt());
        value.put("retry_in_ms", Math.max(0, Math.max(nextReceiverAt, Math.max(nextBindAt, policy.nextActivationAt())) - now));
        value.put("enable_report_age_ms", policy.reportAt() < 0 ? -1 : now - policy.reportAt());
        value.put("first_raw_at_ms", policy.firstRawAt());
        value.put("last_raw_at_ms", policy.lastRawAt());
        value.put("binding_error", lastError);
        value.put("old_callback_drops", oldCallbacks.get());
        value.put("android_elapsed_ms", now);
        published.set(Collections.unmodifiableMap(value));
    }

    Map<String, Object> snapshot() {
        Map<String, Object> value = new LinkedHashMap<>(published.get());
        value.put("old_callback_drops", oldCallbacks.get());
        value.put("requested_epoch", requestedEpoch.get());
        if (!desired.get()) {
            value.put("binding_running", false);
            value.put("desired_enabled", false);
            value.put("activation_state", "STOPPED");
        }
        return Collections.unmodifiableMap(value);
    }

    /** Trampoline prevents synchronous SDK callbacks from recursively entering effects. */
    private static final class SerialExecutor implements Executor {
        private final Executor executor;
        private final ArrayDeque<Runnable> queue = new ArrayDeque<>();
        private boolean draining;
        SerialExecutor(Executor executor) { this.executor = executor; }
        @Override public void execute(Runnable command) {
            synchronized (queue) {
                queue.add(command);
                if (draining) return;
                draining = true;
            }
            executor.execute(() -> {
                while (true) {
                    Runnable task;
                    synchronized (queue) {
                        task = queue.poll();
                        if (task == null) { draining = false; return; }
                    }
                    try { task.run(); }
                    catch (RuntimeException error) {
                        // Preserve future teardown scheduling even when an unexpected callback fails.
                        synchronized (queue) { draining = false; }
                        throw error;
                    }
                }
            });
        }
    }
}
