package com.msdkremote.livevideo;

import android.os.SystemClock;
import android.util.Log;
import androidx.annotation.NonNull;
import java.util.IdentityHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ScheduledThreadPoolExecutor;
import java.util.concurrent.TimeUnit;
import java.util.function.Consumer;
import dji.sdk.keyvalue.value.common.ComponentIndexType;
import dji.v5.manager.datacenter.MediaDataCenter;
import dji.v5.manager.interfaces.ICameraStreamManager;
import org.json.JSONObject;
import org.json.JSONException;

/** All SDK binding effects belong to video-binding; STATUS reads immutable snapshots. */
public final class AvailableCameraListener {
    private static final String TAG = "AvailableCamera";
    private static final String PROCESS_SESSION_ID = UUID.randomUUID().toString();
    private final ScheduledThreadPoolExecutor worker = new ScheduledThreadPoolExecutor(1, runnable -> {
        Thread thread = new Thread(runnable, "video-binding");
        thread.setDaemon(true);
        return thread;
    });
    private volatile FrameBuffer buffer;
    private SdkManager currentAdapter;
    private String lastState;
    private final StreamBindingController<ComponentIndexType, Frame> controller;

    public AvailableCameraListener() {
        worker.setRemoveOnCancelPolicy(true);
        controller = new StreamBindingController<>(worker, SystemClock::elapsedRealtime,
                this::currentManager,
                list -> list.contains(ComponentIndexType.LEFT_OR_MAIN)
                        ? ComponentIndexType.LEFT_OR_MAIN : list.get(0),
                new StreamBindingController.FrameSink<Frame>() {
                    @Override public void beginGeneration(long generation) {
                        FrameBuffer frames = buffer;
                        if (frames != null) frames.beginCameraGeneration(generation);
                    }
                    @Override public boolean add(Frame frame, long generation) {
                        FrameBuffer frames = buffer;
                        return frames != null && frames.addFrame(frame, generation);
                    }
                });
        worker.scheduleWithFixedDelay(() -> {
            controller.ensure();
            Map<String, Object> value = controller.snapshot();
            String state = value.get("activation_state") + ":" + value.get("camera_generation")
                    + ":" + value.get("binding_error");
            if (!state.equals(lastState)) {
                lastState = state;
                Log.i(TAG, "video state session=" + PROCESS_SESSION_ID + " " + value);
            }
        }, 1, 1, TimeUnit.SECONDS);
    }

    public void startListener(FrameBuffer frames) {
        buffer = frames;
        controller.start();
    }

    public void ensureBound() { controller.ensure(); }
    public void stopListener() { controller.stop(); }

    /** Only VideoServerManager's explicit fresh-ground policy may call this. */
    public void recoverDelivery() { controller.recoverReceiver(); }

    /** App shutdown only. Product disconnect uses restartable stopListener(). */
    public void close() {
        controller.stop();
        worker.shutdown(); // queued detach is allowed to complete
    }

    public JSONObject diagnostics() throws JSONException {
        JSONObject result = new JSONObject();
        for (Map.Entry<String, Object> entry : controller.snapshot().entrySet())
            result.put(entry.getKey(), entry.getValue() == null ? JSONObject.NULL : entry.getValue());
        return result.put("process_session_id", PROCESS_SESSION_ID).put("worker", "video-binding");
    }

    private StreamBindingController.Manager<ComponentIndexType, Frame> currentManager() {
        ICameraStreamManager manager = MediaDataCenter.getInstance().getCameraStreamManager();
        if (manager == null) { currentAdapter = null; return null; }
        if (currentAdapter == null || currentAdapter.owner != manager) currentAdapter = new SdkManager(manager);
        return currentAdapter;
    }

    /** These maps and registration calls are touched only by the serial video worker. */
    private static final class SdkManager implements StreamBindingController.Manager<ComponentIndexType, Frame> {
        final ICameraStreamManager owner;
        final Map<StreamBindingController.CameraEvents<ComponentIndexType>,
                ICameraStreamManager.AvailableCameraUpdatedListener> available = new IdentityHashMap<>();
        final Map<Consumer<Frame>, ICameraStreamManager.ReceiveStreamListener> receivers = new IdentityHashMap<>();
        SdkManager(ICameraStreamManager owner) { this.owner = owner; }
        @Override public Object identity() { return owner; }
        @Override public void addAvailable(StreamBindingController.CameraEvents<ComponentIndexType> events) {
            ICameraStreamManager.AvailableCameraUpdatedListener listener =
                    new ICameraStreamManager.AvailableCameraUpdatedListener() {
                @Override public void onAvailableCameraUpdated(@NonNull List<ComponentIndexType> cameras) {
                    events.available(cameras);
                }
                @Override public void onCameraStreamEnableUpdate(@NonNull Map<ComponentIndexType, Boolean> states) {
                    events.enabled(states);
                }
            };
            available.put(events, listener);
            owner.addAvailableCameraUpdatedListener(listener);
        }
        @Override public void removeAvailable(StreamBindingController.CameraEvents<ComponentIndexType> events) {
            ICameraStreamManager.AvailableCameraUpdatedListener listener = available.remove(events);
            if (listener != null) owner.removeAvailableCameraUpdatedListener(listener);
        }
        @Override public void addReceiver(ComponentIndexType camera, Consumer<Frame> consumer) {
            ICameraStreamManager.ReceiveStreamListener listener = (data, offset, length, info) -> {
                // SDK owns/reuses data. Copy here, never defer the SDK byte array to a worker.
                if (offset < 0 || length < 0 || length > 8_000_000 || offset > data.length - length) return;
                consumer.accept(new Frame(data, offset, length, info));
            };
            receivers.put(consumer, listener);
            owner.addReceiveStreamListener(camera, listener);
        }
        @Override public void removeReceiver(Consumer<Frame> consumer) {
            ICameraStreamManager.ReceiveStreamListener listener = receivers.remove(consumer);
            if (listener != null) owner.removeReceiveStreamListener(listener);
        }
        @Override public void enable(ComponentIndexType camera) { owner.enableStream(camera, true); }
    }
}
