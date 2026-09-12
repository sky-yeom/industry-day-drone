package com.msdkremote.commandserver;

import android.util.Log;
import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import java.io.IOException;
import java.net.ServerSocket;
import java.net.Socket;
import java.util.concurrent.CopyOnWriteArraySet;

/** Every dispatch and queued reply belongs to one accepted socket epoch. */
public class CommandServer {
    public interface SessionEndListener { void onSessionEnded(CommandServer server,long epoch); }
    private final int port;
    private final String tag;
    private final CommandServerStateListener stateListener;
    private final MessageQueue queue=new MessageQueue(256);
    private final CopyOnWriteArraySet<CommandHandler> handlers=new CopyOnWriteArraySet<>();
    private final CopyOnWriteArraySet<SessionEndListener> ends=new CopyOnWriteArraySet<>();
    private volatile ServerSocket serverSocket;
    private volatile Socket clientSocket;
    private volatile Thread serverThread;
    private volatile boolean running;
    private long epoch;
    private boolean active;
    public CommandServer(@Nullable CommandServerStateListener listener,int port){this(listener,port,null);}
    public CommandServer(@Nullable CommandServerStateListener listener,int port,@Nullable String tag){stateListener=listener;this.port=port;this.tag=tag;}
    public synchronized void startServer(){if(running)return;running=true;serverThread=new Thread(this::run,"commands-"+port);serverThread.start();}
    public void stopServer() throws InterruptedException {
        Thread thread;
        synchronized(this){running=false;thread=serverThread;}
        closeSession(getConnectionEpoch());close(clientSocket);close(serverSocket);
        if(thread!=null&&thread!=Thread.currentThread()){thread.interrupt();thread.join();}
        synchronized(this){if(serverThread==thread)serverThread=null;}
    }
    private static void close(java.io.Closeable c){if(c!=null)try{c.close();}catch(IOException ignored){}}
    private void run() {
        try {
            ServerSocket listening=new ServerSocket(port);serverSocket=listening;
            if(!running){close(listening);return;}
            if(stateListener!=null)stateListener.onServerRunning();
            while(running&&!Thread.currentThread().isInterrupted()) {
                Socket socket=listening.accept();clientSocket=socket;
                if(!running){close(socket);break;}
                final long acceptedEpoch;
                acceptedEpoch=openSession();
                CommandServerWriter writer=null;
                try {
                    if(stateListener!=null)stateListener.onClientConnected(socket.getInetAddress());
                    writer=new CommandServerWriter(socket.getOutputStream(),queue);
                    CommandServerReader reader=new CommandServerReader(socket.getInputStream(),
                        command->dispatch(command,acceptedEpoch));
                    reader.joinServer();
                } finally {
                    closeSession(acceptedEpoch);close(socket);
                    if(writer!=null)writer.stopServer();
                    if(clientSocket==socket)clientSocket=null;
                }
            }
        } catch(InterruptedException e){Thread.currentThread().interrupt();}
        catch(Exception error){if(running&&stateListener!=null)stateListener.onServerException(error);}
        finally {
            closeSession(getConnectionEpoch());close(clientSocket);close(serverSocket);running=false;
            if(stateListener!=null)stateListener.onServerClosed();
        }
    }
    long openSession(){synchronized(queue){long accepted=++epoch;active=true;queue.clear();return accepted;}}
    int queuedMessages(){return queue.getSize();}
    void dispatch(String command,long acceptedEpoch) {
        if(!isSessionActive(acceptedEpoch))return;
        for(CommandHandler handler:handlers){
            if(!isSessionActive(acceptedEpoch))return;
            try{handler.onCommand(this,command,acceptedEpoch);}catch(RuntimeException error){if(tag!=null)Log.e(tag,"Command dispatch failed",error);}
        }
    }
    public long getConnectionEpoch(){synchronized(queue){return epoch;}}
    public boolean isSessionActive(long expected){synchronized(queue){return active&&epoch==expected;}}
    public void closeSession(long expected) {
        synchronized(queue){if(!active||epoch!=expected)return;active=false;epoch++;queue.clear();}
        // Never call SDK owners under the queue monitor; exactly one close notification.
        for(SessionEndListener listener:ends)try{listener.onSessionEnded(this,expected);}catch(RuntimeException error){if(tag!=null)Log.w(tag,"Session cleanup failed",error);}
        if(stateListener!=null)try{stateListener.onClientDisconnected();}
        catch(RuntimeException error){if(tag!=null)Log.w(tag,"Disconnect notification failed",error);}
    }
    public void sendMessage(@NonNull String message){sendMessage(message,getConnectionEpoch());}
    public void sendMessage(@NonNull String message,long expected){synchronized(queue){if(active&&epoch==expected)queue.addMessage(message);}}
    public void addSessionEndListener(SessionEndListener listener){ends.add(listener);}
    public void removeSessionEndListener(SessionEndListener listener){ends.remove(listener);}
    public void addCommandHandler(@NonNull CommandHandler handler){handlers.add(handler);}
    public boolean removeCommandHandler(@NonNull CommandHandler handler){return handlers.remove(handler);}
    public void removeAllCommandHandlers(){handlers.clear();}
}
