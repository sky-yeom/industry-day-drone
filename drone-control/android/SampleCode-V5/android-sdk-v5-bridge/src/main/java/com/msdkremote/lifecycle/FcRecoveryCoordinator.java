package com.msdkremote.lifecycle;

import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.function.LongSupplier;

/** Only the telemetry owner can be rebound. Automatic recovery is OFF in the production bridge. */
public final class FcRecoveryCoordinator {
    private final boolean enabled;
    private final MaintenanceGate gate;
    private final GroundProof proof;
    private final FcHealthTracker health;
    private final ScheduledExecutorService worker;
    private final LongSupplier clock,generation;
    private final Runnable rebindTelemetry;
    private volatile String state,reason;
    private long ticket,source,lastAttempt=-30000,healthySince=-1,verifyStart,verifyHealthy=-1;
    private boolean attempted;
    private long[] baseline;
    public FcRecoveryCoordinator(boolean enabled,MaintenanceGate gate,GroundProof proof,FcHealthTracker health,
            ScheduledExecutorService worker,LongSupplier clock,LongSupplier generation,Runnable rebindTelemetry) {
        this.enabled=enabled;this.gate=gate;this.proof=proof;this.health=health;this.worker=worker;
        this.clock=clock;this.generation=generation;this.rebindTelemetry=rebindTelemetry;
        state=enabled?"IDLE":"DISABLED";reason=enabled?"NONE":"MANUAL_SDK_MUTATION_PATHS_NOT_FULLY_GATED";
    }
    public String state(){return state;} public String reason(){return reason;}
    public void tick() {
        if(!enabled)return;
        long now=clock.getAsLong();String current=health.state(now);
        if("HEALTHY".equals(current)) {
            if(healthySince<0)healthySince=now;
            if(now-healthySince>=10000 && !state.equals("VERIFYING"))attempted=false;
        } else healthySince=-1;
        if(!"HANDLER_FAULT".equals(current)||attempted||now-lastAttempt<30000
                ||state.equals("PROVING")||state.equals("VERIFYING"))return;
        source=generation.getAsLong();ticket=gate.reserveMaintenance(source);
        if(ticket==0){state="WAIT_OPERATOR";reason="GROUND_OR_CONTROL_STATE_NOT_SAFE";return;}
        attempted=true;lastAttempt=now;state="PROVING";
        if(!proof.start(source,(ok,when,why)->{
            if(!ok){fail(why);return;}
            if(!gate.valid(ticket,source)){fail("MAINTENANCE_INVALIDATED");return;}
            if(clock.getAsLong()-when>500){fail("GROUND_PROOF_STALE");return;}
            try {
                // Revalidate immediately before owner-specific SDK cleanup/install.
                if(!gate.valid(ticket,source)){fail("MAINTENANCE_INVALIDATED");return;}
                rebindTelemetry.run();
                baseline=new long[FcHealthTracker.CORE.length];
                for(int i=0;i<baseline.length;i++)baseline[i]=health.successCount(FcHealthTracker.CORE[i]);
                verifyStart=clock.getAsLong();verifyHealthy=-1;state="VERIFYING";
                worker.schedule(this::verify,200,TimeUnit.MILLISECONDS);
            }catch(RuntimeException e){fail("REBIND_FAILED");}
        }))fail("GROUND_PROOF_BUSY");
    }
    private void verify() {
        if(!state.equals("VERIFYING"))return;
        long now=clock.getAsLong();
        if(!gate.valid(ticket,source)||now-verifyStart>=5000){fail("VERIFY_FAILED_OR_SOURCE_CHANGED");return;}
        if(!proof.start(source,(grounded,when,why)->{
            if(!grounded){fail(why);return;}
            if(!gate.valid(ticket,source)){fail("MAINTENANCE_INVALIDATED");return;}
            long time=clock.getAsLong();boolean healthy="HEALTHY".equals(health.state(time));
            for(int i=0;i<baseline.length;i++)healthy &=health.successCount(FcHealthTracker.CORE[i])-baseline[i]>=2;
            if(healthy){if(verifyHealthy<0)verifyHealthy=time;}else verifyHealthy=-1;
            if(verifyHealthy>=0&&time-verifyHealthy>=2000){gate.release(ticket);state="SUCCEEDED";reason="FRESH_CORE_GETS_VERIFIED";}
            else worker.schedule(this::verify,500,TimeUnit.MILLISECONDS);
        }))fail("GROUND_PROOF_BUSY");
    }
    private void fail(String why){gate.release(ticket);state="WAIT_OPERATOR";reason=why;}
}
