package com.msdkremote.livecontrol.advanced;

import android.os.SystemClock;
import android.util.Log;

import androidx.annotation.NonNull;

import org.json.JSONException;
import org.json.JSONObject;

import java.util.Locale;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import com.msdkremote.lifecycle.PollLease;
import com.msdkremote.lifecycle.PendingSdkReads;
import com.msdkremote.lifecycle.FcHealthTracker;
import com.msdkremote.PcBridge;
import java.util.function.BiConsumer;

import dji.sdk.keyvalue.key.DJIKey;
import dji.sdk.keyvalue.key.FlightControllerKey;
import dji.sdk.keyvalue.key.KeyTools;
import dji.sdk.keyvalue.value.common.Attitude;
import dji.sdk.keyvalue.value.common.Velocity3D;
import dji.sdk.keyvalue.value.flightcontroller.FlightMode;
import dji.v5.common.callback.CommonCallbacks;
import dji.v5.common.error.IDJIError;
import dji.v5.manager.KeyManager;

/**
 * Caches flight telemetry (NED velocity, attitude, fused ground height, flying
 * state) so every control-channel ACK can carry a fresh snapshot to the PC.
 */
public final class TelemetryProvider {
    private static final String TAG = "TelemetryProvider";
    private static final TelemetryProvider INSTANCE = new TelemetryProvider();

    private final Object lock = new Object();
    private boolean started = false;
    private KeyManager listenerManager;
    private Object listenerHolder;
    private boolean cleanupFailed;
    private ScheduledExecutorService poller = null;
    private long pollTicks = 0;
    private long pollFailures = 0;
    private long generation = 0;
    private final FcHealthTracker health = new FcHealthTracker();
    private final PollLease motorsPollInFlight = new PollLease(2000), connectionPollInFlight = new PollLease(2000);
    private Boolean areMotorsOn;
    private long motorsMs;
    private long sourceGeneration = -1;
    private final DJIKey<Boolean> motorsKey = KeyTools.createKey(FlightControllerKey.KeyAreMotorsOn);
    private final DJIKey<Boolean> connectionKey = KeyTools.createKey(FlightControllerKey.KeyConnection);
    private String lastPollError = null;
    private long lastPollErrorMs = 0;

    private final PollLease velocityPollInFlight = new PollLease(2000);
    private final PollLease attitudePollInFlight = new PollLease(2000);
    private final PollLease heightPollInFlight = new PollLease(2000);
    private final PollLease flyingPollInFlight = new PollLease(2000);
    private final PollLease batteryPollInFlight = new PollLease(2000);
    private final PollLease flightModePollInFlight = new PollLease(2000);

    private Velocity3D velocity = null;
    private long velocityMs = 0;
    private Attitude attitude = null;
    private long attitudeMs = 0;
    private Integer ultrasonicHeightDm = null;
    private long heightMs = 0;
    private Boolean isFlying = null;
    private long flyingMs = 0;
    private Integer batteryPercent = null;
    private FlightMode flightMode = null;
    private long flightModeMs = 0;

    private final DJIKey<Velocity3D> velocityKey =
            KeyTools.createKey(FlightControllerKey.KeyAircraftVelocity);
    private final DJIKey<Attitude> attitudeKey =
            KeyTools.createKey(FlightControllerKey.KeyAircraftAttitude);
    private final DJIKey<Integer> heightKey =
            KeyTools.createKey(FlightControllerKey.KeyUltrasonicHeight);
    private final DJIKey<Boolean> flyingKey =
            KeyTools.createKey(FlightControllerKey.KeyIsFlying);
    private final DJIKey<Integer> batteryKey =
            KeyTools.createKey(FlightControllerKey.KeyBatteryPowerPercent);
    private final DJIKey<FlightMode> flightModeKey =
            KeyTools.createKey(FlightControllerKey.KeyFlightMode);

    private TelemetryProvider() {
    }

    public static TelemetryProvider getInstance() {
        return INSTANCE;
    }

    /** Only for ground video repair, never flight authorization. Unknown/stale means false. */
    public boolean isGroundedFresh(long maxAgeMs) {
        synchronized (lock) {
            long now = SystemClock.elapsedRealtime();
            return started && sourceGeneration == PcBridge.connectionGeneration()
                    && Boolean.FALSE.equals(isFlying) && Boolean.FALSE.equals(areMotorsOn)
                    && flyingMs > 0 && motorsMs > 0 && now >= flyingMs && now >= motorsMs
                    && now-flyingMs <= maxAgeMs && now-motorsMs <= maxAgeMs;
        }
    }

    public void start() {
        final long session;
        final KeyManager bindingManager=KeyManager.getInstance();
        final Object bindingHolder=new Object();
        synchronized (lock) {
            if (started) {
                return;
            }
            if(cleanupFailed)throw new IllegalStateException("TELEMETRY_LISTENER_CLEANUP_FAILED");
            listenerManager=bindingManager;listenerHolder=bindingHolder;
            started = true;
            session = ++generation;
            sourceGeneration = PcBridge.connectionGeneration();
            health.reset(generation);
        }
        try {
        Log.i(TAG, "Starting telemetry listeners");
        bindingManager.listen(velocityKey, bindingHolder, (oldValue, newValue) -> {
            synchronized (lock) {
                if (!started || generation != session || sourceGeneration != PcBridge.connectionGeneration()) return;
                velocity = newValue;
                health.listen("AircraftVelocity", SystemClock.elapsedRealtime(), newValue != null);
                velocityMs = SystemClock.elapsedRealtime();
            }
        });
        bindingManager.listen(attitudeKey, bindingHolder, (oldValue, newValue) -> {
            synchronized (lock) {
                if (!started || generation != session || sourceGeneration != PcBridge.connectionGeneration()) return;
                attitude = newValue;
                health.listen("AircraftAttitude", SystemClock.elapsedRealtime(), newValue != null);
                attitudeMs = SystemClock.elapsedRealtime();
            }
        });
        bindingManager.listen(heightKey, bindingHolder, (oldValue, newValue) -> {
            synchronized (lock) {
                if (!started || generation != session || sourceGeneration != PcBridge.connectionGeneration()) return;
                ultrasonicHeightDm = newValue;
                health.listen("UltrasonicHeight", SystemClock.elapsedRealtime(), newValue != null);
                heightMs = SystemClock.elapsedRealtime();
            }
        });
        bindingManager.listen(flyingKey, bindingHolder, (oldValue, newValue) -> {
            synchronized (lock) {
                if (!started || generation != session || sourceGeneration != PcBridge.connectionGeneration()) return;
                isFlying = newValue;
                health.listen("IsFlying", SystemClock.elapsedRealtime(), newValue != null);
                flyingMs = SystemClock.elapsedRealtime();
            }
        });
        bindingManager.listen(batteryKey, bindingHolder, (oldValue, newValue) -> {
            synchronized (lock) {
                if (!started || generation != session || sourceGeneration != PcBridge.connectionGeneration()) return;
                batteryPercent = newValue;
            }
        });
        bindingManager.listen(flightModeKey, bindingHolder, (oldValue, newValue) -> {
            synchronized (lock) {
                if (!started || generation != session || sourceGeneration != PcBridge.connectionGeneration()) return;
                flightMode = newValue;
                health.listen("FlightMode", SystemClock.elapsedRealtime(), newValue != null);
                flightModeMs = SystemClock.elapsedRealtime();
            }
        });

        bindingManager.listen(motorsKey, bindingHolder, (oldValue, newValue) -> {
            synchronized(lock) {
                if (!started || generation != session || sourceGeneration != PcBridge.connectionGeneration()) return;
                areMotorsOn=newValue; motorsMs=newValue==null?0:SystemClock.elapsedRealtime();
                health.listen("AreMotorsOn",SystemClock.elapsedRealtime(),newValue!=null);
            }
        });
        bindingManager.listen(connectionKey, bindingHolder, (oldValue, newValue) -> {
            synchronized(lock) {
                if (!started || generation != session || sourceGeneration != PcBridge.connectionGeneration()) return;
                health.connection(newValue); health.listen("Connection",SystemClock.elapsedRealtime(),newValue!=null);
            }
        });
        pollValue(connectionKey, connectionPollInFlight, "Connection", 1000, (v,t)->health.connection(v));

        // DJI listeners are change notifications. In a steady hover the
        // decimetre height and zero velocity can remain unchanged for many
        // seconds, making fresh data look stale. Poll the GET-capable keys at
        // 5 Hz as well; one in-flight request per key prevents a slow handler
        // from building an unbounded callback queue.
        synchronized (lock) {
            if (!started || generation != session || sourceGeneration != PcBridge.connectionGeneration()) return;
            poller = Executors.newSingleThreadScheduledExecutor(runnable -> {
                Thread thread = new Thread(runnable, "telemetry-5hz");
                thread.setDaemon(true);
                return thread;
            });
            poller.scheduleAtFixedRate(this::pollSafely, 0, 200, TimeUnit.MILLISECONDS);
        }
        } catch (RuntimeException failure) {
            stop();
            throw failure;
        }
    }

    public void stop() {
        ScheduledExecutorService toStop;
        final KeyManager ownerManager;final Object ownerHolder;
        synchronized (lock) {
            started = false;
            ownerManager=listenerManager;ownerHolder=listenerHolder;
            generation++;
            toStop = poller;
            poller = null;
            velocity = null; attitude = null; ultrasonicHeightDm = null;
            isFlying = null; areMotorsOn = null; motorsMs = 0; flightMode = null; batteryPercent = null;
            health.reset(generation);
            if(Boolean.FALSE.equals(PcBridge.productConnected()))health.connection(false);
            velocityMs = attitudeMs = heightMs = flyingMs = flightModeMs = 0;
            velocityPollInFlight.invalidate(); attitudePollInFlight.invalidate();
            heightPollInFlight.invalidate(); flyingPollInFlight.invalidate();
            batteryPollInFlight.invalidate(); flightModePollInFlight.invalidate();
            motorsPollInFlight.invalidate(); connectionPollInFlight.invalidate();
        }
        if (toStop != null) {
            toStop.shutdownNow();
        }
        try {
            if(ownerManager!=null&&ownerHolder!=null)ownerManager.cancelListen(ownerHolder);
            synchronized(lock){if(listenerHolder==ownerHolder){listenerHolder=null;listenerManager=null;cleanupFailed=false;}}
        }catch(RuntimeException error){synchronized(lock){cleanupFailed=true;}Log.w(TAG,"telemetry listener cleanup failed",error);}

    }

    private void pollSafely() {
        try {
            pollValue(velocityKey, velocityPollInFlight, "AircraftVelocity", 200, (value, when) -> {
                velocity = value;
                velocityMs = when;
            });
            pollValue(attitudeKey, attitudePollInFlight, "AircraftAttitude", 200, (value, when) -> {
                attitude = value;
                attitudeMs = when;
            });
            pollValue(heightKey, heightPollInFlight, "UltrasonicHeight", 200, (value, when) -> {
                ultrasonicHeightDm = value;
                heightMs = when;
            });
            pollValue(flyingKey, flyingPollInFlight, "IsFlying", 200, (value, when) -> {
                isFlying = value;
                flyingMs = when;
            });
            pollValue(flightModeKey, flightModePollInFlight, "FlightMode", 200, (value, when) -> {
                flightMode = value;
                flightModeMs = when;
            });
            // Motors can stay false without listener callbacks. Their GET must
            // stay within the same fresh ground-proof window as IsFlying.
            pollValue(motorsKey, motorsPollInFlight, "AreMotorsOn", 200,
                    (value, when) -> { areMotorsOn=value; motorsMs=when; });
            if (++pollTicks % 5 == 0) {
                pollValue(batteryKey, batteryPollInFlight, "BatteryPowerPercent", 1000,
                        (value, when) -> batteryPercent = value);
            }
        } catch (Throwable error) {
            Log.e(TAG, "telemetry poll tick failed", error);
        }
    }

    private <T> void pollValue(DJIKey<T> key, PollLease inFlight, String id, long interval, BiConsumer<T, Long> update) {
        final long session, product, request;
        final PendingSdkReads.Read physical;
        synchronized (lock) {
            if (!started) return;
            if (sourceGeneration != PcBridge.connectionGeneration()) {
                sourceGeneration=PcBridge.connectionGeneration(); generation++; health.reset(generation);
                velocityPollInFlight.invalidate();attitudePollInFlight.invalidate();heightPollInFlight.invalidate();
                flyingPollInFlight.invalidate();flightModePollInFlight.invalidate();batteryPollInFlight.invalidate();
                motorsPollInFlight.invalidate();connectionPollInFlight.invalidate();
                velocity=null; attitude=null; ultrasonicHeightDm=null; isFlying=null; areMotorsOn=null; flightMode=null;
                velocityMs=attitudeMs=heightMs=flyingMs=motorsMs=flightModeMs=0;
            }
            long now=SystemClock.elapsedRealtime();
            if (!health.due(id,now))return;
            session=generation; product=sourceGeneration;
            long expired=inFlight.expiredCount();
            request=inFlight.begin(now);
            if(inFlight.expiredCount()>expired){PendingSdkReads.SHARED.telemetryDeadline(key,now);health.timeout(id,now);inFlight.complete(request);return;}
            if(request==0)return;
            physical=PendingSdkReads.SHARED.reserve(key,PendingSdkReads.Owner.TELEMETRY,product,now,
                    "IsFlying".equals(id)||"AreMotorsOn".equals(id));
            if(physical==null){inFlight.complete(request);return;}
            health.issued(id,now);
        }
        try {
            KeyManager.getInstance().getValue(key,new CommonCallbacks.CompletionCallbackWithParam<T>() {
                @Override public void onSuccess(T value) {
                    if(!PendingSdkReads.SHARED.complete(physical.id))return;
                    synchronized(lock) {
                        if(!started || session!=generation || product!=PcBridge.connectionGeneration()
                                || !inFlight.complete(request))return;
                        long now=SystemClock.elapsedRealtime();
                        health.success(id,now,value!=null,interval);
                        if(value!=null)update.accept(value,now);
                    }
                }
                @Override public void onFailure(@NonNull IDJIError error) {
                    if(!PendingSdkReads.SHARED.complete(physical.id))return;
                    synchronized(lock) {
                        if(!started || session!=generation || product!=PcBridge.connectionGeneration()
                                || !inFlight.complete(request))return;
                        pollFailures++; lastPollError=key+": "+error; lastPollErrorMs=SystemClock.elapsedRealtime();
                        health.failed(id,lastPollErrorMs,error.errorCode(),error.toString());
                    }
                }
            });
        } catch(RuntimeException error) {
            PendingSdkReads.SHARED.uncertain(physical.id);
            synchronized(lock) {
                if(session==generation && inFlight.complete(request)) {
                    pollFailures++;lastPollError=error.toString();lastPollErrorMs=SystemClock.elapsedRealtime();
                    health.failed(id,lastPollErrorMs,"SDK_SUBMISSION_UNCERTAIN");
                }
            }
        }
    }

    public FcHealthTracker healthTracker() { return health; }

    /** One-line state used by the Virtual Stick send diagnostic. */
    public String summaryForLog() {
        long now = SystemClock.elapsedRealtime();
        synchronized (lock) {
            String velocityText = velocity == null
                    ? "?"
                    : String.format(Locale.US, "%.2f,%.2f,%.2f",
                            velocity.getX(), velocity.getY(), velocity.getZ());
            String attitudeText = attitude == null
                    ? "?"
                    : String.format(Locale.US, "%.1f,%.1f,%.1f",
                            attitude.getPitch(), attitude.getRoll(), attitude.getYaw());
            String heightText = ultrasonicHeightDm == null
                    ? "?"
                    : String.format(Locale.US, "%.2f", ultrasonicHeightDm / 10.0);
            return "vel=" + velocityText
                    + "@" + (velocityMs == 0 ? -1 : now - velocityMs) + "ms"
                    + " att=" + attitudeText
                    + "@" + (attitudeMs == 0 ? -1 : now - attitudeMs) + "ms"
                    + " h=" + heightText
                    + "@" + (heightMs == 0 ? -1 : now - heightMs) + "ms"
                    + " flying=" + isFlying
                    + " mode=" + flightMode
                    + "@" + (flightModeMs == 0 ? -1 : now - flightModeMs) + "ms"
                    + " polls_failed=" + pollFailures;
        }
    }

    /**
     * Snapshot as a JSON object for embedding in protocol ACKs. Values that
     * were never reported are null; ages let the PC reject stale data.
     */
    public JSONObject snapshotJson() {
        long now;
        JSONObject json = new JSONObject();
        try {
            json.put("bridge_build_id", "5.18-connectivity.20260910.6");
            json.put("bridge_health", com.msdkremote.PcBridge.diagnostics());
            json.put("max_tilt_angle_deg", StickControlManager.MAX_TILT_ANGLE_DEG);
            JSONObject video = com.msdkremote.livevideo.VideoServerManager.getInstance().diagnostics();
            json.put("video", video);
            synchronized (lock) {
                // Read the clock with the samples locked so an intervening
                // callback cannot make a fresh sample appear to have negative age.
                now = SystemClock.elapsedRealtime();
                json.put("telemetry_started", started);
                json.put("fc_health", new JSONObject(health.snapshot(now)).put("recovery_state", PcBridge.recoveryState())
                        .put("recovery_reason", PcBridge.recoveryReason()));
                json.put("are_motors_on", areMotorsOn==null?JSONObject.NULL:areMotorsOn);
                json.put("are_motors_on_age_ms", areMotorsOn==null||motorsMs==0?JSONObject.NULL:Math.max(0,now-motorsMs));
                json.put("sdk_reads_pending",PendingSdkReads.SHARED.size());
                json.put("sdk_read_requests",new org.json.JSONArray(PendingSdkReads.SHARED.snapshot(now)));
                json.put("telemetry_generation", generation);
                json.put("telemetry_poll_failures", pollFailures);
                json.put("telemetry_last_error", lastPollError == null ? JSONObject.NULL : lastPollError);
                json.put("telemetry_last_error_age_ms", lastPollErrorMs == 0 ? -1 : now-lastPollErrorMs);
                json.put("telemetry_expired_gets", velocityPollInFlight.expiredCount()
                        + attitudePollInFlight.expiredCount() + heightPollInFlight.expiredCount()
                        + flyingPollInFlight.expiredCount() + batteryPollInFlight.expiredCount()
                        + flightModePollInFlight.expiredCount());
                if (velocity != null) {
                    json.put("velocity_north_mps", velocity.getX());
                    json.put("velocity_east_mps", velocity.getY());
                    json.put("velocity_down_mps", velocity.getZ());
                    json.put("velocity_age_ms", now - velocityMs);
                }
                if (attitude != null) {
                    json.put("yaw_deg", attitude.getYaw());
                    json.put("pitch_deg", attitude.getPitch());
                    json.put("roll_deg", attitude.getRoll());
                    json.put("attitude_age_ms", now - attitudeMs);
                }
                if (ultrasonicHeightDm != null) {
                    json.put("height_m", ultrasonicHeightDm / 10.0);
                    json.put("height_age_ms", now - heightMs);
                }
                if (isFlying != null) {
                    json.put("is_flying", isFlying.booleanValue());
                    json.put("is_flying_age_ms", now - flyingMs);
                }
                if (batteryPercent != null) {
                    json.put("battery_percent", batteryPercent.intValue());
                }
                if (flightMode != null) {
                    json.put("flight_mode", flightMode.toString());
                    json.put("flight_mode_age_ms", now - flightModeMs);
                }
            }
            json.put("armed", StickControlManager.getInstance().isArmed());
            long overrideMs = RcOverrideMonitor.getInstance().lastOverrideMs();
            if (overrideMs > 0) {
                json.put("rc_override_age_ms", now - overrideMs);
            }
            // Virtual Stick state: distinguishes "commands accepted" from
            // "flight controller ignored them"; both ACK identically.
            JSONObject vs = StickControlManager.getInstance().stateJson();
            for (java.util.Iterator<String> it = vs.keys(); it.hasNext(); ) {
                String key = it.next();
                json.put(key, vs.get(key));
            }
        } catch (JSONException ignored) {
            // Keys above are constants and values are JSON-safe primitives.
        }
        return json;
    }
}
