package com.msdkremote.livequery;

import android.os.SystemClock;
import androidx.annotation.NonNull;
import com.msdkremote.PcBridge;
import com.msdkremote.commandserver.CommandServer;
import com.msdkremote.lifecycle.MaintenanceGate;
import com.msdkremote.lifecycle.PendingSdkReads;
import com.msdkremote.livecontrol.advanced.FlightCommands;
import dji.sdk.keyvalue.key.DJIKey;
import dji.sdk.keyvalue.key.FlightControllerKey;
import dji.sdk.keyvalue.value.common.EmptyMsg;
import dji.v5.common.callback.CommonCallbacks;
import dji.v5.common.error.IDJIError;
import dji.v5.manager.KeyManager;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.Set;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;

/** Query ownership is socket scoped; physical work and failed cleanups remain process scoped. */
public final class QuerySessionRegistry implements CommandServer.SessionEndListener {
    public static final QuerySessionRegistry SHARED=new QuerySessionRegistry();
    private final Set<QueryRequestContext> requests=new HashSet<>();
    private final Set<Listener> listeners=new HashSet<>();
    private final ScheduledExecutorService deadlines=Executors.newSingleThreadScheduledExecutor(r->{Thread t=new Thread(r,"query-deadlines");t.setDaemon(true);return t;});
    private static final class Listener {
        final QueryRequestContext context;final DJIKey key;final KeyManager manager;
        boolean valid=true,binding=true,cleanupFailed,buffered;Object bufferedValue;
        Listener(QueryRequestContext c,DJIKey key){context=c;this.key=key;manager=KeyManager.getInstance();}
    }
    public static void local(CommandServer server,long epoch,String module,String key,String code){
        server.sendMessage("QUERY_LOCAL_ERROR "+(module.isEmpty()?"-":module)+" "+(key.isEmpty()?"-":key)+" "+code,epoch);
    }
    private static String value(Object result){return result instanceof EmptyMsg?"success":String.valueOf(result);}
    private void finish(QueryRequestContext c,String text,boolean local){
        if(!c.open.compareAndSet(true,false))return;
        synchronized(this){requests.remove(c);}
        if(!c.server.isSessionActive(c.epoch))return;
        if(c.generation!=PcBridge.connectionGeneration()&&!"SOURCE_CHANGED".equals(text)){text="SOURCE_CHANGED";local=true;}
        if(local)local(c.server,c.epoch,c.module,c.key,text);else c.server.sendMessage(c.prefix()+text,c.epoch);
    }
    private synchronized boolean pending(CommandServer server,long epoch,String key,boolean any){
        for(QueryRequestContext c:requests)if(c.open.get()&&c.server==server&&c.epoch==epoch&&(any||c.key.equals(key)))return true;
        return false;
    }
    private synchronized Listener find(CommandServer server,long epoch,DJIKey key){
        for(Listener l:listeners)if(l.context.server==server&&l.context.epoch==epoch&&l.key.equals(key))return l;return null;
    }
    @SuppressWarnings({"unchecked","rawtypes"})
    public void execute(CommandServer server,long epoch,KeyItem item,String method,String parameter){
        if(!server.isSessionActive(epoch))return;
        DJIKey key=DJIKey.create(item.getRawKeyInfo());
        QueryRequestContext c=new QueryRequestContext(server,epoch,method,item);
        if(method.equals("UNLISTEN")){unlisten(c,key);return;}
        if(method.equals("LISTEN")){listen(c,key,item);return;}
        boolean get=method.equals("GET"),action=method.equals("ACTION"),set=method.equals("SET");
        if(!get&&!action&&!set){local(server,epoch,c.module,c.key,"INVALID_PARAMETER");return;}
        boolean landing=action&&item.getRawActionKeyInfo()==FlightControllerKey.KeyStartAutoLanding;
        if((get&&!item.getRawKeyInfo().isCanGet())||(set&&!item.getRawKeyInfo().isCanSet())
                ||(action&&(item.getRawActionKeyInfo()==null||!item.getRawKeyInfo().isCanPerformAction()))){
            server.sendMessage(c.prefix()+"Cannot command '"+method+"' on key.",epoch);return;}
        Object parameterValue=null;
        if(set||(action&&!parameter.isEmpty())){
            try{parameterValue=item.getParameter(parameter);}catch(RuntimeException error){local(server,epoch,c.module,c.key,"INVALID_PARAMETER");return;}
            if(parameterValue==null){local(server,epoch,c.module,c.key,"INVALID_PARAMETER");return;}
        }
        synchronized(this){
            if(!landing&&(pending(server,epoch,c.key,true)||find(server,epoch,key)!=null)){
                local(server,epoch,c.module,c.key,"BUSY");return;}
            requests.add(c);
        }
        final PendingSdkReads.Read read;
        final long mutation;
        if(get){
            read=PendingSdkReads.SHARED.reserve(key,PendingSdkReads.Owner.QUERY,c.generation,SystemClock.elapsedRealtime(),
                item.getRawKeyInfo()==FlightControllerKey.KeyIsFlying||item.getRawKeyInfo()==FlightControllerKey.KeyAreMotorsOn);
            mutation=0;if(read==null){finish(c,"SDK_READ_CAPACITY_EXHAUSTED",true);return;}
        }else {
            read=null;
            if(!landing&&MaintenanceGate.SHARED.maintenanceActive()){finish(c,"MAINTENANCE_IN_PROGRESS",true);return;}
            mutation=landing?0:MaintenanceGate.SHARED.beginMutation("QUERY:"+method+":"+key,true,false);
            if(!landing&&mutation==0){finish(c,"SDK_MUTATION_CAPACITY_EXHAUSTED",true);return;}
        }
        deadlines.schedule(()->{if(read!=null)PendingSdkReads.SHARED.deadline(read.id);finish(c,"SDK_DEADLINE_EXCEEDED",true);},get?2:5,TimeUnit.SECONDS);
        if(!c.current()){
            if(read!=null)PendingSdkReads.SHARED.complete(read.id);if(mutation!=0)MaintenanceGate.SHARED.completeMutation(mutation);
            finish(c,"SOURCE_CHANGED",true);return;
        }
        try {
            if(landing){FlightCommands.startLanding(c.generation,(ok,detail)->finish(c,ok?"success":detail,!ok&&detail.equals("LANDING_ALREADY_PENDING")));return;}
            CommonCallbacks.CompletionCallbackWithParam<Object> callback=new CommonCallbacks.CompletionCallbackWithParam<Object>(){
                private boolean complete(){if(read!=null)return PendingSdkReads.SHARED.complete(read.id);MaintenanceGate.SHARED.completeMutation(mutation);return true;}
                @Override public void onSuccess(Object result){if(complete())finish(c,value(result),false);}
                @Override public void onFailure(@NonNull IDJIError error){if(complete())finish(c,error.toString(),false);}
            };
            if(get)submitGet((DJIKey<Object>)key,callback);
            else if(set)KeyManager.getInstance().setValue(key,parameterValue,new CommonCallbacks.CompletionCallback(){
                @Override public void onSuccess(){callback.onSuccess(new EmptyMsg());}
                @Override public void onFailure(@NonNull IDJIError error){callback.onFailure(error);}
            });
            else if(parameter.isEmpty())KeyManager.getInstance().performAction(DJIKey.create(item.getRawActionKeyInfo()),callback);
            else KeyManager.getInstance().performAction(DJIKey.create(item.getRawActionKeyInfo()),parameterValue,callback);
        }catch(RuntimeException error){if(read!=null)PendingSdkReads.SHARED.uncertain(read.id);finish(c,"SDK_SUBMISSION_UNCERTAIN",true);}
    }
    private static <T> void submitGet(DJIKey<T> key,CommonCallbacks.CompletionCallbackWithParam<T> callback){
        KeyManager.getInstance().getValue(key,callback);
    }
    @SuppressWarnings({"unchecked","rawtypes"})
    private void listen(QueryRequestContext c,DJIKey key,KeyItem item){
        if(!item.getRawKeyInfo().isCanListen()){c.server.sendMessage(c.prefix()+"Cannot command 'LISTEN' on key.",c.epoch);return;}
        final Listener holder;
        synchronized(this){
            Listener existing=find(c.server,c.epoch,key);
            if(existing!=null){if(existing.cleanupFailed)local(c.server,c.epoch,c.module,c.key,"LISTENER_CLEANUP_FAILED");return;}
            if(pending(c.server,c.epoch,c.key,false)){local(c.server,c.epoch,c.module,c.key,"BUSY");return;}
            if(listeners.size()>=16){local(c.server,c.epoch,c.module,c.key,"LISTENER_LIMIT");return;}
            holder=new Listener(c,key);listeners.add(holder);
        }
        try {
            holder.manager.listen(key,holder,(oldValue,newValue)->{
                synchronized(QuerySessionRegistry.this){
                    if(!holder.valid||!c.current())return;
                    if(holder.binding){holder.buffered=true;holder.bufferedValue=newValue;return;}
                }
                c.server.sendMessage(c.prefix()+value(newValue),c.epoch);
            });
            Object buffered=null;boolean publish=false;
            synchronized(this){if(!holder.valid||!c.current())throw new IllegalStateException("SOURCE_CHANGED");
                holder.binding=false;publish=holder.buffered;buffered=holder.bufferedValue;}
            if(publish)c.server.sendMessage(c.prefix()+value(buffered),c.epoch);
        }catch(RuntimeException error){synchronized(this){holder.binding=false;}cleanup(holder);local(c.server,c.epoch,c.module,c.key,holder.cleanupFailed?"LISTENER_CLEANUP_FAILED":"SDK_SUBMISSION_UNCERTAIN");}
    }
    private void unlisten(QueryRequestContext c,DJIKey key){
        Listener holder=find(c.server,c.epoch,key);
        if(holder!=null)cleanup(holder);
        if(holder!=null&&holder.cleanupFailed)local(c.server,c.epoch,c.module,c.key,"LISTENER_CLEANUP_FAILED");
        else c.server.sendMessage(c.prefix()+"success",c.epoch);
    }
    private void cleanup(Listener holder){
        synchronized(this){holder.valid=false;holder.context.open.set(false);
            if(holder.binding)return; // Retain capacity until the registering SDK entry has returned.
        }
        try{holder.manager.cancelListen(holder.key,holder);synchronized(this){listeners.remove(holder);}}
        catch(RuntimeException error){synchronized(this){holder.cleanupFailed=true;}}
    }
    @Override public void onSessionEnded(CommandServer server,long epoch){
        ArrayList<QueryRequestContext> req;ArrayList<Listener> ls;
        synchronized(this){req=new ArrayList<>(requests);ls=new ArrayList<>(listeners);}
        for(QueryRequestContext c:req)if(c.server==server&&c.epoch==epoch)finish(c,"SOURCE_CHANGED",true);
        for(Listener l:ls)if(l.context.server==server&&l.context.epoch==epoch)cleanup(l);
    }
    public void onSourceChanged(){
        ArrayList<QueryRequestContext> req;ArrayList<Listener> ls;
        synchronized(this){req=new ArrayList<>(requests);ls=new ArrayList<>(listeners);}
        for(QueryRequestContext c:req)finish(c,"SOURCE_CHANGED",true);
        for(Listener l:ls)cleanup(l);
    }
}
