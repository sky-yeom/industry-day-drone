package com.msdkremote.livevideo;

import java.util.LinkedList;
import java.util.Queue;
import java.util.function.LongSupplier;
import android.os.SystemClock;
import org.json.JSONObject;
import org.json.JSONException;

class FrameBuffer
{
    private final Queue<Frame> frames = new LinkedList<>();
    private final int maxBufferSize;
    private int bufferSize = 0;
    private boolean nextKeyFrame = true;
    private final int WAIT_TIMEOUT = 100;
    private final Object lock = new Object();
    private long receivedFrames, receivedBytes, deliveredFrames, droppedFrames;
    private long lastReceivedMs, lastDeliveredMs;
    private final LongSupplier clock;
    private static final int MAX_SINGLE_FRAME_BYTES = 8_000_000;
    private long readerGeneration;
    private long cameraGeneration, currentFrames, currentBytes, currentKeyFrames, oldCallbackDrops;
    private long currentFirstRawMs = -1, currentLastRawMs = -1, currentFirstKeyMs = -1, currentLastKeyMs = -1;
    private int currentWidth, currentHeight;
    private FrameCodec currentCodec;
    private long keyFrames, oversizedFrames, rejectedOversizedFrames, lastKeyFrameMs;
    private int largestFrameBytes;

    public JSONObject diagnostics() throws JSONException {
        synchronized (lock) {
            JSONObject result = new JSONObject();
            long now = clock.getAsLong();
            result.put("camera_frames", receivedFrames);
            result.put("camera_bytes", receivedBytes);
            result.put("delivered_frames", deliveredFrames);
            result.put("dropped_frames", droppedFrames);
            result.put("buffer_bytes", bufferSize);
            result.put("camera_age_ms", currentLastRawMs < 0 ? -1 : now - currentLastRawMs);
            result.put("delivery_age_ms", lastDeliveredMs == 0 ? -1 : now - lastDeliveredMs);
            result.put("waiting_keyframe", nextKeyFrame);
            result.put("input_keyframes", keyFrames);
            result.put("keyframe_age_ms", lastKeyFrameMs == 0 ? -1 : now - lastKeyFrameMs);
            result.put("largest_frame_bytes", largestFrameBytes);
            result.put("oversized_frames", oversizedFrames);
            result.put("rejected_oversized_frames", rejectedOversizedFrames);
            result.put("reader_generation", readerGeneration);
            result.put("camera_generation", cameraGeneration);
            result.put("current_camera_frames", currentFrames);
            result.put("current_camera_bytes", currentBytes);
            result.put("current_camera_keyframes", currentKeyFrames);
            result.put("current_first_raw_at_ms", currentFirstRawMs);
            result.put("current_first_keyframe_at_ms", currentFirstKeyMs);
            result.put("current_keyframe_age_ms", currentLastKeyMs < 0 ? -1 : now - currentLastKeyMs);
            result.put("current_width", currentWidth);
            result.put("current_height", currentHeight);
            result.put("current_codec", currentCodec == null ? JSONObject.NULL : currentCodec.toString());
            result.put("old_callback_drops", oldCallbackDrops);
            result.put("camera_counters_scope", "lifetime; current_camera_* is generation scoped");
            result.put("keyframe_request_sent", false);
            result.put("delivery_counter_stage", "queue_dequeue_not_socket_write");
            return result;
        }
    }

    public FrameBuffer(int maxBufferSize) {
        this(maxBufferSize, SystemClock::elapsedRealtime);
    }

    FrameBuffer(int maxBufferSize, LongSupplier clock) {
        if (maxBufferSize <= 0 || maxBufferSize > MAX_SINGLE_FRAME_BYTES)
            throw new IllegalArgumentException("Invalid video queue budget");
        this.maxBufferSize = maxBufferSize;
        this.clock = clock;
    }

    public int getMaxBufferSize() {
        return this.maxBufferSize;
    }

    public int getBufferSize() {
        synchronized (lock) { return this.bufferSize; }
    }

    public boolean isWaitingKeyframe() {
        synchronized (lock) { return nextKeyFrame; }
    }

    /** Discard queued data and wait for the next I-frame; sends no request to DJI. */
    public void nextKeyFrame() {
        synchronized (lock) {
            this.nextKeyFrame = true;
            droppedFrames += frames.size();
            frames.clear();
            bufferSize = 0;
            lock.notifyAll();
        }
    }

    public void addFrame(Frame frame)
    {
        synchronized (lock) { addFrame(frame, cameraGeneration); }
    }

    /** Invalidate old raw callbacks independently of the current socket reader. */
    public void beginCameraGeneration(long generation) {
        synchronized (lock) {
            if (generation < cameraGeneration) return;
            cameraGeneration = generation;
            currentFrames = currentBytes = currentKeyFrames = 0;
            currentFirstRawMs = currentLastRawMs = currentFirstKeyMs = currentLastKeyMs = -1;
            currentWidth = currentHeight = 0;
            currentCodec = null;
            nextKeyFrame();
        }
    }

    public boolean addFrame(Frame frame, long generation) {
        synchronized (lock) {
            if (generation != cameraGeneration) { oldCallbackDrops++; return false; }
            receivedFrames++;
            receivedBytes += frame.getSize();
            lastReceivedMs = clock.getAsLong();
            currentFrames++;
            currentBytes += frame.getSize();
            if (currentFirstRawMs < 0) currentFirstRawMs = lastReceivedMs;
            currentLastRawMs = lastReceivedMs;
            currentWidth = frame.getWidth(); currentHeight = frame.getHeight(); currentCodec = frame.getCodec();
            if (frame.isKeyFrame()) {
                currentKeyFrames++;
                if (currentFirstKeyMs < 0) currentFirstKeyMs = lastReceivedMs;
                currentLastKeyMs = lastReceivedMs;
            }
            largestFrameBytes = Math.max(largestFrameBytes, frame.getSize());
            if (frame.isKeyFrame()) { keyFrames++; lastKeyFrameMs = lastReceivedMs; }
            if (frame.getSize() > maxBufferSize) oversizedFrames++;
            if (frame.getSize() > MAX_SINGLE_FRAME_BYTES) {
                rejectedOversizedFrames++; droppedFrames++;
                nextKeyFrame();
                return true;
            }
            this.frames.add(frame);
            this.bufferSize += frame.getSize();

            // Retain one large frame plus the pending-data budget. Previously
            // every single I-frame >1 MB evicted itself, preventing decoding.
            while (this.bufferSize > queueBudget()) {
                Frame removedFrame = this.frames.poll();
                droppedFrames++;
                this.nextKeyFrame = true;

                if (removedFrame == null)
                    this.bufferSize = 0;
                else
                    this.bufferSize -= removedFrame.getSize();
            }

            lock.notifyAll();
            return true;
        }
    }

    private int queueBudget() {
        Frame head = frames.peek();
        return head != null && head.getSize() > maxBufferSize
                ? head.getSize() + maxBufferSize : maxBufferSize;
    }

    /** Reader tickets keep a replaced writer from consuming the new I-frame. */
    public long openReader() {
        synchronized (lock) {
            readerGeneration++;
            nextKeyFrame();
            return readerGeneration;
        }
    }

    private Frame getNextKeyFrame() throws InterruptedException
    {
        synchronized (lock) {
            Frame nextFrame = null;

            while (nextFrame == null || !nextFrame.isKeyFrame()) {
                if (frames.isEmpty())
                    lock.wait(WAIT_TIMEOUT);

                nextFrame = frames.poll();

                if (nextFrame == null)
                    this.bufferSize = 0;
                else
                    this.bufferSize -= nextFrame.getSize();
            }

            return nextFrame;
        }
    }

    private Frame getNextFrame() throws InterruptedException
    {
        synchronized (lock) {
            Frame nextFrame = null;

            while (nextFrame == null) {
                if (frames.isEmpty())
                    lock.wait(WAIT_TIMEOUT);

                nextFrame = frames.poll();

                if (nextFrame == null)
                    this.bufferSize = 0;
                else
                    this.bufferSize -= nextFrame.getSize();

            }

            return nextFrame;
        }
    }

    public Frame getFrame() throws InterruptedException {
        final long ticket;
        synchronized (lock) { ticket = readerGeneration; }
        return getFrame(ticket);
    }

    public Frame getFrame(long ticket) throws InterruptedException {
        synchronized (lock) {
            while (true) {
                if (ticket != readerGeneration || Thread.currentThread().isInterrupted())
                    throw new InterruptedException("Video reader replaced or stopped");
                Frame frame = frames.poll();
                if (frame == null) { lock.wait(100); continue; }
                bufferSize -= frame.getSize();
                if (nextKeyFrame && !frame.isKeyFrame()) {
                    droppedFrames++;
                    continue;
                }
                nextKeyFrame = false;
                deliveredFrames++;
                lastDeliveredMs = clock.getAsLong();
                return frame;
            }
        }
    }

    public long cameraAgeMs() {
        synchronized (lock) {
            return currentLastRawMs < 0 ? -1 : clock.getAsLong() - currentLastRawMs;
        }
    }
}
