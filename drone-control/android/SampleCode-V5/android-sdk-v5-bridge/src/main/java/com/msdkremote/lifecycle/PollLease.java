package com.msdkremote.lifecycle;

/** A missing SDK callback must not permanently suppress a polling key.
 * Token ownership also prevents a late callback from completing a newer GET. */
public final class PollLease {
    private final long timeoutMs;
    private long serial;
    private long active;
    private long since;
    private long expired;

    public PollLease(long timeoutMs) { this.timeoutMs = timeoutMs; }

    public synchronized long begin(long now) {
        if (active != 0 && now - since < timeoutMs) return 0;
        if (active != 0) expired++;
        active = ++serial;
        since = now;
        return active;
    }

    public synchronized boolean complete(long token) {
        if (token == 0 || token != active) return false;
        active = 0;
        return true;
    }

    public synchronized void invalidate() { active = 0; serial++; }
    public synchronized long expiredCount() { return expired; }
}
