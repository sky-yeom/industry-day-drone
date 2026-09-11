package com.msdkremote.lifecycle;

import java.util.LinkedHashMap;
import java.util.Map;

/** Per-key evidence. LISTEN activity cannot hide a broken GET handler. */
public final class FcHealthTracker {
    public static final String[] CORE={"AircraftVelocity","AircraftAttitude","UltrasonicHeight","IsFlying","FlightMode"};
    private static final class Key {
        long listen=-1, get=-1, started=-1, completed=-1, errorAt=-1, due;
        long issued,success,failure,empty,timeout;
        int failures,handlers; String error,rawError,source;
        long failureSince=-1;
    }
    private final Map<String,Key> keys=new LinkedHashMap<>();
    private long generation;
    private Boolean connected;
    public FcHealthTracker() { reset(0); }
    public synchronized void reset(long next) {
        generation=next; connected=null;
        for(String s:CORE)keys.computeIfAbsent(s,k->new Key());
        keys.computeIfAbsent("AreMotorsOn",k->new Key()); keys.computeIfAbsent("Connection",k->new Key());
        for(Key k:keys.values()) { k.listen=k.get=k.started=k.completed=k.errorAt=-1;
            k.failures=k.handlers=0; k.due=0; k.error=null; k.rawError=null;k.failureSince=-1;k.source=null; }
    }
    private Key key(String id){return keys.computeIfAbsent(id,k->new Key());}
    public synchronized void connection(Boolean c) { connected=c; }
    public synchronized void listen(String id,long now,boolean nonnull) {
        Key k=key(id); if(nonnull){k.listen=now;k.source="LISTEN";}else k.listen=-1;
    }
    public synchronized boolean due(String id,long now){return now>=key(id).due;}
    public synchronized void issued(String id,long now){Key k=key(id); k.started=now;k.issued++;}
    public synchronized void success(String id,long now,boolean nonnull,long interval) {
        Key k=key(id); k.completed=now;
        if(!nonnull){k.empty++; failed(id,now,"EMPTY_VALUE");return;}
        k.get=now;k.source="GET";k.success++;k.failures=k.handlers=0;k.error=null;k.rawError=null;k.failureSince=-1;k.due=now+interval;
    }
    public synchronized void failed(String id,long now,String code) {
        failed(id,now,code,code);
    }
    public synchronized void failed(String id,long now,String code,String raw) {
        Key k=key(id);k.completed=now;k.errorAt=now;k.error=code;k.failure++;k.failures++;
        k.rawError=raw;if(k.failureSince<0)k.failureSince=now;
        if("REQUEST_HANDLER_NOT_FOUND".equals(code))k.handlers++; else k.handlers=0;
        k.due=now+(k.failures<3?200:k.failures==3?1000:k.failures==4?2000:5000);
    }
    public synchronized void timeout(String id,long now){key(id).timeout++;failed(id,now,"SDK_DEADLINE_EXCEEDED");}
    public synchronized String state(long now) {
        if(Boolean.FALSE.equals(connected))return "DISCONNECTED";
        boolean all=true,seen=false,degraded=false;int broken=0;
        for(String id:CORE){Key k=key(id);boolean fresh=k.get>=0&&now-k.get<=1000;
            all &=fresh;seen|=k.issued>0;degraded|=k.failures>=3 || (k.get>=0&&!fresh) || (k.get<0&&k.started>=0&&now-k.started>1000);
            if(k.handlers>=3&&!fresh)broken++;}
        if(broken>=2)return "HANDLER_FAULT";
        if(all)return "HEALTHY";
        return degraded?"DEGRADED": "WARMING";
    }
    public synchronized Map<String,Object> snapshot(long now) {
        Map<String,Object> out=new LinkedHashMap<>(), per=new LinkedHashMap<>();
        out.put("state",state(now));out.put("generation",generation);
        for(Map.Entry<String,Key> e:keys.entrySet()) {
            Key k=e.getValue(); Map<String,Object> v=new LinkedHashMap<>();
            v.put("listener_age_ms",k.listen<0?null:Math.max(0,now-k.listen));
            v.put("get_success_age_ms",k.get<0?null:Math.max(0,now-k.get));
            v.put("consecutive_failures",k.failures);v.put("consecutive_handler_failures",k.handlers);
            v.put("last_error_code",k.error);v.put("source",k.source);
            v.put("last_error_raw",k.rawError);v.put("last_error_age_ms",k.errorAt<0?null:Math.max(0,now-k.errorAt));
            v.put("get_started_age_ms",k.started<0?null:Math.max(0,now-k.started));
            v.put("get_completed_age_ms",k.completed<0?null:Math.max(0,now-k.completed));
            v.put("failure_streak_age_ms",k.failureSince<0?null:Math.max(0,now-k.failureSince));
            v.put("issued_total",k.issued);v.put("success_total",k.success);v.put("failure_total",k.failure);
            v.put("empty_total",k.empty);v.put("timeout_total",k.timeout);v.put("next_due_ms",k.due);
            per.put(e.getKey(),v);
        }
        out.put("keys",per);return out;
    }
    public synchronized long successCount(String id){return key(id).success;}
}
