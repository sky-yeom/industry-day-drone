package com.msdkremote;

import android.util.Log;
import com.msdkremote.lifecycle.RetryableInit;
import com.msdkremote.livecontrol.ControlServerManager;
import com.msdkremote.livecontrol.advanced.ObstacleAvoidanceController;
import com.msdkremote.livecontrol.advanced.RcOverrideMonitor;
import com.msdkremote.livecontrol.advanced.StickControlManager;
import com.msdkremote.livecontrol.advanced.TelemetryProvider;
import com.msdkremote.livequery.QueryServerManager;
import com.msdkremote.livevideo.VideoServerManager;
import org.json.JSONObject;
import org.json.JSONException;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;

/** TCP server lifetime differs from SDK binding lifetime.
 * Recovery only repairs subscriptions. NEVER arms, resumes flight, resets
 * the SDK, changes sensors or sends movement commands. */
public final class PcBridge {
    private static final String TAG = "PcBridge";
    public static final int CONTROL_PORT = 9998, VIDEO_PORT = 9999, QUERY_PORT = 9997;
    private static final ScheduledExecutorService worker =
            Executors.newSingleThreadScheduledExecutor(r -> {
                Thread t = new Thread(r, "bridge-lifecycle"); t.setDaemon(true); return t;
            });
    private static final RetryableInit vs = new RetryableInit();
    private static final RetryableInit telemetry = new RetryableInit();
    private static final RetryableInit rc = new RetryableInit();
    private static final RetryableInit queryServer = new RetryableInit();
    private static final RetryableInit controlServer = new RetryableInit();
    private static volatile boolean registered;
    private static volatile Boolean productConnected;
    private static volatile String lastEvent = "NOT_REGISTERED";
    private static volatile String lastError;
    private static volatile int productId = -1;
    private static volatile long connectionGeneration;
    private static boolean scheduled;
    private static String token;

    private PcBridge() {}

    public static synchronized void start(String armToken) {
        token = armToken;
        registered = true;
        lastEvent = "SDK_REGISTERED";
        if (!scheduled) {
            scheduled = true;
            worker.scheduleWithFixedDelay(PcBridge::ensureSafely, 0, 2, TimeUnit.SECONDS);
        } else worker.execute(PcBridge::ensureSafely);
    }

    public static void onProductConnected(int id) {
        productId = id;
        productConnected = true;
        lastEvent = "PRODUCT_CONNECTED";
        Log.i(TAG, "product connected: " + id);
        worker.execute(() -> {
            if (!Boolean.TRUE.equals(productConnected)
                    || StickControlManager.getInstance().isArmedOrEnabling()) return;
            invalidateBindings();
            ensureSafely();
        });
    }

    public static void onProductDisconnected(int id) {
        productConnected = false;
        productId = id;
        lastEvent = "PRODUCT_DISCONNECTED";
        try {
            if (StickControlManager.getInstance().isArmedOrEnabling())
                StickControlManager.getInstance().emergencyStop();
        } catch (RuntimeException e) { Log.e(TAG, "disconnect release failed", e); }
        worker.execute(PcBridge::invalidateBindings);
    }

    private static void invalidateBindings() {
        connectionGeneration++;
        step("telemetry-stop", () -> TelemetryProvider.getInstance().stop());
        step("rc-stop", () -> RcOverrideMonitor.getInstance().stop());
        step("video-unbind", () -> VideoServerManager.getInstance().unbindSdk());
        step("vs-invalidate", () -> StickControlManager.getInstance().invalidateStateListener());
        vs.invalidate(); telemetry.invalidate(); rc.invalidate();
    }

    private static void step(String name, Runnable action) {
        try { action.run(); }
        catch (RuntimeException e) {
            lastError = name + ": " + e;
            Log.e(TAG, lastError, e);
        }
    }

    private static void ensureSafely() {
        try {
            if (!registered) return;
            queryServer.ensure(() -> QueryServerManager.getInstance().startServer(QUERY_PORT, token));
            controlServer.ensure(() -> ControlServerManager.getInstance().startServer(CONTROL_PORT, token));
            // Opening a socket must not count as a successful SDK binding.
            step("video-server", () -> VideoServerManager.getInstance().startServer(VIDEO_PORT));
            if (Boolean.FALSE.equals(productConnected)) return;
            vs.ensure(() -> StickControlManager.getInstance().startStateListener());
            telemetry.ensure(() -> TelemetryProvider.getInstance().start());
            rc.ensure(() -> RcOverrideMonitor.getInstance().start());
            step("video-binding", () -> VideoServerManager.getInstance().ensureSdkBinding());
            step("perception-binding", ObstacleAvoidanceController::startPerceptionListener);
        } catch (Throwable e) {
            lastError = e.toString(); Log.e(TAG, "lifecycle iteration failed", e);
        }
    }

    public static JSONObject diagnostics() throws JSONException {
        JSONObject j = new JSONObject();
        j.put("sdk_registered", registered);
        j.put("product_connected", productConnected == null ? JSONObject.NULL : productConnected);
        j.put("product_id", productId);
        j.put("connection_generation", connectionGeneration);
        j.put("last_event", lastEvent);
        j.put("last_error", lastError == null ? JSONObject.NULL : lastError);
        j.put("bindings_ready", vs.isReady() && telemetry.isReady() && rc.isReady());
        j.put("vs_binding", binding(vs));
        j.put("telemetry_binding", binding(telemetry));
        j.put("rc_binding", binding(rc));
        // Subscription installation is NOT proof of aircraft readiness.
        j.put("automatic_arm_or_resume", false);
        return j;
    }

    private static JSONObject binding(RetryableInit state) throws JSONException {
        return new JSONObject().put("installed", state.isReady()).put("attempts", state.attempts())
                .put("error", state.error() == null ? JSONObject.NULL : state.error());
    }
}
