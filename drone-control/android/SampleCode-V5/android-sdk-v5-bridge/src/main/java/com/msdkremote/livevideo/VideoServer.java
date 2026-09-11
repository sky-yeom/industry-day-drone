package com.msdkremote.livevideo;

import android.util.Log;
import android.os.SystemClock;
import org.json.JSONObject;
import org.json.JSONException;

import java.io.IOException;
import java.io.OutputStream;
import java.net.ServerSocket;
import java.net.Socket;
import java.net.SocketException;

/**
 * Streams encoded camera frames to the newest TCP client.
 *
 * Accepting connections and waiting for camera frames must happen on separate
 * threads. Camera/battery reconnects can leave FrameBuffer empty; the old
 * implementation then blocked in getFrame() and never accepted the PC's new
 * socket. A new client now atomically replaces and closes the previous one.
 */
class VideoServer {
    private static final String TAG = "VideoServer";

    private final Object stateLock = new Object();
    private volatile boolean running = false;
    private Thread acceptThread = null;
    private Thread writerThread = null;
    private ServerSocket serverSocket = null;
    private Socket clientSocket = null;
    private final DeliveryHealth delivery = new DeliveryHealth();

    public synchronized void startServer(int port, FrameBuffer buffer) {
        if (acceptThread != null) {
            return;
        }
        running = true;
        acceptThread = new Thread(() -> acceptLoop(port, buffer), "video-accept");
        acceptThread.start();
    }

    private void acceptLoop(int port, FrameBuffer buffer) {
        Log.i(TAG, "Starting server port - " + port);
        try {
            ServerSocket created = new ServerSocket(port);
            synchronized (stateLock) {
                if (!running) {
                    closeQuietly(created);
                    return;
                }
                serverSocket = created;
            }
            Log.i(TAG, "Server socket ready");
            while (running && !Thread.currentThread().isInterrupted()) {
                Log.i(TAG, "Waiting for new client");
                Socket accepted = created.accept();
                accepted.setTcpNoDelay(true);
                if (!running) {
                    closeQuietly(accepted);
                    break;
                }
                replaceClient(accepted, buffer);
            }
        } catch (SocketException error) {
            if (running) {
                Log.e(TAG, "Video accept socket failed", error);
            }
        } catch (IOException error) {
            if (running) {
                Log.e(TAG, "Video server failed", error);
            }
        } finally {
            synchronized (stateLock) {
                closeQuietly(serverSocket);
                serverSocket = null;
            }
            Log.i(TAG, "Stopped server port - " + port);
        }
    }

    private void replaceClient(Socket accepted, FrameBuffer buffer) {
        final Socket previousSocket;
        final Thread previousWriter;
        final Thread nextWriter;
        final long readerTicket = buffer.openReader();
        synchronized (stateLock) {
            final long deliveryTicket = delivery.connected(SystemClock.elapsedRealtime());
            nextWriter = new Thread(() -> writeClient(accepted, buffer, readerTicket, deliveryTicket),
                    "video-writer-" + deliveryTicket);
            previousSocket = clientSocket;
            previousWriter = writerThread;
            clientSocket = accepted;
            writerThread = nextWriter;
        }
        // Closing unblocks write(); interrupting unblocks getFrame().
        closeQuietly(previousSocket);
        if (previousWriter != null) {
            previousWriter.interrupt();
            if (previousWriter != Thread.currentThread()) {
                try {
                    previousWriter.join(500);
                } catch (InterruptedException error) {
                    Thread.currentThread().interrupt();
                    closeQuietly(accepted);
                    return;
                }
            }
        }
        nextWriter.start();
        Log.i(TAG, "New video client connected; previous client replaced");
    }

    private void writeClient(Socket socket, FrameBuffer buffer, long readerTicket, long deliveryTicket) {
        try {
            OutputStream output = socket.getOutputStream();
            while (running && isCurrentClient(socket)
                    && !Thread.currentThread().isInterrupted()) {
                Frame frame = buffer.getFrame(readerTicket);
                if (!isCurrentClient(socket)) {
                    break;
                }
                delivery.writeStarted(deliveryTicket, SystemClock.elapsedRealtime());
                output.write(frame.getData());
                output.flush();
                delivery.written(deliveryTicket, frame.getSize(), SystemClock.elapsedRealtime());
            }
        } catch (InterruptedException ignored) {
            Thread.currentThread().interrupt();
        } catch (IOException error) {
            if (running && isCurrentClient(socket)) {
                Log.i(TAG, "Video client disconnected: " + error);
            }
        } finally {
            closeQuietly(socket);
            delivery.disconnected(deliveryTicket);
            synchronized (stateLock) {
                if (clientSocket == socket) {
                    clientSocket = null;
                }
                if (writerThread == Thread.currentThread()) {
                    writerThread = null;
                }
            }
        }
    }

    private boolean isCurrentClient(Socket socket) {
        synchronized (stateLock) {
            return running && clientSocket == socket;
        }
    }

    public JSONObject diagnostics() throws JSONException {
        DeliveryHealth.Snapshot s = delivery.snapshot(SystemClock.elapsedRealtime());
        synchronized (stateLock) {
            return new JSONObject().put("listening", running && serverSocket != null)
                    .put("client_connected", s.connected).put("client_generation", s.generation)
                    .put("client_written_bytes", s.bytes).put("client_written_frames", s.frames)
                    .put("client_age_ms", s.connectionAgeMs).put("socket_write_age_ms", s.writeAgeMs)
                    .put("socket_writing_age_ms", s.writingAgeMs).put("ground_recoveries", s.recoveries)
                    .put("recovery_reason", s.recoveryReason).put("recovery_blocked", s.recoveryBlocked)
                    .put("recoveries_without_progress", s.recoveriesWithoutProgress)
                    .put("android_elapsed_ms", SystemClock.elapsedRealtime())
                    .put("write_semantics", "accepted_by_local_tcp_not_pc_decode_ack");
        }
    }

    public boolean recoverGroundDelivery(boolean enabled, boolean groundDisarmed,
                                         long cameraAgeMs, boolean waitingKeyframe) {
        synchronized (stateLock) {
            DeliveryHealth.Recovery action = delivery.claimRecovery(
                    SystemClock.elapsedRealtime(), enabled, groundDisarmed, cameraAgeMs, waitingKeyframe);
            if (action == DeliveryHealth.Recovery.CLOSE_STALLED_CLIENT) {
                Log.w(TAG, "Ground video write stalled; closing only the video client");
                closeQuietly(clientSocket);
                if (writerThread != null) writerThread.interrupt();
            }
            return action == DeliveryHealth.Recovery.REBIND_CAMERA;
        }
    }

    public synchronized void stopServer() throws InterruptedException {
        if (acceptThread == null) {
            return;
        }
        final Thread accept;
        final Thread writer;
        synchronized (stateLock) {
            running = false;
            accept = acceptThread;
            writer = writerThread;
            closeQuietly(serverSocket);
            closeQuietly(clientSocket);
            if (writer != null) {
                writer.interrupt();
            }
            accept.interrupt();
        }
        accept.join();
        if (writer != null && writer != Thread.currentThread()) {
            writer.join();
        }
        synchronized (stateLock) {
            acceptThread = null;
            writerThread = null;
            serverSocket = null;
            clientSocket = null;
        }
    }

    private static void closeQuietly(ServerSocket socket) {
        if (socket != null) {
            try {
                socket.close();
            } catch (IOException ignored) {
                // Best-effort shutdown.
            }
        }
    }

    private static void closeQuietly(Socket socket) {
        if (socket != null) {
            try {
                socket.close();
            } catch (IOException ignored) {
                // Best-effort shutdown.
            }
        }
    }
}
