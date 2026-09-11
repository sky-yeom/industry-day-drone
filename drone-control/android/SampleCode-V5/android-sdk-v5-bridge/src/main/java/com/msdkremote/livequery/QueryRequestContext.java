package com.msdkremote.livequery;

import com.msdkremote.commandserver.CommandServer;
import com.msdkremote.PcBridge;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicLong;

/** Logical reply lifetime, deliberately independent from physical SDK completion. */
final class QueryRequestContext {
    private static final AtomicLong IDS=new AtomicLong();
    final long id=IDS.incrementAndGet(),epoch,generation;
    final String process=PcBridge.processStartId(),method,module,key;
    final CommandServer server;
    final AtomicBoolean open=new AtomicBoolean(true);
    QueryRequestContext(CommandServer server,long epoch,String method,KeyItem<?,?> item){this.server=server;this.epoch=epoch;
        generation=PcBridge.connectionGeneration();this.method=method;module=item.getModuleName();key=item.getKeyName();}
    boolean current(){return generation==PcBridge.connectionGeneration()&&server.isSessionActive(epoch);}
    String prefix(){return module+" "+key+" ";}
}
