package com.msdkremote.livevideo;

import java.util.function.LongSupplier;

/** Pure video activation policy. An enable return is never evidence of raw video. */
final class StreamActivationPolicy {
    enum Effect { NONE, ENABLE }
    enum State { STOPPED, WAIT_MANAGER, WAIT_CAMERA, WAIT_ENABLE_REPORT, WAIT_RAW, STREAMING, STALLED }
    private final LongSupplier clock;
    private boolean running, managerAvailable, cameraAvailable;
    private Boolean reportedEnabled;
    private long selectedAt = -1, reportAt = -1, firstRawAt = -1, lastRawAt = -1;
    private long lastActivationAt = -1, nextActivationAt;
    private int attempts, consecutiveAttempts;

    StreamActivationPolicy(LongSupplier clock) { this.clock = clock; }

    void reset(boolean running, boolean managerAvailable, boolean cameraAvailable) {
        this.running = running;
        this.managerAvailable = managerAvailable;
        this.cameraAvailable = cameraAvailable;
        reportedEnabled = null;
        selectedAt = clock.getAsLong();
        reportAt = firstRawAt = lastRawAt = lastActivationAt = -1;
        nextActivationAt = 0;
        attempts = consecutiveAttempts = 0;
    }

    void report(Boolean value) {
        reportedEnabled = value;
        reportAt = clock.getAsLong();
    }

    void raw(long at) {
        boolean resumed = lastRawAt < 0 || at - lastRawAt >= 3000;
        if (firstRawAt < 0) firstRawAt = at;
        lastRawAt = at;
        if (resumed) {
            consecutiveAttempts = 0;
            // Preserve the minimum interval even if an SDK report arrives synchronously.
            nextActivationAt = lastActivationAt < 0 ? 0 : lastActivationAt + 3000;
        }
    }

    Effect reconcile() {
        long now = clock.getAsLong();
        if (!running || !managerAvailable || !cameraAvailable || now < nextActivationAt)
            return Effect.NONE;
        boolean freshRaw = lastRawAt >= 0 && now - lastRawAt < 3000;
        if (Boolean.TRUE.equals(reportedEnabled) || (reportedEnabled == null && freshRaw))
            return Effect.NONE;
        // Reserve the attempt before the adapter enters the SDK (which may call back inline).
        long delay = Math.min(30000L, 3000L << Math.min(consecutiveAttempts, 4));
        attempts++;
        consecutiveAttempts++;
        lastActivationAt = now;
        nextActivationAt = now + delay;
        return Effect.ENABLE;
    }

    State state() {
        if (!running) return State.STOPPED;
        if (!managerAvailable) return State.WAIT_MANAGER;
        if (!cameraAvailable) return State.WAIT_CAMERA;
        long now = clock.getAsLong();
        if (lastRawAt >= 0 && now - lastRawAt < 3000) return State.STREAMING;
        if (lastRawAt >= 0 || (Boolean.TRUE.equals(reportedEnabled) && now - selectedAt >= 3000))
            return State.STALLED;
        return Boolean.TRUE.equals(reportedEnabled) ? State.WAIT_RAW : State.WAIT_ENABLE_REPORT;
    }

    Boolean reportedEnabled() { return reportedEnabled; }
    long reportAt() { return reportAt; }
    long firstRawAt() { return firstRawAt; }
    long lastRawAt() { return lastRawAt; }
    long lastActivationAt() { return lastActivationAt; }
    long nextActivationAt() { return nextActivationAt; }
    int attempts() { return attempts; }
}
