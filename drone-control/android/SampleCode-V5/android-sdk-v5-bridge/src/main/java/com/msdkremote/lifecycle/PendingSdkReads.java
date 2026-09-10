package com.msdkremote.lifecycle;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.List;
import java.util.ArrayList;

/** Physical SDK submissions, not waiting callers. Deadlines NEVER free these slots. */
public final class PendingSdkReads {
    public enum Owner { TELEMETRY, QUERY, GROUND_PROOF }
    public static final PendingSdkReads SHARED = new PendingSdkReads();
    public static final class Read {
        public final long id, generation, submittedMs;
        public final Object key;
        public final Owner owner;
        public boolean timedOut, submissionUncertain;
        private Read(long id, Object key, Owner owner, long generation, long now) {
            this.id=id; this.key=key; this.owner=owner; this.generation=generation; submittedMs=now;
        }
    }
    private final Map<Long, Read> pending = new LinkedHashMap<>();
    private long nextId;
    private boolean proofActive;
    public synchronized void setProofActive(boolean active) { proofActive=active; }
    public synchronized Read reserve(Object actualKey, Owner owner, long generation, long now, boolean groundKey) {
        int same=0, query=0, ordinary=0, proof=0;
        for (Read r:pending.values()) {
            if (r.key.equals(actualKey)) same++;
            if (r.owner==Owner.QUERY) query++;
            if (r.owner==Owner.GROUND_PROOF) proof++; else ordinary++;
        }
        if (pending.size()>=12 || same>=2) return null;
        if (owner==Owner.GROUND_PROOF) { if (proof>=1) return null; }
        else if (ordinary>=11 || (groundKey && (proofActive || same>=1))) return null;
        if (owner==Owner.QUERY && query>=4) return null;
        Read r=new Read(++nextId, actualKey, owner, generation, now);
        pending.put(r.id,r); return r;
    }
    /** Exactly once even for old generations, duplicate callbacks, and callback-then-throw. */
    public synchronized boolean complete(long id) { return pending.remove(id)!=null; }
    public synchronized void deadline(long id) { Read r=pending.get(id); if(r!=null)r.timedOut=true; }
    public synchronized void telemetryDeadline(Object key,long now) {
        for(Read r:pending.values())if(r.owner==Owner.TELEMETRY&&r.key.equals(key)&&now-r.submittedMs>=2000)r.timedOut=true;
    }
    public synchronized void uncertain(long id) { Read r=pending.get(id); if(r!=null)r.submissionUncertain=true; }
    public synchronized int size() { return pending.size(); }
    public synchronized int count(Owner owner) { int n=0; for(Read r:pending.values())if(r.owner==owner)n++; return n; }
    public synchronized long[] ids() { long[] out=new long[pending.size()]; int i=0; for(long id:pending.keySet())out[i++]=id; return out; }
    public synchronized List<Map<String,Object>> snapshot(long now) {
        List<Map<String,Object>> out=new ArrayList<>();
        for(Read r:pending.values()){
            Map<String,Object> item=new LinkedHashMap<>();
            item.put("request_id",r.id);item.put("owner",r.owner.name());item.put("actual_key",String.valueOf(r.key));
            item.put("connection_generation",r.generation);item.put("age_ms",Math.max(0,now-r.submittedMs));
            item.put("reply_deadline_expired",r.timedOut);item.put("submission_uncertain",r.submissionUncertain);out.add(item);
        }
        return out;
    }
}
