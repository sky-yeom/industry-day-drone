package com.msdkremote.livevideo;

import org.junit.Test;
import static org.junit.Assert.*;
import static com.msdkremote.livevideo.DeliveryHealth.Recovery.*;

public class DeliveryHealthTest {
    @Test public void freshCameraDoesNotHideMissingDelivery() {
        DeliveryHealth d = new DeliveryHealth(); d.connected(0);
        assertEquals(NONE, d.claimRecovery(2999, true, 10));
        assertEquals(REBIND_CAMERA, d.claimRecovery(3000, true, 10));
    }
    @Test public void blockedSocketIsNotMistakenForCameraFailure() {
        DeliveryHealth d = new DeliveryHealth(); long t = d.connected(0);
        d.writeStarted(t, 100);
        assertEquals(CLOSE_STALLED_CLIENT, d.claimRecovery(3100, true, 10));
    }
    @Test public void noRebindInFlightArmedOrUnknownGroundState() {
        DeliveryHealth d = new DeliveryHealth(); d.connected(0);
        assertEquals(NONE, d.claimRecovery(10000, false, 10));
    }
    @Test public void noRecoveryWithoutClientOrCameraData() {
        DeliveryHealth d = new DeliveryHealth();
        assertEquals(NONE, d.claimRecovery(10000, true, 10));
        d.connected(0);
        assertEquals(NONE, d.claimRecovery(10000, true, -1));
        assertEquals(NONE, d.claimRecovery(10000, true, 3000));
    }
    @Test public void reconnectsCannotResetRecoveryCooldown() {
        DeliveryHealth d = new DeliveryHealth(); d.connected(0);
        assertEquals(REBIND_CAMERA, d.claimRecovery(3000, true, 10));
        d.connected(4000);
        assertEquals(NONE, d.claimRecovery(7000, true, 10));
        assertEquals(REBIND_CAMERA, d.claimRecovery(13000, true, 10));
    }
    @Test public void actualWritePreventsFalseStall() {
        DeliveryHealth d = new DeliveryHealth(); long t = d.connected(0);
        d.writeStarted(t, 2900); d.written(t, 2000, 3000);
        assertEquals(NONE, d.claimRecovery(5000, true, 10));
        assertEquals(2000, d.snapshot(5000).bytes);
        assertEquals(1, d.snapshot(5000).frames);
        assertEquals(-1, d.snapshot(5000).writingAgeMs);
    }
    @Test public void oldWriterCannotUpdateOrCloseReplacementSession() {
        DeliveryHealth d = new DeliveryHealth(); long old = d.connected(0);
        long current = d.connected(100);
        d.writeStarted(old, 200); d.written(old, 999, 300); d.disconnected(old);
        DeliveryHealth.Snapshot s = d.snapshot(400);
        assertTrue(s.connected); assertEquals(current, s.generation); assertEquals(0, s.bytes);
        d.written(current, 123, 500); assertEquals(123, d.snapshot(600).bytes);
    }
}
