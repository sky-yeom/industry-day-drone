package com.msdkremote.lifecycle;

import org.junit.Test;
import static org.junit.Assert.*;
import java.util.*;
import java.util.concurrent.*;
import java.util.function.Consumer;

/** Deterministic fake scheduler and adapters: no Android, SDK, network, or real-time sleeps. */
public class SafetyLifecycleTest {
    static final class Scheduler extends AbstractExecutorService implements ScheduledExecutorService {
        long now, serial; boolean stopped;
        final PriorityQueue<Task<?>> queue = new PriorityQueue<>();
        final class Task<V> extends FutureTask<V> implements RunnableScheduledFuture<V> {
            final long due, order;
            Task(Callable<V> callable,long delay){super(callable);due=now+delay;order=serial++;}
            public long getDelay(TimeUnit unit){return unit.convert(due-now,TimeUnit.MILLISECONDS);}
            public int compareTo(Delayed other){Task<?> t=(Task<?>)other;int c=Long.compare(due,t.due);return c==0?Long.compare(order,t.order):c;}
            public boolean isPeriodic(){return false;}
        }
        void until(long time){while(!queue.isEmpty()&&queue.peek().due<=time){Task<?> task=queue.remove();now=task.due;task.run();}now=time;}
        public void execute(Runnable r){schedule(r,0,TimeUnit.MILLISECONDS);}
        public ScheduledFuture<?> schedule(Runnable r,long d,TimeUnit unit){return schedule(Executors.callable(r),d,unit);}
        public <V> ScheduledFuture<V> schedule(Callable<V> r,long d,TimeUnit unit){Task<V> t=new Task<>(r,unit.toMillis(d));queue.add(t);return t;}
        public ScheduledFuture<?> scheduleAtFixedRate(Runnable r,long a,long b,TimeUnit u){throw new UnsupportedOperationException();}
        public ScheduledFuture<?> scheduleWithFixedDelay(Runnable r,long a,long b,TimeUnit u){throw new UnsupportedOperationException();}
        public void shutdown(){stopped=true;}
        public List<Runnable> shutdownNow(){stopped=true;queue.clear();return Collections.emptyList();}
        public boolean isShutdown(){return stopped;}
        public boolean isTerminated(){return stopped&&queue.isEmpty();}
        public boolean awaitTermination(long t,TimeUnit u){return isTerminated();}
    }
    final Scheduler scheduler = new Scheduler();
    long generation=1;
    final PendingSdkReads ledger = new PendingSdkReads();
    final List<String> proofResults = new ArrayList<>();
    GroundProof proof(GroundProof.Reader reader){return new GroundProof(scheduler,()->scheduler.now,()->generation,reader,"flying","motors",ledger);}
    GroundProof.Result result(){return (ok,at,reason)->proofResults.add(ok+":"+reason);}

    @Test public void deadlinesAndUncertainSubmissionRetainPhysicalCapacityUntilExactlyOneCallback() {
        for(int i=0;i<11;i++)assertNotNull(ledger.reserve("ordinary"+i,PendingSdkReads.Owner.TELEMETRY,1,0,false));
        long id=ledger.ids()[0];ledger.deadline(id);ledger.uncertain(id);
        assertNull(ledger.reserve("overflow",PendingSdkReads.Owner.TELEMETRY,2,9999,false));
        assertNotNull(ledger.reserve("flying",PendingSdkReads.Owner.GROUND_PROOF,2,9999,true));
        assertEquals(12,ledger.size());assertTrue(ledger.complete(id));assertFalse(ledger.complete(id));assertEquals(11,ledger.size());
        assertNotNull(ledger.reserve("replacement",PendingSdkReads.Owner.TELEMETRY,2,10000,false));
    }
    @Test public void queryBudgetAndProofReservationAreIndependent() {
        for(int i=0;i<4;i++)assertNotNull(ledger.reserve("query"+i,PendingSdkReads.Owner.QUERY,1,0,false));
        assertNull(ledger.reserve("query5",PendingSdkReads.Owner.QUERY,1,0,false));
        ledger.setProofActive(true);
        assertNull(ledger.reserve("flying",PendingSdkReads.Owner.TELEMETRY,1,0,true));
        assertNotNull(ledger.reserve("flying",PendingSdkReads.Owner.GROUND_PROOF,1,0,true));
        assertNull(ledger.reserve("motors",PendingSdkReads.Owner.GROUND_PROOF,1,0,true));
    }
    @Test public void perActualKeyCapacitySurvivesGenerationChanges() {
        assertNotNull(ledger.reserve("same",PendingSdkReads.Owner.TELEMETRY,1,0,false));
        assertNotNull(ledger.reserve(new String("same"),PendingSdkReads.Owner.QUERY,2,0,false));
        assertNull(ledger.reserve("same",PendingSdkReads.Owner.TELEMETRY,3,0,false));
    }
    @Test public void watchdogInspectionDoesNotRenewAcceptedMotionOrZeroLease() {
        CommandWatchdog watchdog=new CommandWatchdog();assertEquals(CommandWatchdog.Decision.RELEASE,watchdog.at(0));
        watchdog.accepted(0);
        for(int now=0;now<300;now++)assertEquals(CommandWatchdog.Decision.SEND,watchdog.at(now));
        assertEquals(CommandWatchdog.Decision.ZERO,watchdog.at(300));assertEquals(CommandWatchdog.Decision.RELEASE,watchdog.at(1000));
        watchdog.accepted(1100);assertEquals(CommandWatchdog.Decision.SEND,watchdog.at(1100));
        assertEquals(CommandWatchdog.Decision.RELEASE,watchdog.at(1099));
    }
    @Test public void unsafeIntentAndPendingMutationSurviveDisconnect() {
        MaintenanceGate gate=new MaintenanceGate();gate.sourceChanged(1);gate.controlState(false,false,false);
        long action=gate.beginMutation("TAKEOFF",true,false);assertNotEquals(0,action);
        gate.sourceChanged(2);gate.controlState(false,false,false);
        assertEquals(0,gate.reserveMaintenance(2));assertFalse(gate.acknowledgeGround(2,true));
        gate.completeMutation(action);assertEquals(0,gate.reserveMaintenance(2));
        assertFalse(gate.acknowledgeGround(2,false));assertTrue(gate.acknowledgeGround(2,true));
        assertNotEquals(0,gate.reserveMaintenance(2));
    }
    @Test public void landingAndReleaseInvalidateMaintenanceAndUnknownVsCannotReserve() {
        MaintenanceGate gate=new MaintenanceGate();gate.sourceChanged(1);gate.controlState(false,false,null);
        assertEquals(0,gate.reserveMaintenance(1));gate.controlState(false,false,false);
        long ticket=gate.reserveMaintenance(1);assertNotEquals(0,ticket);
        assertEquals(0,gate.beginMutation("CAMERA_WRITE",false,false));
        long landing=gate.beginMutation("LANDING",false,true);assertNotEquals(0,landing);assertFalse(gate.valid(ticket,1));
        gate.completeMutation(landing);ticket=gate.reserveMaintenance(1);assertNotEquals(0,ticket);
        gate.beginRelease();assertFalse(gate.valid(ticket,1));assertEquals(0,gate.reserveMaintenance(1));
        gate.finishRelease();assertNotEquals(0,gate.reserveMaintenance(1));
    }
    @Test public void proofRequiresTwoSequentialFalseFalseRounds() {
        List<Object> keys=new ArrayList<>();
        GroundProof p=proof((key,cb)->{keys.add(key);cb.done(false,null);});
        assertTrue(p.start(1,result()));assertFalse(p.start(1,result()));scheduler.until(199);
        assertEquals(Arrays.asList("flying","motors"),keys);assertTrue(proofResults.isEmpty());
        scheduler.until(200);assertEquals(Arrays.asList("flying","motors","flying","motors"),keys);
        assertEquals(Arrays.asList("true:GROUND_CONFIRMED"),proofResults);assertEquals(0,ledger.size());
        scheduler.until(4000);assertEquals(1,proofResults.size());
    }
    @Test public void trueUnknownAndReadErrorNeverProveGround() {
        for(int mode=0;mode<3;mode++) {
            final int choice=mode;proofResults.clear();
            GroundProof p=proof((key,cb)->cb.done(choice==0?true:null,choice==2?"GET_FAILED":null));
            p.start(generation,result());scheduler.until(scheduler.now);
            assertEquals(1,proofResults.size());assertTrue(proofResults.get(0).startsWith("false:"));
        }
    }
    @Test public void proofTimeoutRetainsLedgerSlotAndLateCallbackCannotCompleteAttemptAgain() {
        List<GroundProof.Callback> callbacks=new ArrayList<>();GroundProof p=proof((key,cb)->callbacks.add(cb));
        p.start(1,result());scheduler.until(1000);
        assertEquals(Arrays.asList("false:SDK_DEADLINE_EXCEEDED"),proofResults);assertEquals(1,ledger.size());
        callbacks.get(0).done(false,null);scheduler.until(1000);
        assertEquals(0,ledger.size());assertEquals(1,proofResults.size());assertEquals(1,callbacks.size());
    }
    @Test public void sourceChangeRejectsAnOtherwiseSuccessfulGroundCallback() {
        List<GroundProof.Callback> callbacks=new ArrayList<>();GroundProof p=proof((key,cb)->callbacks.add(cb));
        p.start(1,result());scheduler.until(0);generation=2;callbacks.get(0).done(false,null);scheduler.until(0);
        assertEquals(Arrays.asList("false:SOURCE_CHANGED"),proofResults);assertEquals(0,ledger.size());
    }
    @Test public void submissionThrowIsUncertainAndNeverFreesAReadSlot() {
        GroundProof p=proof((key,cb)->{throw new IllegalStateException("submission failed");});
        p.start(1,result());scheduler.until(0);
        assertEquals(Arrays.asList("false:SDK_SUBMISSION_UNCERTAIN"),proofResults);assertEquals(1,ledger.size());
        scheduler.until(4000);assertEquals(1,proofResults.size());assertEquals(1,ledger.size());
    }
    FcHealthTracker handlerFault() {
        FcHealthTracker health=new FcHealthTracker();
        for(int i=0;i<3;i++)for(int k=0;k<2;k++)health.failed(FcHealthTracker.CORE[k],scheduler.now,"REQUEST_HANDLER_NOT_FOUND");
        assertEquals("HANDLER_FAULT",health.state(scheduler.now));return health;
    }
    @Test public void listenActivityCannotMaskMissingGetHandlerAndResetInvalidatesFreshness() {
        FcHealthTracker health=handlerFault();for(String key:FcHealthTracker.CORE)health.listen(key,0,true);
        assertEquals("HANDLER_FAULT",health.state(0));
        for(String key:FcHealthTracker.CORE)health.success(key,0,true,200);
        assertEquals("HEALTHY",health.state(1000));assertEquals("DEGRADED",health.state(1001));
        health.reset(2);assertEquals("WARMING",health.state(1001));assertEquals(1,health.successCount(FcHealthTracker.CORE[0]));
    }
    @Test public void defaultOffRecoveryHasNoReadOrRebindEffects() {
        int[] reads={0},binds={0};MaintenanceGate gate=new MaintenanceGate();
        GroundProof p=proof((key,cb)->{reads[0]++;cb.done(false,null);});
        FcRecoveryCoordinator recovery=new FcRecoveryCoordinator(false,gate,p,handlerFault(),scheduler,()->scheduler.now,()->generation,()->binds[0]++);
        recovery.tick();scheduler.until(10000);assertEquals("DISABLED",recovery.state());assertEquals(0,reads[0]);assertEquals(0,binds[0]);
    }
    @Test public void invalidatedMaintenanceCannotRebindEvenWithFourFalseProofReads() {
        int[] binds={0};MaintenanceGate gate=new MaintenanceGate();gate.sourceChanged(1);gate.controlState(false,false,false);
        GroundProof p=proof((key,cb)->cb.done(false,null));
        FcRecoveryCoordinator recovery=new FcRecoveryCoordinator(true,gate,p,handlerFault(),scheduler,()->scheduler.now,()->generation,()->binds[0]++);
        recovery.tick();scheduler.until(0);gate.beginRelease();scheduler.until(200);
        assertEquals("WAIT_OPERATOR",recovery.state());assertEquals(0,binds[0]);assertFalse(gate.maintenanceActive());
    }
    static class PerceptionAdapter implements PerceptionBinding.Adapter {
        Consumer<Map<String,Object>> info;Consumer<PerceptionBinding.Range> range;
        int addInfo,addRange,removeInfo,removeRange;boolean inline,addThrows,removeThrows;
        Runnable duringInfo,duringRemove;
        public void addInfo(Consumer<Map<String,Object>> cb){info=cb;addInfo++;if(inline)cb.accept(Collections.singletonMap("oa_horizontal_enabled",false));if(duringInfo!=null)duringInfo.run();}
        public void addRange(Consumer<PerceptionBinding.Range> cb){range=cb;addRange++;if(inline)cb.accept(new PerceptionBinding.Range(45,new int[]{1000,60000},-1,-1));if(addThrows)throw new IllegalStateException("add failed after callback");}
        public void removeInfo(Consumer<Map<String,Object>> cb){removeInfo++;if(duringRemove!=null)duringRemove.run();if(removeThrows)throw new IllegalStateException("remove failed");}
        public void removeRange(Consumer<PerceptionBinding.Range> cb){removeRange++;if(removeThrows)throw new IllegalStateException("remove failed");}
    }
    @SuppressWarnings("unchecked") Map<String,Object> diag(PerceptionBinding p){return (Map<String,Object>)p.snapshot().get("oa_diagnostics");}
    @Test public void perceptionPublishesInlineCallbacksOnlyAfterBothAddsSucceed() {
        PerceptionBinding p=new PerceptionBinding(()->scheduler.now,()->generation,"process");PerceptionAdapter a=new PerceptionAdapter();a.inline=true;
        p.ensure(a);assertEquals("BOUND",diag(p).get("listener_state"));assertEquals(false,p.snapshot().get("oa_horizontal_enabled"));
        assertEquals("HAS_REPORTED_RANGE",diag(p).get("range_observation_state"));
        scheduler.now=20000;p.ensure(a);assertEquals(1,a.addInfo);assertEquals(1,a.addRange);
        a.range.accept(new PerceptionBinding.Range(45,new int[]{1000,60000},-1,-1));
        assertEquals(0L,diag(p).get("last_callback_age_ms"));assertEquals(20000L,diag(p).get("last_value_change_age_ms"));
    }
    @Test public void addThenThrowDiscardsBufferedDataAndCleanupFailureBlocksRetry() {
        PerceptionBinding p=new PerceptionBinding(()->scheduler.now,()->generation,"process");PerceptionAdapter a=new PerceptionAdapter();a.inline=true;a.addThrows=true;a.removeThrows=true;
        p.ensure(a);assertEquals("CLEANUP_FAILED",diag(p).get("listener_state"));assertEquals(1,a.removeInfo);assertEquals(1,a.removeRange);
        assertEquals("NEVER_RECEIVED",diag(p).get("range_observation_state"));assertNull(p.snapshot().get("oa_horizontal_enabled"));
        scheduler.now=60000;p.ensure(a);assertEquals(1,a.addInfo);
        a.range.accept(new PerceptionBinding.Range(45,new int[]{500},0,0));assertEquals("NEVER_RECEIVED",diag(p).get("range_observation_state"));
    }
    @Test public void oldRegistrationCannotRemoveOrRegisterTheReplacementListeners() {
        PerceptionBinding p=new PerceptionBinding(()->scheduler.now,()->generation,"process");
        PerceptionAdapter old=new PerceptionAdapter(),replacement=new PerceptionAdapter();replacement.inline=true;
        old.duringInfo=()->{p.stop();generation++;p.ensure(replacement);};
        p.ensure(old);
        assertEquals(0,replacement.addInfo);assertEquals(0,old.addRange);
        assertEquals(1,old.removeInfo);assertEquals(1,old.removeRange);
        p.ensure(replacement);
        assertEquals("BOUND",diag(p).get("listener_state"));assertEquals(0,replacement.removeInfo);assertEquals(0,replacement.removeRange);
        assertEquals(0,old.addRange);assertEquals(false,p.snapshot().get("oa_horizontal_enabled"));
    }
    @Test public void stopDuringCleanupCannotReenterRemoveOrInstallBeforeCleanupFinishes() {
        PerceptionBinding p=new PerceptionBinding(()->scheduler.now,()->generation,"process");
        PerceptionAdapter old=new PerceptionAdapter(),replacement=new PerceptionAdapter();
        p.ensure(old);old.duringRemove=()->{p.stop();p.ensure(replacement);};
        p.stop();assertEquals(1,old.removeInfo);assertEquals(1,old.removeRange);assertEquals(0,replacement.addInfo);
        assertEquals("UNBOUND",diag(p).get("listener_state"));p.ensure(replacement);
        assertEquals("BOUND",diag(p).get("listener_state"));assertEquals(1,replacement.addInfo);assertEquals(0,replacement.removeInfo);
    }
}
