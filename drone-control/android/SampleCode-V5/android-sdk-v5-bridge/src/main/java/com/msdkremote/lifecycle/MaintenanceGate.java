package com.msdkremote.lifecycle;

import java.util.HashMap;
import java.util.Map;

/** Process-wide intent arbitration. Acquire this gate before any owner state lock.
 * Permits survive caller deadlines. No SDK call or external callback runs under this lock. */
public final class MaintenanceGate {
    public static final MaintenanceGate SHARED = new MaintenanceGate();
    private long serial, generation;
    private long maintenance;
    private boolean unsafeIntent, armed, enabling, explicitVsDisabled, releasePending;
    private final Map<Long,String> mutations=new HashMap<>();
    public synchronized void sourceChanged(long value) {
        generation=value; maintenance=0; serial++; explicitVsDisabled=false;
        // Unsafe intent and pending SDK actions belong to the process, not a connection.
    }
    public synchronized void controlState(boolean a, boolean e, Boolean vsEnabled) {
        armed=a; enabling=e; explicitVsDisabled=Boolean.FALSE.equals(vsEnabled);
    }
    public synchronized void beginRelease(){invalidateMaintenance();releasePending=true;}
    public synchronized void finishRelease(){releasePending=false;}
    public synchronized boolean allowsControl() { return maintenance==0; }
    public synchronized long beginMutation(String identity, boolean unsafe, boolean landing) {
        if(landing) invalidateMaintenance();
        else if(maintenance!=0) return 0;
        int general=0;
        for(String id:mutations.values()) {
            if(id.equals(identity))return 0;
            if(!id.equals("LANDING"))general++;
        }
        if(!landing && general>=2)return 0;
        long permit=++serial; mutations.put(permit,landing?"LANDING":identity);
        if(unsafe)unsafeIntent=true;
        return permit;
    }
    public synchronized void uncertainMutation(long permit){if(mutations.containsKey(permit))unsafeIntent=true;}
    public synchronized void completeMutation(long permit) { mutations.remove(permit); }
    public synchronized long reserveMaintenance(long expectedGeneration) {
        if(generation!=expectedGeneration || maintenance!=0 || armed || enabling
                || !explicitVsDisabled || releasePending || unsafeIntent || !mutations.isEmpty())return 0;
        maintenance=++serial; return maintenance;
    }
    public synchronized boolean valid(long ticket,long expectedGeneration) {
        return ticket!=0 && maintenance==ticket && generation==expectedGeneration
                && !armed && !enabling && explicitVsDisabled && !releasePending && !unsafeIntent && mutations.isEmpty();
    }
    public synchronized void release(long ticket) { if(maintenance==ticket)maintenance=0; }
    public synchronized void invalidateMaintenance() { maintenance=0; serial++; }
    public synchronized boolean maintenanceActive() { return maintenance!=0; }
    public synchronized boolean unsafeIntent() { return unsafeIntent; }
    /** Explicit operator acknowledgement plus independently verified ground proof only. */
    public synchronized boolean acknowledgeGround(long expectedGeneration, boolean freshProof) {
        if(!freshProof || generation!=expectedGeneration || armed || enabling || releasePending || !explicitVsDisabled
                || !mutations.isEmpty())return false;
        unsafeIntent=false; return true;
    }
}
