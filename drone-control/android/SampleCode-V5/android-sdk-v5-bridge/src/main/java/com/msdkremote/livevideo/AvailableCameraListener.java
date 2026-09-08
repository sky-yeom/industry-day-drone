package com.msdkremote.livevideo;

import android.os.SystemClock;
import android.util.Log;
import androidx.annotation.NonNull;
import java.util.List;
import java.util.Map;
import dji.sdk.keyvalue.value.common.ComponentIndexType;
import dji.sdk.keyvalue.key.CameraKey;
import dji.sdk.keyvalue.key.KeyTools;
import dji.v5.manager.KeyManager;
import dji.v5.manager.datacenter.MediaDataCenter;
import dji.v5.manager.interfaces.ICameraStreamManager;
import org.json.JSONObject;
import org.json.JSONException;

/** Reacquires the current SDK manager after a reconnect, with bounded backoff.
 * Old-generation camera callbacks cannot refill a newly reset stream. */
public final class AvailableCameraListener {
    private static final String TAG = "AvailableCamera";
    private ICameraStreamManager manager;
    private ICameraStreamManager.AvailableCameraUpdatedListener availableListener;
    private ICameraStreamManager.ReceiveStreamListener receiver;
    private ComponentIndexType camera;
    private FrameBuffer buffer;
    private long generation;
    private long nextRetryMs;
    private long retryDelayMs = 3000;
    private long rebinds;
    private String lastError;
    private Boolean enabled;
    private boolean running;

    public synchronized void startListener(FrameBuffer frames) {
        buffer = frames;
        running = true;
        ensureBound();
    }

    public synchronized void ensureBound() {
        if (!running || buffer == null) return;
        long now = SystemClock.elapsedRealtime();
        ICameraStreamManager current = MediaDataCenter.getInstance().getCameraStreamManager();
        long age = buffer.cameraAgeMs();
        if (current == manager && receiver != null && age >= 0 && age < 3000) {
            retryDelayMs = 3000;
            lastError = null;
            return;
        }
        if (now < nextRetryMs) return;
        nextRetryMs = now + retryDelayMs;
        retryDelayMs = Math.min(30000, retryDelayMs * 2);
        try {
            // Re-add available-camera listener too: it may itself be detached.
            detach();
            manager = current;
            final long ticket = ++generation;
            availableListener = new ICameraStreamManager.AvailableCameraUpdatedListener() {
                @Override public void onAvailableCameraUpdated(@NonNull List<ComponentIndexType> list) {
                    synchronized (AvailableCameraListener.this) {
                        if (!running || generation != ticket) return;
                        if (list.isEmpty()) return; // wait for camera/product reconnect
                        ComponentIndexType selected = list.contains(ComponentIndexType.LEFT_OR_MAIN)
                                ? ComponentIndexType.LEFT_OR_MAIN : list.get(0);
                        if (receiver == null || camera != selected) bind(selected, ticket);
                    }
                }
                @Override public void onCameraStreamEnableUpdate(@NonNull Map<ComponentIndexType, Boolean> states) {
                    synchronized (AvailableCameraListener.this) {
                        if (generation == ticket) enabled = states.get(ComponentIndexType.LEFT_OR_MAIN);
                    }
                }
            };
            manager.addAvailableCameraUpdatedListener(availableListener);
            // A cached connection is sufficient to attempt a read-only listener
            // registration; it is NOT used to authorize flight.
            Boolean connected = KeyManager.getInstance().getValue(KeyTools.createKey(CameraKey.KeyConnection));
            if (receiver == null && Boolean.TRUE.equals(connected))
                bind(ComponentIndexType.LEFT_OR_MAIN, ticket);
            if (camera != null && Boolean.FALSE.equals(enabled))
                manager.enableStream(camera, true); // main image stream only, not vision sensors
        } catch (RuntimeException e) {
            lastError = e.toString();
            Log.e(TAG, "camera binding failed; will retry", e);
            detach();
        }
    }

    private void bind(ComponentIndexType selected, long ticket) {
        if (receiver != null) manager.removeReceiveStreamListener(receiver);
        camera = selected;
        buffer.nextKeyFrame();
        receiver = (data, offset, length, info) -> {
            synchronized (AvailableCameraListener.this) {
                if (!running || generation != ticket) return;
                buffer.addFrame(new Frame(data, offset, length, info));
            }
        };
        manager.addReceiveStreamListener(selected, receiver);
        rebinds++;
        Log.i(TAG, "camera attached generation=" + ticket + " camera=" + selected);
    }

    private void detach() {
        generation++;
        if (manager != null) {
            try { if (receiver != null) manager.removeReceiveStreamListener(receiver); }
            catch (RuntimeException e) { Log.w(TAG, "receiver detach", e); }
            try { if (availableListener != null) manager.removeAvailableCameraUpdatedListener(availableListener); }
            catch (RuntimeException e) { Log.w(TAG, "camera list detach", e); }
        }
        receiver = null; availableListener = null; camera = null; manager = null; enabled = null;
        if (buffer != null) buffer.nextKeyFrame();
    }

    public synchronized void stopListener() {
        running = false;
        detach();
        nextRetryMs = 0;
        retryDelayMs = 3000;
    }

    /** Explicit bounded ground recovery when camera input is fresh but no bytes leave. */
    public synchronized void recoverDelivery() {
        if (!running) return;
        Log.w(TAG, "Camera input alive but video delivery stalled; reattaching our listener only");
        detach();
        nextRetryMs = 0;
        retryDelayMs = 3000;
        ensureBound();
    }

    public synchronized JSONObject diagnostics() throws JSONException {
        return new JSONObject().put("binding_running", running).put("binding_generation", generation)
                .put("binding_attempts", rebinds).put("camera_selected", camera == null ? JSONObject.NULL : camera.name())
                .put("stream_enabled", enabled == null ? JSONObject.NULL : enabled)
                .put("binding_error", lastError == null ? JSONObject.NULL : lastError)
                .put("retry_in_ms", Math.max(0, nextRetryMs - SystemClock.elapsedRealtime()));
    }
}
