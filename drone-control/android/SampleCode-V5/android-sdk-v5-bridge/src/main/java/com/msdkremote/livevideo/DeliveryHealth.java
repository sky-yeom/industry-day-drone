package com.msdkremote.livevideo;

/** Socket-write health, distinct from camera input and queue dequeue counters. */
final class DeliveryHealth {
    enum Recovery { NONE, REBIND_CAMERA, CLOSE_STALLED_CLIENT }
    private long generation, connectedMs, lastWriteMs = -1, writeStartedMs = -1;
    private long bytes, frames, lastRecoveryMs = -1, recoveries;
    private boolean connected;

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
    }
    synchronized Recovery claimRecovery(long now, boolean groundDisarmed, long cameraAgeMs) {
        if (!groundDisarmed || !connected || cameraAgeMs < 0 || cameraAgeMs >= 3000)
            return Recovery.NONE;
        long lastProgress = lastWriteMs < 0 ? connectedMs : lastWriteMs;
        if (now - lastProgress < 3000 || (lastRecoveryMs >= 0 && now - lastRecoveryMs < 10000))
            return Recovery.NONE;
        lastRecoveryMs = now; recoveries++;
        return writeStartedMs >= 0 ? Recovery.CLOSE_STALLED_CLIENT : Recovery.REBIND_CAMERA;
    }
    synchronized Snapshot snapshot(long now) {
        return new Snapshot(generation, connected, bytes, frames,
                connected ? now - connectedMs : -1,
                lastWriteMs < 0 ? -1 : now - lastWriteMs,
                writeStartedMs < 0 ? -1 : now - writeStartedMs, recoveries);
    }
    static final class Snapshot {
        final long generation, bytes, frames, connectionAgeMs, writeAgeMs, writingAgeMs, recoveries;
        final boolean connected;
        Snapshot(long generation, boolean connected, long bytes, long frames, long connectionAgeMs,
                 long writeAgeMs, long writingAgeMs, long recoveries) {
            this.generation = generation; this.connected = connected; this.bytes = bytes; this.frames = frames;
            this.connectionAgeMs = connectionAgeMs; this.writeAgeMs = writeAgeMs;
            this.writingAgeMs = writingAgeMs; this.recoveries = recoveries;
        }
    }
}
