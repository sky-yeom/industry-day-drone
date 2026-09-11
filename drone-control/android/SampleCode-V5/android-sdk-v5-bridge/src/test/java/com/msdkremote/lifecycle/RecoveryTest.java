package com.msdkremote.lifecycle;

import org.junit.Test;
import static org.junit.Assert.*;

public class RecoveryTest {
    @Test public void missingCallbackRetriesAfterDeadline() {
        PollLease p = new PollLease(2000);
        long first = p.begin(100);
        assertTrue(first > 0);
        assertEquals(0, p.begin(2099));
        assertTrue(p.begin(2100) > first);
        assertEquals(1, p.expiredCount());
    }
    @Test public void lateCallbackCannotClearNewGet() {
        PollLease p = new PollLease(2000);
        long first = p.begin(100), second = p.begin(2100);
        assertFalse(p.complete(first));
        assertEquals(0, p.begin(2200));
        assertTrue(p.complete(second));
        assertTrue(p.begin(2201) > second);
    }
    @Test public void reconnectDiscardsOldGeneration() {
        PollLease p = new PollLease(2000);
        long old = p.begin(100);
        p.invalidate();
        long next = p.begin(101);
        assertFalse(p.complete(old));
        assertTrue(p.complete(next));
    }
    @Test public void duplicateCallbackIgnored() {
        PollLease p = new PollLease(2000);
        long id = p.begin(1);
        assertTrue(p.complete(id));
        assertFalse(p.complete(id));
        assertFalse(p.complete(0));
    }
    @Test public void partialInitializationRemainsRetryable() {
        RetryableInit init = new RetryableInit();
        assertFalse(init.ensure(() -> { throw new IllegalStateException("SDK not ready"); }));
        assertFalse(init.isReady());
        assertNotNull(init.error());
        assertTrue(init.ensure(() -> {}));
        assertEquals(2, init.attempts());
        assertNull(init.error());
    }
    @Test public void successfulInitNotRepeatedEveryTick() {
        RetryableInit init = new RetryableInit();
        int[] calls = {0};
        for (int i=0; i<100; i++) assertTrue(init.ensure(() -> calls[0]++));
        assertEquals(1, calls[0]);
    }
    @Test public void failedVsInitDoesNotSkipTelemetryOrRc() {
        RetryableInit vs = new RetryableInit(), telemetry = new RetryableInit(), rc = new RetryableInit();
        vs.ensure(() -> { throw new IllegalStateException("VS unavailable"); });
        telemetry.ensure(() -> {}); rc.ensure(() -> {});
        assertFalse(vs.isReady()); assertTrue(telemetry.isReady()); assertTrue(rc.isReady());
    }
    @Test public void repeatedDisconnectReconnectDropsOldCallbacks() {
        RetryableInit init = new RetryableInit();
        PollLease p = new PollLease(2000);
        int[] bindings = {0};
        for (int i=0; i<100; i++) {
            assertTrue(init.ensure(() -> bindings[0]++));
            long request = p.begin(i);
            init.invalidate(); p.invalidate();
            assertFalse(p.complete(request));
        }
        assertEquals(100, bindings[0]);
        assertFalse(init.isReady());
    }
}
