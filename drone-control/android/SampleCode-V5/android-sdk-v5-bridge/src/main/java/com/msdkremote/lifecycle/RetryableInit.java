package com.msdkremote.lifecycle;

/** Only a successfully completed initialization is remembered as ready. */
public final class RetryableInit {
    private boolean ready;
    private int attempts;
    private String error;

    public synchronized boolean ensure(Runnable initialize) {
        if (ready) return true;
        attempts++;
        try {
            initialize.run();
            ready = true;
            error = null;
        } catch (RuntimeException failure) {
            error = failure.getClass().getSimpleName() + ": " + failure.getMessage();
        }
        return ready;
    }

    public synchronized void invalidate() { ready = false; }
    public synchronized boolean isReady() { return ready; }
    public synchronized int attempts() { return attempts; }
    public synchronized String error() { return error; }
}
