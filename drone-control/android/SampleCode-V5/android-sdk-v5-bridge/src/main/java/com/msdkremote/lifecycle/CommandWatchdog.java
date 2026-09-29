package com.msdkremote.lifecycle;

/** Time of accepted motion/explicit zero only. STATUS and heartbeat do not renew this lease. */
public final class CommandWatchdog {
    public enum Decision { SEND, ZERO, RELEASE }
    // ZERO still brakes 300ms after the last command. RELEASE was 1000ms, which a
    // single venue Wi-Fi stall of up to 800ms plus the PC's 300ms keep-alive gap
    // exceeded, handing authority to the RC mid-route. The aircraft is already
    // braked by then, so a longer release only decides whether the run survives.
    public static final long ZERO_MS=300, RELEASE_MS=3000;
    private long acceptedMs=-1;
    public synchronized void accepted(long now) { acceptedMs=now; }
    public synchronized Decision at(long now) {
        long age=acceptedMs<0?Long.MAX_VALUE:now-acceptedMs;
        if(age<0 || age>=RELEASE_MS)return Decision.RELEASE;
        return age>=ZERO_MS?Decision.ZERO:Decision.SEND;
    }
}
