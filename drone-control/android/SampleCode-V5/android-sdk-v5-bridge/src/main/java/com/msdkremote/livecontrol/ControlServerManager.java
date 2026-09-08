package com.msdkremote.livecontrol;

import android.util.Log;

import com.msdkremote.commandserver.CommandServer;
import com.msdkremote.commandserver.CommandServerStateListener;
import com.msdkremote.livecontrol.advanced.AdvancedControlCommandHandler;
import com.msdkremote.livecontrol.advanced.StickControlManager;

import java.net.InetAddress;

public class ControlServerManager
{
    // Logging TAG
    private final String TAG = this.getClass().getSimpleName();

    // Command Server instance
    private CommandServer commandServer = null;

    // Active command handler, so client connect/disconnect can reset its session
    private AdvancedControlCommandHandler commandHandler = null;

    // State listener - limiting to one listener
    private final Object StateListenerLock = new Object();
    private CommandServerStateListener stateListener = null;


    /* ------------------- Singleton ------------------- */

    // Control Server Manager instance - singleton
    private static ControlServerManager instance = null;

    private ControlServerManager() {
        Log.i(TAG, "ControlServer was created for the first time!");
    }

    /**
     * Get instance of ControlServerManager
     *
     * @return single instance of ControlServerManager
     */
    public static ControlServerManager getInstance()
    {
        if (instance == null)
            instance = new ControlServerManager();

        return instance;
    }


    /* ------------------- Server Control ------------------- */

    /**
     * Initiate ControlServer on specific port.
     *
     * @param port port number used by server.
     */
    public synchronized void startServer(int port, String armToken)
    {
        // Check if server already running
        if (this.commandServer != null) {
            Log.w(TAG, "Control Server already running.");
            return;
        }

        // Opens new server
        Log.i(TAG, "Starting new Control Server, port : " + port + ".");
        // Keep the wire-level command trace visible in logcat.  The original
        // two-argument constructor deliberately suppresses every accept/read/
        // write message, which made a transport timeout indistinguishable from
        // a DJI control rejection during field tests.
        this.commandServer = new CommandServer(
                new commandServerStateListener(), port, "PcControlServer");

        this.commandHandler = new AdvancedControlCommandHandler(
                StickControlManager.getInstance(), armToken);
        commandServer.addCommandHandler(this.commandHandler);
        commandServer.startServer();
    }


    /**
     * Stops the ControlServer.
     *
     * @throws InterruptedException if current thread was interrupted mid waiting.
     */
    public synchronized void killServer() throws InterruptedException
    {
        // Check if server already terminated
        if (this.commandServer == null) {
            Log.w(TAG, "Control Server already closed.");
            return;
        }

        // Stops control server
        Log.i(TAG, "Stop Control Server.");
        this.commandServer.removeAllCommandHandlers();
        this.commandServer.stopServer();
        this.commandServer = null;
        this.commandHandler = null;
    }

    public void emergencyStop()
    {
        StickControlManager.getInstance().emergencyStop();
    }


    /* ------------------- State Listener ------------------- */

    /**
     * Sets a state listener for the ControlServer.
     * <br>
     * Note: There can be only one state listener over the program.
     * <br>
     * Note: Setting state listener when one already registered will remove
     *       the previous state listener.
     *
     * @param listener the state listener to set.
     */
    public synchronized void setStateListener(CommandServerStateListener listener)
    {
        synchronized (StateListenerLock) {
            this.stateListener = listener;
        }
    }

    /**
     * Remove the state listener over the ControlServer.
     */
    public synchronized void resetStateListener()
    {
        synchronized (StateListenerLock) {
            this.stateListener = null;
        }
    }

    // Inner class to give the ability to add listener mid running.
    private class commandServerStateListener implements CommandServerStateListener
    {
        @Override
        public void onServerRunning() {
            synchronized (ControlServerManager.this.StateListenerLock) {
                if (ControlServerManager.this.stateListener != null) {
                    ControlServerManager.this.stateListener.onServerRunning();
                }
            }
        }

        @Override
        public void onServerClosed() {
            synchronized (ControlServerManager.this.StateListenerLock) {
                if (ControlServerManager.this.stateListener != null) {
                    ControlServerManager.this.stateListener.onServerClosed();
                }
            }
        }

        @Override
        public void onServerException(Exception e) {
            synchronized (ControlServerManager.this.StateListenerLock) {
                if (ControlServerManager.this.stateListener != null) {
                    ControlServerManager.this.stateListener.onServerException(e);
                }
            }
        }

        @Override
        public void onClientConnected(InetAddress address) {
            AdvancedControlCommandHandler handler = ControlServerManager.this.commandHandler;
            if (handler != null) {
                handler.resetSession();
            }
            synchronized (ControlServerManager.this.StateListenerLock) {
                if (ControlServerManager.this.stateListener != null) {
                    ControlServerManager.this.stateListener.onClientConnected(address);
                }
            }
        }

        @Override
        public void onClientDisconnected() {
            // A read-only status client disconnecting while already disarmed
            // must not generate a misleading DJI disable failure. An armed or
            // in-flight arm handshake still releases immediately.
            if (StickControlManager.getInstance().isArmedOrEnabling()) {
                StickControlManager.getInstance().emergencyStop();
            }
            AdvancedControlCommandHandler handler = ControlServerManager.this.commandHandler;
            if (handler != null) {
                handler.resetSession();
            }
            synchronized (ControlServerManager.this.StateListenerLock) {
                if (ControlServerManager.this.stateListener != null) {
                    ControlServerManager.this.stateListener.onClientDisconnected();
                }
            }
        }
    }
}
