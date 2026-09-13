package com.msdkremote.lifecycle;

import org.junit.Test;
import static org.junit.Assert.*;

public class DiagnosticEvidenceTest {
    @Test public void persistentEvidenceOmitsRawErrorsButKeepsFailureTransition() {
        FcHealthTracker health=new FcHealthTracker();
        for(String key:FcHealthTracker.CORE) {
            health.issued(key,10);
            health.success(key,20,true,200);
        }
        assertEquals("HEALTHY",health.state(100));
        for(String key:FcHealthTracker.CORE) {
            for(int n=0;n<3;n++)
                health.failed(key,1200+n,"REQUEST_HANDLER_NOT_FOUND","private-token-must-not-persist");
        }
        assertEquals("HANDLER_FAULT",health.state(1300));
        String diagnostic=health.diagnosticSnapshot(1300).toString();
        assertFalse(diagnostic.contains("private-token"));
        assertFalse(diagnostic.contains("last_error_raw"));
        assertTrue(diagnostic.contains("REQUEST_HANDLER_NOT_FOUND"));
        assertTrue(diagnostic.contains("get_success_age_ms=1280"));
        assertTrue(health.snapshot(1300).toString().contains("private-token-must-not-persist"));
        for(String key:FcHealthTracker.CORE)health.success(key,1400,true,200);
        assertEquals("HEALTHY",health.diagnosticSnapshot(1400).get("state"));
    }
}
