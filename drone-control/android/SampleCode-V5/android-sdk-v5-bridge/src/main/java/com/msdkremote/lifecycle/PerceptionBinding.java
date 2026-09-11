package com.msdkremote.lifecycle;

import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.function.Consumer;
import java.util.function.LongSupplier;

/** Transactional pair of owner-scoped ON_CHANGE listeners. No age-based re-registration. */
public final class PerceptionBinding {
    public interface Adapter {
        default void prepare(Consumer<Map<String,Object>> info,Consumer<Range> range){}
        void addInfo(Consumer<Map<String,Object>> callback);
        void addRange(Consumer<Range> callback);
        void removeInfo(Consumer<Map<String,Object>> callback);
        void removeRange(Consumer<Range> callback);
    }
    public static final class Range {
        public final int interval,up,down; public final int[] horizontal;
        public Range(int interval,int[] horizontal,int up,int down){this.interval=interval;this.horizontal=horizontal.clone();this.up=up;this.down=down;}
        @Override public boolean equals(Object o){if(!(o instanceof Range))return false;Range r=(Range)o;return interval==r.interval&&up==r.up&&down==r.down&&Arrays.equals(horizontal,r.horizontal);}
        @Override public int hashCode(){return Arrays.hashCode(horizontal);}
        public String state(){if(horizontal.length==0&&up<0&&down<0)return "EMPTY";
            if(usable(up)||usable(down))return "HAS_REPORTED_RANGE";for(int v:horizontal)if(usable(v))return "HAS_REPORTED_RANGE";return "NO_USABLE_RANGE";}
        private boolean usable(int v){return v>0&&v<60000;}
    }
    private final LongSupplier clock,connection;
    private final String process;
    private String state="UNBOUND",registrationError,cleanupError;
    private long generation,connectionGeneration,callbackMs=-1,valueMs=-1,infoMs=-1,sequence,nextRetry;
    private int attempts;
    private Range range,bufferedRange;
    private Map<String,Object> info,bufferedInfo;
    private long bufferedRangeMs,bufferedInfoMs,bufferedValueMs,bufferedCount;
    private Adapter adapter;
    private boolean registrationRunning,boundaryRequested,cleanupRunning;
    private Consumer<Range> rangeListener;
    private Consumer<Map<String,Object>> infoListener;
    public PerceptionBinding(LongSupplier clock,LongSupplier connection,String process){this.clock=clock;this.connection=connection;this.process=process;}
    public void ensure(Adapter candidate) {
        final long token;
        final Consumer<Map<String,Object>> attemptInfo;
        final Consumer<Range> attemptRange;
        synchronized(this) {
            if(state.equals("BOUND")||state.equals("BINDING")||state.equals("CLEANUP_FAILED")||state.equals("CLEANING"))return;
            if(attempts>=4||clock.getAsLong()<nextRetry)return;
            token=++generation;connectionGeneration=connection.getAsLong();attempts++;state="BINDING";
            adapter=candidate;bufferedRange=null;bufferedInfo=null;bufferedCount=0;
            infoListener=value->acceptInfo(token,value);rangeListener=value->acceptRange(token,value);
            attemptInfo=infoListener;attemptRange=rangeListener;registrationRunning=true;boundaryRequested=false;
        }
        try {
            candidate.prepare(attemptInfo,attemptRange);
            candidate.addInfo(attemptInfo);
            synchronized(this){if(generation!=token)throw new IllegalStateException("SOURCE_CHANGED");}
            candidate.addRange(attemptRange);
            synchronized(this) {
                if(generation!=token||connectionGeneration!=connection.getAsLong())throw new IllegalStateException("SOURCE_CHANGED");
                state="BOUND";registrationError=null;registrationRunning=false;
                if(bufferedInfo!=null){info=bufferedInfo;infoMs=bufferedInfoMs;}
                if(bufferedRange!=null){range=bufferedRange;callbackMs=bufferedRangeMs;valueMs=bufferedValueMs;sequence+=bufferedCount;}
                bufferedInfo=null;bufferedRange=null;
            }
        }catch(RuntimeException error) {
            synchronized(this){registrationError=error.toString();registrationRunning=false;}
            cleanup(false);
        }
    }
    private synchronized void acceptInfo(long token,Map<String,Object> value) {
        if(token!=generation||connectionGeneration!=connection.getAsLong()||value==null)return;
        Map<String,Object> copy=new LinkedHashMap<>(value);long now=clock.getAsLong();
        if(state.equals("BINDING")){bufferedInfo=copy;bufferedInfoMs=now;}
        else if(state.equals("BOUND")){info=copy;infoMs=now;}
    }
    private synchronized void acceptRange(long token,Range value) {
        if(token!=generation||connectionGeneration!=connection.getAsLong()||value==null)return;
        Range copy=new Range(value.interval,value.horizontal,value.up,value.down);long now=clock.getAsLong();
        if(state.equals("BINDING")){if(!copy.equals(bufferedRange))bufferedValueMs=now;bufferedRange=copy;bufferedRangeMs=now;bufferedCount++;}
        else if(state.equals("BOUND"))publishRange(copy,now);
    }
    private void publishRange(Range value,long now){sequence++;callbackMs=now;if(!value.equals(range))valueMs=now;range=value;}
    public void stop(){
        synchronized(this){
            if(state.equals("CLEANING")){boundaryRequested=true;return;}
            if(registrationRunning){
                generation++;state="CLEANING";boundaryRequested=true;
                range=null;info=null;bufferedRange=null;bufferedInfo=null;callbackMs=valueMs=infoMs=-1;
                return; // The registering owner removes its captured pair after SDK entry returns.
            }
        }
        cleanup(true);
    }
    private void cleanup(boolean boundary) {
        Adapter owner;Consumer<Range> ranges;Consumer<Map<String,Object>> infos;
        synchronized(this){if(cleanupRunning){boundaryRequested|=boundary;return;}cleanupRunning=true;
            boundary=boundary||boundaryRequested;boundaryRequested=false;generation++;state="CLEANING";range=null;info=null;bufferedInfo=null;bufferedRange=null;
            callbackMs=valueMs=infoMs=-1;owner=adapter;ranges=rangeListener;infos=infoListener;}
        String error=null;
        if(owner!=null){
            try{owner.removeInfo(infos);}catch(RuntimeException e){error="INFO: "+e;}
            try{owner.removeRange(ranges);}catch(RuntimeException e){error=(error==null?"":error+"; ")+"RANGE: "+e;}
        }
        synchronized(this){cleanupRunning=false;boundary=boundary||boundaryRequested;boundaryRequested=false;cleanupError=error;
            if(error!=null){state="CLEANUP_FAILED";return;}
            adapter=null;rangeListener=null;infoListener=null;
            if(boundary){attempts=0;nextRetry=0;state="UNBOUND";}
            else{state="RETRY_WAIT";nextRetry=clock.getAsLong()+(attempts==1?1000:attempts==2?2000:5000);}
        }
    }
    public synchronized Map<String,Object> snapshot() {
        long now=clock.getAsLong();boolean current=connectionGeneration==connection.getAsLong()&&state.equals("BOUND");
        Range r=current?range:null;Map<String,Object> values=current?info:null;
        Map<String,Object> out=new LinkedHashMap<>(),diag=new LinkedHashMap<>(),fields=new LinkedHashMap<>();
        String[] names={"oa_type","oa_horizontal_enabled","oa_upward_enabled","oa_downward_enabled","vision_positioning_enabled","oa_sensors_working"};
        for(String name:names){Object value=values==null?null:values.get(name);Map<String,Object> field=new LinkedHashMap<>();
            field.put("reported",values!=null);field.put("value",value);field.put("age_ms",values==null?null:Math.max(0,now-infoMs));fields.put(name,field);out.put(name,value);}
        out.put("oa_horizontal_angle_interval_deg",r==null?null:r.interval);
        out.put("oa_horizontal_distances_mm",r==null?new int[0]:r.horizontal.clone());
        out.put("oa_horizontal_sample_count",r==null?0:r.horizontal.length);
        out.put("oa_upward_distance_mm",r==null?null:r.up);out.put("oa_downward_distance_mm",r==null?null:r.down);
        out.put("oa_obstacle_data_age_ms",r==null?-1:Math.max(0,now-callbackMs));
        diag.put("listener_state",state);diag.put("generation",generation);diag.put("connection_generation",connectionGeneration);
        diag.put("source_process_start_id",process);diag.put("callback_sequence",sequence);diag.put("callback_semantics","ON_CHANGE");
        diag.put("periodic_heartbeat_expected",false);diag.put("source_timestamp_available",false);
        diag.put("last_callback_age_ms",r==null?null:Math.max(0,now-callbackMs));diag.put("last_value_change_age_ms",r==null?null:Math.max(0,now-valueMs));
        diag.put("info_received",values!=null);diag.put("last_info_callback_age_ms",values==null?null:Math.max(0,now-infoMs));
        diag.put("range_observation_state",r==null?"NEVER_RECEIVED":r.state());diag.put("status_fields",fields);
        diag.put("registration_error",registrationError);diag.put("cleanup_error",cleanupError);diag.put("registration_attempts",attempts);
        if(values!=null)diag.put("working_directions",values.get("working_directions"));
        out.put("oa_diagnostics",diag);return out;
    }
}
