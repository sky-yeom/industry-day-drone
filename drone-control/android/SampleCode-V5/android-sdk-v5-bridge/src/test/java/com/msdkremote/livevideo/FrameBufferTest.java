package com.msdkremote.livevideo;

import org.junit.Test;
import static org.junit.Assert.*;

public class FrameBufferTest {
    @Test(timeout=2000) public void cameraGenerationRejectsOldCopiedFrameWithoutReplacingReader() throws Exception {
        long[] now = {0};
        FrameBuffer buffer = new FrameBuffer(100, () -> now[0]);
        long reader = buffer.openReader();
        buffer.beginCameraGeneration(1);
        assertTrue(buffer.addFrame(frame(20, true), 1));
        assertEquals(0, buffer.cameraAgeMs());
        Frame copiedBeforeStop = frame(20, true);
        buffer.beginCameraGeneration(2);
        assertEquals(-1, buffer.cameraAgeMs());
        assertFalse(buffer.addFrame(copiedBeforeStop, 1));
        Frame current = frame(30, true);
        now[0] = 100;
        assertTrue(buffer.addFrame(current, 2));
        assertSame(current, buffer.getFrame(reader));
    }

    @Test public void sdkByteArrayIsCopiedBeforeCallbackReturns() {
        byte[] sdk = {1, 2, 3};
        Frame frame = new Frame(sdk, 0, sdk.length, 1, 1, true, 0, 30, null);
        sdk[0] = 99;
        assertArrayEquals(new byte[]{1, 2, 3}, frame.getData());
    }

    private Frame frame(int size, boolean key) {
        return new Frame(new byte[size], 0, size, 1080, 1920, key, 0, 30, null);
    }

    @Test public void largeKeyframeIsNotSilentlyDiscarded() {
        FrameBuffer buffer = new FrameBuffer(100, () -> 1000L);
        buffer.addFrame(frame(150, true));
        assertEquals("A single I-frame larger than the queue budget must survive", 150, buffer.getBufferSize());
    }

    @Test(timeout=2000) public void normalStreamStillDeliversInOrder() throws Exception {
        FrameBuffer buffer = new FrameBuffer(100, () -> 1000L);
        Frame key = frame(40, true), delta = frame(20, false);
        buffer.addFrame(key); buffer.addFrame(delta);
        assertSame(key, buffer.getFrame()); assertSame(delta, buffer.getFrame());
    }

    @Test(timeout=2000) public void largeKeyframeRetainsFollowingDelta() throws Exception {
        FrameBuffer buffer = new FrameBuffer(100, () -> 1000L);
        Frame key = frame(150, true), delta = frame(50, false);
        buffer.addFrame(key); buffer.addFrame(delta);
        assertEquals(200, buffer.getBufferSize());
        assertSame(key, buffer.getFrame()); assertSame(delta, buffer.getFrame());
    }

    @Test(timeout=2000) public void overflowWaitsForNextIndependentFrame() throws Exception {
        FrameBuffer buffer = new FrameBuffer(100, () -> 1000L);
        buffer.addFrame(frame(60, true)); buffer.addFrame(frame(60, false));
        Frame next = frame(20, true);
        buffer.addFrame(next);
        assertSame(next, buffer.getFrame());
        assertEquals(0, buffer.getBufferSize());
    }

    @Test(timeout=2000) public void replacedReaderCannotStealNewKeyframe() throws Exception {
        FrameBuffer buffer = new FrameBuffer(100, () -> 1000L);
        long old = buffer.openReader(), current = buffer.openReader();
        Frame key = frame(50, true); buffer.addFrame(key);
        try { buffer.getFrame(old); fail("Old reader must be cancelled"); }
        catch (InterruptedException expected) { }
        assertSame(key, buffer.getFrame(current));
    }

    @Test(timeout=2000) public void replacementWakesBlockedReader() throws Exception {
        FrameBuffer buffer = new FrameBuffer(100, () -> 1000L);
        long old = buffer.openReader();
        java.util.concurrent.atomic.AtomicBoolean cancelled = new java.util.concurrent.atomic.AtomicBoolean();
        Thread thread = new Thread(() -> {
            try { buffer.getFrame(old); }
            catch (InterruptedException expected) { cancelled.set(true); }
        });
        thread.start();
        buffer.openReader();
        thread.join(1000);
        assertFalse(thread.isAlive()); assertTrue(cancelled.get());
    }

    @Test(timeout=2000) public void hugeMalformedFrameCannotGrowQueueWithoutLimit() throws Exception {
        FrameBuffer buffer = new FrameBuffer(100, () -> 1000L);
        buffer.addFrame(frame(8_000_001, true));
        assertEquals(0, buffer.getBufferSize());
        Frame key = frame(40, true); buffer.addFrame(key);
        assertSame(key, buffer.getFrame());
    }

    @Test(timeout=2000) public void aHundredReconnectsKeepNewReaderFrames() throws Exception {
        FrameBuffer buffer = new FrameBuffer(100, () -> 1000L);
        long previous = buffer.openReader();
        for (int i = 0; i < 100; i++) {
            long current = buffer.openReader();
            Frame key = frame(50, true); buffer.addFrame(key);
            try { buffer.getFrame(previous); fail("Old reader survived"); }
            catch (InterruptedException expected) { }
            assertSame(key, buffer.getFrame(current));
            previous = current;
        }
    }
    @Test public void continuousLargeFramesStayMemoryBounded() {
        FrameBuffer buffer = new FrameBuffer(100, () -> 1000L);
        for (int i=0; i<1000; i++) {
            buffer.addFrame(frame(150, i % 30 == 0));
            assertTrue(buffer.getBufferSize() <= 250);
        }
    }
}
