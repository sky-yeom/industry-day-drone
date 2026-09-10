package com.msdkremote.commandserver;

import org.junit.Test;
import static org.junit.Assert.*;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;

/** No sockets or SDK singleton calls: exercise exactly the accept/close/dispatch transitions. */
public class CommandServerEpochTest {
    @Test public void lateRepliesCannotCrossCloseOrAccept() {
        CommandServer server=new CommandServer(null,1);
        long first=server.openSession();
        server.sendMessage("first",first);assertEquals(1,server.queuedMessages());
        server.closeSession(first);assertEquals(0,server.queuedMessages());
        server.sendMessage("late",first);assertEquals(0,server.queuedMessages());
        long second=server.openSession();assertTrue(second>first);
        server.sendMessage("old callback",first);assertEquals(0,server.queuedMessages());
        server.sendMessage("second",second);assertEquals(1,server.queuedMessages());
    }
    @Test public void closeInvalidatesBeforeNotifyingAndNotifiesOnce() {
        CommandServer server=new CommandServer(null,1);long epoch=server.openSession();
        AtomicInteger ends=new AtomicInteger();
        server.addSessionEndListener((owner,closed)->{
            assertEquals(epoch,closed);assertFalse(owner.isSessionActive(closed));
            assertEquals(0,owner.queuedMessages());ends.incrementAndGet();
            owner.sendMessage("cleanup callback",closed);
        });
        server.sendMessage("pending",epoch);server.closeSession(epoch);server.closeSession(epoch);
        assertEquals(1,ends.get());assertEquals(0,server.queuedMessages());
    }
    @Test public void oldReaderCannotBorrowANewEpoch() {
        CommandServer server=new CommandServer(null,1);long old=server.openSession();
        AtomicLong received=new AtomicLong(-1);
        server.addCommandHandler(new CommandHandler(){
            @Override public void onCommand(CommandServer owner,String command){fail("Epoch overload must be used");}
            @Override public void onCommand(CommandServer owner,String command,long epoch){received.set(epoch);}
        });
        server.closeSession(old);long current=server.openSession();
        server.dispatch("old reader queued command",old);assertEquals(-1,received.get());
        server.dispatch("current command",current);assertEquals(current,received.get());
    }
}
