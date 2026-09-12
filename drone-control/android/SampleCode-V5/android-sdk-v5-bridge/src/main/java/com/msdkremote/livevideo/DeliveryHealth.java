package com.msdkremote.livevideo;

/** Socket-write health, distinct from camera input and queue dequeue counters. */
final class DeliveryHealth {
    enum Recovery { NONE, REBIND_CAMERA, CLOSE_STALLED_CLIENT }
    private long generation, connectedMs, lastWriteMs = -1, writeStartedMs = -1;
    private long bytes, frames, lastRecoveryMs = -1, recoveries;
    private boolean connected;
    private int recoveriesWithoutProgress;
    private String recoveryReason = "none", recoveryBlocked = "not_evaluated";

    synchronized long connected(long now) {
        generation++; connected = true;
        connectedMs = now; lastWriteMs = writeStartedMs = -1;
        bytes = frames = 0;
        return generation;
    }
    synchronized void disconnected(long ticket) {
        if (ticket == generation) { connected = false; writeStartedMs = -1; }
    }
    synchronized void writeStarted(long ticket, long now) {
        if (connected && ticket == generation) writeStartedMs = now;
    }
    synchronized void written(long ticket, int size, long now) {
        if (!connected || ticket != generation) return;
        bytes += size; frames++; lastWriteMs = now; writeStartedMs = -1;
        recoveriesWithoutProgress = 0;
    }
    synchronized Recovery claimRecovery(long now, boolean groundDisarmed, long cameraAgeMs) {
        return claimRecovery(now, true, groundDisarmed, cameraAgeMs, true);
    }
    synchronized Recovery claimRecovery(long now, boolean enabled, boolean groundDisarmed,
                                        long cameraAgeMs, boolean waitingKeyframe) {
        recoveryReason = writeStartedMs >= 0 ? "socket_write_stalled"
                : waitingKeyframe ? "waiting_for_next_keyframe" : "queue_delivery_stalled";
        if (!enabled) { recoveryBlocked = "automatic_recovery_disabled"; return Recovery.NONE; }
        if (!groundDisarmed) { recoveryBlocked = "ground_not_fresh_or_control_armed"; return Recovery.NONE; }
        if (!connected) { recoveryBlocked = "no_client_normal"; return Recovery.NONE; }
        if (cameraAgeMs < 0 || cameraAgeMs >= 3000) {
            recoveryBlocked = "current_camera_raw_not_fresh"; return Recovery.NONE;
        }
        long lastProgress = lastWriteMs < 0 ? connectedMs : lastWriteMs;
        if (now - lastProgress < 3000) { recoveryBlocked = "progress_within_3s"; return Recovery.NONE; }
        if (lastRecoveryMs >= 0 && now - lastRecoveryMs < 10000) {
            recoveryBlocked = "recovery_cooldown_10s"; return Recovery.NONE;
        }
        if (recoveriesWithoutProgress >= 3) { recoveryBlocked = "recovery_budget_exhausted"; return Recovery.NONE; }
        recoveryBlocked = "none";
        lastRecoveryMs = now; recoveries++; recoveriesWithoutProgress++;
        return writeStartedMs >= 0 ? Recovery.CLOSE_STALLED_CLIENT : Recovery.REBIND_CAMERA;
    }
    synchronized Snapshot snapshot(long now) {
        return new Snapshot(generation, connected, bytes, frames,
                connected ? now - connectedMs : -1,
                lastWriteMs < 0 ? -1 : now - lastWriteMs,
                writeStartedMs < 0 ? -1 : now - writeStartedMs, recoveries,
                recoveryReason, recoveryBlocked, recoveriesWithoutProgress);
    }
    static final class Snapshot {
        final long generation, bytes, frames, connectionAgeMs, writeAgeMs, writingAgeMs, recoveries;
        final boolean connected;
        final String recoveryReason, recoveryBlocked;
        final int recoveriesWithoutProgress;
        Snapshot(long generation, boolean connected, long bytes, long frames, long connectionAgeMs,
                 long writeAgeMs, long writingAgeMs, long recoveries,
                 String recoveryReason, String recoveryBlocked, int recoveriesWithoutProgress) {
            this.generation = generation; this.connected = connected; this.bytes = bytes; this.frames = frames;
            this.connectionAgeMs = connectionAgeMs; this.writeAgeMs = writeAgeMs;
            this.writingAgeMs = writingAgeMs; this.recoveries = recoveries;
            this.recoveryReason = recoveryReason; this.recoveryBlocked = recoveryBlocked;
            this.recoveriesWithoutProgress = recoveriesWithoutProgress;
        }
    }
}
