package com.msdkremote.lifecycle;

import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.function.LongSupplier;

/** Two direct, sequential false/false rounds. Timeouts do not cancel physical SDK requests. */
public final class GroundProof {
    public interface Callback { void done(Boolean value, String error); }
    public interface Reader { void get(Object key, Callback callback); }
    public interface Result { void done(boolean grounded,long completedMs,String reason); }
    private final ScheduledExecutorService worker;
    private final LongSupplier clock,generation;
    private final Reader reader;
    private final Object flyingKey,motorsKey;
    private final PendingSdkReads ledger;
    private boolean active;
    public GroundProof(ScheduledExecutorService worker,LongSupplier clock,LongSupplier generation,
                       Reader reader,Object flying,Object motors,PendingSdkReads ledger) {
        this.worker=worker;this.clock=clock;this.generation=generation;this.reader=reader;
        flyingKey=flying;motorsKey=motors;this.ledger=ledger;
    }
    public synchronized boolean start(long expected,Result result) {
        if(active)return false;
        active=true;ledger.setProofActive(true);
        Attempt a=new Attempt(expected,clock.getAsLong(),result);
        worker.schedule(()->finish(a,false,"GROUND_PROOF_DEADLINE"),3000,TimeUnit.MILLISECONDS);
        worker.execute(()->read(a,0));return true;
    }
    private static final class Attempt {
        final long generation,start;final Result result;final AtomicBoolean open=new AtomicBoolean(true);
        Attempt(long g,long s,Result r){generation=g;start=s;result=r;}
    }
    private void read(Attempt a,int step) {
        if(!a.open.get())return;
        if(a.generation!=generation.getAsLong()){finish(a,false,"SOURCE_CHANGED");return;}
        long now=clock.getAsLong();if(now-a.start>=3000){finish(a,false,"GROUND_PROOF_DEADLINE");return;}
        Object key=step%2==0?flyingKey:motorsKey;
        PendingSdkReads.Read slot=ledger.reserve(key,PendingSdkReads.Owner.GROUND_PROOF,a.generation,now,true);
        if(slot==null){finish(a,false,"SDK_READ_CAPACITY_EXHAUSTED");return;}
        AtomicBoolean reply=new AtomicBoolean(true);
        worker.schedule(()->{if(reply.compareAndSet(true,false)){ledger.deadline(slot.id);finish(a,false,"SDK_DEADLINE_EXCEEDED");}},1000,TimeUnit.MILLISECONDS);
        try {
            reader.get(key,(value,error)->{
                if(!ledger.complete(slot.id))return;
                worker.execute(()->{
                    if(!reply.compareAndSet(true,false)||!a.open.get())return;
                    if(a.generation!=generation.getAsLong()){finish(a,false,"SOURCE_CHANGED");return;}
                    if(error!=null||!Boolean.FALSE.equals(value)){finish(a,false,error==null?"NOT_CONFIRMED_GROUND":error);return;}
                    if(step==3){finish(a,true,"GROUND_CONFIRMED");return;}
                    if(step==1)worker.schedule(()->read(a,2),200,TimeUnit.MILLISECONDS);
                    else read(a,step+1);
                });
            });
        }catch(RuntimeException error){ledger.uncertain(slot.id);if(reply.compareAndSet(true,false))finish(a,false,"SDK_SUBMISSION_UNCERTAIN");}
    }
    private void finish(Attempt a,boolean ok,String reason) {
        if(!a.open.compareAndSet(true,false))return;
        synchronized(this){active=false;ledger.setProofActive(false);}
        a.result.done(ok,clock.getAsLong(),reason);
    }
}
