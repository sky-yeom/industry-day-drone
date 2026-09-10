package com.msdkremote.livevideo;

import org.junit.Test;
import static org.junit.Assert.*;

public class StreamActivationPolicyTest {
    private long now;
    private StreamActivationPolicy policy() {
        StreamActivationPolicy p = new StreamActivationPolicy(() -> now);
        p.reset(true, true, true);
        return p;
    }
    @Test public void missingReportAttemptsOnceThenExponentialBackoff() {
        StreamActivationPolicy p = policy();
        long[] times = {0, 3000, 9000, 21000, 45000, 75000};
        for (long at : times) {
            if (at > 0) {
                now = at - 1;
                assertEquals(StreamActivationPolicy.Effect.NONE, p.reconcile());
            }
            now = at;
            assertEquals(StreamActivationPolicy.Effect.ENABLE, p.reconcile());
            assertEquals(StreamActivationPolicy.Effect.NONE, p.reconcile());
        }
        assertEquals(6, p.attempts());
    }
    @Test public void trueReportAndEnableReturnNeverCountAsStreaming() {
        StreamActivationPolicy p = policy();
        p.reconcile();
        assertEquals(StreamActivationPolicy.State.WAIT_ENABLE_REPORT, p.state());
        p.report(true);
        assertEquals(StreamActivationPolicy.State.WAIT_RAW, p.state());
        now = 3000;
        assertEquals(StreamActivationPolicy.State.STALLED, p.state());
        assertEquals(StreamActivationPolicy.Effect.NONE, p.reconcile());
        p.raw(now);
        assertEquals(StreamActivationPolicy.State.STREAMING, p.state());
    }
    @Test public void falseReportsCannotBypassBackoff() {
        StreamActivationPolicy p = policy();
        p.report(false); p.reconcile();
        for (now = 1; now < 3000; now++) {
            p.report(false);
            assertEquals(StreamActivationPolicy.Effect.NONE, p.reconcile());
        }
        assertEquals(StreamActivationPolicy.Effect.ENABLE, p.reconcile());
    }
    @Test public void rawSuccessResetsBackoffForNextFailure() {
        StreamActivationPolicy p = policy();
        p.reconcile(); now = 3000; p.reconcile(); now = 9000; p.reconcile();
        now = 10000; p.raw(now); p.report(false);
        now = 12000;
        assertEquals(StreamActivationPolicy.Effect.ENABLE, p.reconcile());
        assertEquals(15000, p.nextActivationAt());
    }
    @Test public void resetCannotReusePreviousGenerationRaw() {
        StreamActivationPolicy p = policy(); p.raw(0);
        assertEquals(StreamActivationPolicy.State.STREAMING, p.state());
        p.reset(true, true, false);
        assertEquals(StreamActivationPolicy.State.WAIT_CAMERA, p.state());
        assertEquals(-1, p.lastRawAt());
        p.reset(false, false, false);
        assertEquals(StreamActivationPolicy.State.STOPPED, p.state());
        assertEquals(StreamActivationPolicy.Effect.NONE, p.reconcile());
    }
}
