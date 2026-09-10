package com.msdkremote;

import android.util.Log;
import android.os.SystemClock;
import com.msdkremote.lifecycle.*;
import dji.sdk.keyvalue.key.*;
import dji.v5.manager.KeyManager;
import dji.v5.common.callback.CommonCallbacks;
import dji.v5.common.error.IDJIError;
import androidx.annotation.NonNull;
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
    private static final String PROCESS_START_ID=java.util.UUID.randomUUID().toString();
    private static final long PROCESS_STARTED_MS=SystemClock.elapsedRealtime();
    private static long eventSerial;
    // No UI toggle until every manual sample mutation entry point is also gated.
    private static final boolean AUTOMATIC_FC_RECOVERY=false;
    private static FcRecoveryCoordinator recovery;
    public static String processStartId(){return PROCESS_START_ID;}
    public static long connectionGeneration(){return connectionGeneration;}
    public static Boolean productConnected(){return productConnected;}
    public static String recoveryState(){return recovery==null?"DISABLED":recovery.state();}
    public static String recoveryReason(){return recovery==null?"MANUAL_SDK_MUTATION_PATHS_NOT_FULLY_GATED":recovery.reason();}
    public static void execute(Runnable work){worker.execute(work);}


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

    public static void onProductConnected(int id) { enqueueProduct(id,true,"PRODUCT_CONNECTED"); }
    public static void onProductDisconnected(int id) { enqueueProduct(id,false,"PRODUCT_DISCONNECTED"); }
    public static void onProductChanged(int id) { enqueueProduct(id,productConnected,"PRODUCT_CHANGED"); }
    private static synchronized void enqueueProduct(int id,Boolean connected,String event) {
        if(!"PRODUCT_CHANGED".equals(event) && productId==id && java.util.Objects.equals(productConnected,connected))return;
        final long serial=++eventSerial, capturedGeneration=++connectionGeneration;
        productId=id;productConnected=connected;lastEvent=event;
        MaintenanceGate.SHARED.sourceChanged(capturedGeneration);
        // Capture this event; a later reconnect must not erase an earlier disconnect cleanup.
        worker.execute(()->{
            QueryServerManager.getInstance().onSourceChanged();
            if(Boolean.FALSE.equals(connected)) {
                if(StickControlManager.getInstance().isArmedOrEnabling())
                    step("disconnect-release",()->StickControlManager.getInstance().emergencyStop());
                invalidateBindings();
                return;
            }
            if(serial!=eventSerial)return;
            if("PRODUCT_CHANGED".equals(event) && (StickControlManager.getInstance().isArmedOrEnabling()
                    ||!TelemetryProvider.getInstance().isGroundedFresh(500))) {
                lastError="PRODUCT_CHANGED_REBIND_WAIT_OPERATOR";
                return;
            }
            // An initial subscription install is read-only; it is not recovery or flight authorization.
            ensureSafely();
        });
    }

    private static void invalidateBindings() {
        step("telemetry-stop", () -> TelemetryProvider.getInstance().stop());
        step("rc-stop", () -> RcOverrideMonitor.getInstance().stop());
        step("video-unbind", () -> VideoServerManager.getInstance().unbindSdk());
        step("perception-unbind", ObstacleAvoidanceController::stopPerceptionListener);
        step("vs-invalidate", () -> StickControlManager.getInstance().invalidateStateListener());
        vs.invalidate(); telemetry.invalidate(); rc.invalidate();
    }

    @SuppressWarnings("unchecked")
    private static void ensureRecovery() {
        if(recovery!=null)return;
        GroundProof proof=new GroundProof(worker,SystemClock::elapsedRealtime,PcBridge::connectionGeneration,
            (key,callback)->KeyManager.getInstance().getValue((DJIKey<Boolean>)key,
                new CommonCallbacks.CompletionCallbackWithParam<Boolean>() {
                    @Override public void onSuccess(Boolean value){callback.done(value,null);}
                    @Override public void onFailure(@NonNull IDJIError error){callback.done(null,error.errorCode());}
                }),KeyTools.createKey(FlightControllerKey.KeyIsFlying),
                KeyTools.createKey(FlightControllerKey.KeyAreMotorsOn),PendingSdkReads.SHARED);
        recovery=new FcRecoveryCoordinator(AUTOMATIC_FC_RECOVERY,MaintenanceGate.SHARED,proof,
            TelemetryProvider.getInstance().healthTracker(),worker,SystemClock::elapsedRealtime,
            PcBridge::connectionGeneration,()->{TelemetryProvider.getInstance().stop();TelemetryProvider.getInstance().start();});
        worker.scheduleWithFixedDelay(()->recovery.tick(),200,200,TimeUnit.MILLISECONDS);
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
            ensureRecovery();
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
        j.put("process_start_id",PROCESS_START_ID);
        j.put("process_started_elapsed_ms",PROCESS_STARTED_MS);
        j.put("pid",android.os.Process.myPid());
        j.put("automatic_fc_recovery_enabled",AUTOMATIC_FC_RECOVERY);
        j.put("recovery_state",recoveryState());
        j.put("recovery_reason",recoveryReason());
        j.put("unsafe_intent_latched",MaintenanceGate.SHARED.unsafeIntent());
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
