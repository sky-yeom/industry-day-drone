package com.msdkremote.livecontrol.advanced;

import android.os.SystemClock;
import android.util.Log;

import androidx.annotation.NonNull;

import org.json.JSONException;
import org.json.JSONArray;
import org.json.JSONObject;

import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;

import dji.sdk.keyvalue.key.DJIKey;
import dji.sdk.keyvalue.key.FlightControllerKey;
import dji.sdk.keyvalue.key.KeyTools;
import dji.sdk.keyvalue.value.common.EmptyMsg;
import dji.sdk.keyvalue.value.flightcontroller.FlightCoordinateSystem;
import dji.sdk.keyvalue.value.flightcontroller.FlightControlAuthorityChangeReason;
import dji.sdk.keyvalue.value.flightcontroller.RollPitchControlMode;
import dji.sdk.keyvalue.value.flightcontroller.VerticalControlMode;
import dji.sdk.keyvalue.value.flightcontroller.VirtualStickFlightControlParam;
import dji.sdk.keyvalue.value.flightcontroller.YawControlMode;
import dji.v5.common.callback.CommonCallbacks;
import dji.v5.common.error.IDJIError;
import dji.v5.manager.KeyManager;
import dji.v5.manager.aircraft.virtualstick.IStick;
import dji.v5.manager.aircraft.virtualstick.VirtualStickManager;
import dji.v5.manager.aircraft.virtualstick.VirtualStickState;
import dji.v5.manager.aircraft.virtualstick.VirtualStickStateListener;

/**
 * Owns three explicit Virtual Stick paths. OFFICIAL_ADVANCED is the production
 * path from DJI's V5 sample: enable advanced mode, then continuously call
 * sendVirtualStickAdvancedParam. BASIC and ADVANCED_DIRECT are retained only
 * for controlled diagnostics; they are never mixed into the production path.
 *
 * Stick scaling (DJI's own conversion, speedLevel included):
 *   leftStick.vertical    -> climb rate,  full scale 6 m/s
 *   leftStick.horizontal  -> yaw rate,    full scale 180 deg/s
 *   rightStick.vertical   -> forward,     full scale 23 m/s
 *   rightStick.horizontal -> right,       full scale 23 m/s
 */
public final class StickControlManager {
    private enum StickMode {
        BASIC,
        OFFICIAL_ADVANCED,
        OFFICIAL_ADVANCED_ANGLE,
        ADVANCED_DIRECT
    }

    public interface ResultCallback {
        void onResult(boolean success, String detail);
    }

    private static final String TAG = "StickControlManager";
    private static final StickControlManager INSTANCE = new StickControlManager();

    // Hard app-side ceilings. Stage 3 traverses ~2.5 m sideways along the
    // wall at 180 cm, where the room is clear, so lateral is no longer the
    // tightly-capped axis it was for the narrow floor-level route.
    public static final double MAX_FORWARD_SPEED_MPS = 0.5;
    public static final double MAX_LATERAL_SPEED_MPS = 0.5;
    public static final double MAX_VERTICAL_SPEED_MPS = 0.45;
    public static final double MAX_YAW_RATE_DPS = 18.0;
    // Diagnostic ANGLE path ceiling. Two degrees is used for the first
    // indoor test; the hard ceiling prevents an API caller from requesting
    // the SDK's much larger +/-30 degree range by mistake.
    public static final double MAX_TILT_ANGLE_DEG = 3.0;

    private static final long MODE_STATE_GRACE_MS = 1000;
    private static final long RELEASE_RETRY_MS = 500;
    // Bounded: an unbounded retry loop spun 200+ times in flight.
    private static final int MAX_RELEASE_ATTEMPTS = 10;

    // speedLevel sets each axis' FULL-SCALE speed, so a low value turns a
    // slow setpoint into a large, responsive stick deflection. At 0.1 a
    // 0.30 m/s lateral command was only 13% deflection and the aircraft did
    // not move at all; at 0.05 the same command is 26% and full scale stays
    // safely low (lateral 1.15 m/s, vertical 0.30 m/s).
    // 0.08 leaves the vertical axis headroom: at 0.05 its full scale was
    // exactly 0.30 m/s, so a 0.30 m/s climb was already saturated and the
    // aircraft stalled around 1.5 m. Lateral stays usable (0.30 m/s is still
    // speedLevel is a pure app-side scale: value = speedLevel * FULL_SCALE *
    // position / 660, so the m/s the aircraft receives is the same whatever
    // it is set to - only the quantisation step and the ceiling change. An
    // earlier note here claiming a low value made the aircraft "more
    // responsive" was wrong. 0.10 is the lowest value at which every app
    // limit is actually reachable: at 0.08 an 18 deg/s yaw needed 825 counts,
    // was clamped to 660, and silently topped out at 14.4 deg/s.
    private static final double BASIC_SPEED_LEVEL = 0.10;
    private static final double ADVANCED_SPEED_LEVEL = 1.00;
    private static final double STICK_RANGE = 660.0;
    private static final double VERTICAL_FULL_SCALE = 6.0;
    private static final double HORIZONTAL_FULL_SCALE = 23.0;
    private static final double YAW_FULL_SCALE = 180.0;

    private final Object lock = new Object();
    private final DJIKey.ActionKey<VirtualStickFlightControlParam, EmptyMsg> vsSendKey =
            KeyTools.createKey(FlightControllerKey.KeySendVirtualStickFlightControlData);
    // Never shut down: retries the emergency release until DJI confirms it.
    private final ScheduledExecutorService releaseExecutor =
            Executors.newSingleThreadScheduledExecutor(runnable -> {
                Thread thread = new Thread(runnable, "vs-release-retry");
                thread.setDaemon(true);
                return thread;
            });

    private ScheduledExecutorService sender;
    private boolean armed;
    private boolean enabling;
    // Incremented whenever control is released; a late enable success from an
    // older epoch must never re-arm.
    private long epoch = 0;
    private long lastHeartbeatMs;
    private long lastSequence = -1;
    private StickMode stickMode = StickMode.OFFICIAL_ADVANCED;
    private volatile long modeRequestedMs = 0;

    private final AtomicLong officialAdvancedFramesSent = new AtomicLong();
    private volatile long lastOfficialAdvancedFrameMs = 0;

    private final AtomicLong directFramesSent = new AtomicLong();
    private final AtomicLong directFramesSucceeded = new AtomicLong();
    private final AtomicLong directFramesFailed = new AtomicLong();
    private final AtomicInteger consecutiveDirectFailures = new AtomicInteger();
    private volatile long lastDirectCallbackMs = 0;
    private volatile String lastDirectError = null;

    private double forwardMps;
    private double rightMps;
    private double upMps;
    private double yawRateDps;
    private double requestedForwardMps;
    private double requestedRightMps;
    private double requestedUpMps;
    private double requestedYawRateDps;
    // ANGLE mode uses physical body tilt, not the misleading velocity-axis
    // field mapping. Positive semantic values always mean forward/right;
    // buildAdvancedParamLocked() performs DJI's BODY-angle sign conversion.
    private double forwardTiltDeg;
    private double rightTiltDeg;
    private double requestedForwardTiltDeg;
    private double requestedRightTiltDeg;
    private long activeCommandSequence = -1;
    private long activeCommandAcceptedMs = 0;

    private volatile long lastSdkSubmitSequence = -1;
    private volatile long lastSdkSubmitFrame = -1;
    private volatile long lastSdkSubmitMs = 0;
    private volatile String lastSdkSubmitResult = "NOT_SUBMITTED";
    private volatile long lastSdkResultSequence = -1;
    private volatile long lastSdkResultFrame = -1;
    private volatile long lastSdkResultMs = 0;
    private volatile String lastSdkResult = "NOT_AVAILABLE";
    private volatile String lastSdkResultError = null;

    private volatile Boolean vsEnabled = null;
    private volatile Boolean vsAdvancedEnabled = null;
    private volatile String vsAuthorityOwner = null;
    private volatile String vsChangeReason = null;
    private boolean stateListenerStarted = false;

    private StickControlManager() {
    }

    public static StickControlManager getInstance() {
        return INSTANCE;
    }

    /** Subscribe to DJI's Virtual Stick state so failures are observable. */
    public void startStateListener() {
        synchronized (lock) {
            if (stateListenerStarted) {
                return;
            }
        }
        VirtualStickManager.getInstance().setVirtualStickStateListener(
                new VirtualStickStateListener() {
                    @Override
                    public void onVirtualStickStateUpdate(@NonNull VirtualStickState state) {
                        vsEnabled = state.isVirtualStickEnable();
                        vsAdvancedEnabled = state.isVirtualStickAdvancedModeEnabled();
                        Object owner = state.getCurrentFlightControlAuthorityOwner();
                        vsAuthorityOwner = owner == null ? null : owner.toString();
                        Log.i(TAG, "VS state: enabled=" + vsEnabled
                                + " advanced=" + vsAdvancedEnabled
                                + " authority=" + vsAuthorityOwner);
                    }

                    @Override
                    public void onChangeReasonUpdate(
                            @NonNull FlightControlAuthorityChangeReason reason) {
                        vsChangeReason = reason.toString();
                        Log.w(TAG, "Flight control authority changed: " + reason);
                    }
                });
        synchronized (lock) { stateListenerStarted = true; }
    }

    public void invalidateStateListener() {
        synchronized (lock) {
            stateListenerStarted = false;
            vsEnabled = null;
            vsAdvancedEnabled = null;
            vsAuthorityOwner = null;
            vsChangeReason = null;
        }
    }

    public void arm(@NonNull ResultCallback callback) {
        final long armEpoch;
        synchronized (lock) {
            if (armed) {
                lastHeartbeatMs = SystemClock.elapsedRealtime();
                callback.onResult(true, "already_armed");
                return;
            }
            if (enabling) {
                callback.onResult(false, "arm_in_progress");
                return;
            }
            enabling = true;
            armEpoch = epoch;
            zeroLocked();
        }

        try {
            VirtualStickManager.getInstance().enableVirtualStick(
                    new CommonCallbacks.CompletionCallback() {
                        @Override
                        public void onSuccess() {
                            onEnableSucceeded(armEpoch, callback);
                        }

                        @Override
                        public void onFailure(@NonNull IDJIError error) {
                            synchronized (lock) {
                                if (epoch == armEpoch) {
                                    enabling = false;
                                    armed = false;
                                    zeroLocked();
                                }
                            }
                            callback.onResult(false, error.toString());
                        }
                    });
        } catch (RuntimeException error) {
            Log.e(TAG, "enableVirtualStick threw", error);
            synchronized (lock) {
                if (epoch == armEpoch) {
                    enabling = false;
                    armed = false;
                    zeroLocked();
                }
            }
            callback.onResult(false, "enable_virtual_stick_threw");
        }
    }

    /** Select the official Advanced path or an explicitly requested diagnostic. */
    public void selectMode(@NonNull String requested, @NonNull ResultCallback callback) {
        final StickMode selected;
        if ("basic".equalsIgnoreCase(requested)) {
            selected = StickMode.BASIC;
        } else if ("advanced".equalsIgnoreCase(requested)
                || "official_advanced".equalsIgnoreCase(requested)) {
            selected = StickMode.OFFICIAL_ADVANCED;
        } else if ("advanced_angle".equalsIgnoreCase(requested)
                || "official_advanced_angle".equalsIgnoreCase(requested)) {
            selected = StickMode.OFFICIAL_ADVANCED_ANGLE;
        } else if ("advanced_direct".equalsIgnoreCase(requested)) {
            selected = StickMode.ADVANCED_DIRECT;
        } else {
            callback.onResult(false, "unsupported_stick_mode");
            return;
        }
        synchronized (lock) {
            if (armed || enabling) {
                callback.onResult(false, "cannot_change_mode_while_armed");
                return;
            }
            stickMode = selected;
            zeroLocked();
            resetSendDiagnostics();
        }
        try {
            centreSticks();
            boolean expectAdvanced = usesAdvancedMode(selected);
            VirtualStickManager.getInstance().setVirtualStickAdvancedModeEnabled(expectAdvanced);
            VirtualStickManager.getInstance().setSpeedLevel(
                    expectAdvanced ? ADVANCED_SPEED_LEVEL : BASIC_SPEED_LEVEL);
            modeRequestedMs = SystemClock.elapsedRealtime();
            // DJI's official sample treats this setter as synchronous local
            // configuration. The state listener can arrive a little later, so
            // do not turn that harmless callback delay into a false failure.
            callback.onResult(true, selected.name().toLowerCase());
        } catch (RuntimeException error) {
            Log.e(TAG, "Could not select stick mode " + selected, error);
            callback.onResult(false, "stick_mode_failed");
        }
    }

    private void onEnableSucceeded(long armEpoch, @NonNull ResultCallback callback) {
        boolean cancelled;
        synchronized (lock) {
            cancelled = (epoch != armEpoch);
        }
        if (cancelled) {
            abortStaleArm(callback);
            return;
        }

        final StickMode selectedMode;
        synchronized (lock) {
            selectedMode = stickMode;
        }
        try {
            // BASIC and ADVANCED share a process-wide switch. Centre first so
            // changing paths cannot replay a stale Basic stick value.
            centreSticks();
            boolean expectAdvanced = usesAdvancedMode(selectedMode);
            VirtualStickManager.getInstance().setVirtualStickAdvancedModeEnabled(expectAdvanced);
            modeRequestedMs = SystemClock.elapsedRealtime();
            // VirtualStickManager applies speedLevel to official Advanced
            // velocity setpoints. Pinning 1.0 preserves the requested m/s.
            // The direct diagnostic bypasses that conversion but uses the same
            // setting so another sample screen cannot alter its semantics.
            VirtualStickManager.getInstance().setSpeedLevel(
                    expectAdvanced ? ADVANCED_SPEED_LEVEL : BASIC_SPEED_LEVEL);
            Log.i(TAG, "mode=" + selectedMode + " speedLevel="
                    + VirtualStickManager.getInstance().getSpeedLevel());
            centreSticks();
            resetSendDiagnostics();
        } catch (RuntimeException error) {
            Log.e(TAG, "Stick setup failed; releasing", error);
            long releaseEpoch;
            synchronized (lock) {
                if (epoch == armEpoch) {
                    enabling = false;
                    armed = false;
                    epoch++;
                }
                releaseEpoch = epoch;
            }
            releaseVirtualStick(releaseEpoch, 1);
            callback.onResult(false, "stick_setup_failed");
            return;
        }

        boolean committed = false;
        synchronized (lock) {
            if (epoch == armEpoch) {
                enabling = false;
                armed = true;
                lastHeartbeatMs = SystemClock.elapsedRealtime();
                lastSequence = -1;
                startSenderLocked();
                committed = true;
            }
        }
        if (!committed) {
            abortStaleArm(callback);
            return;
        }
        callback.onResult(true, "armed");
        // A stick already held over produces no change event; sample it once.
        RcOverrideMonitor.getInstance().checkNow();
    }

    private void abortStaleArm(@NonNull ResultCallback callback) {
        long releaseEpoch;
        boolean newerArmActive;
        synchronized (lock) {
            newerArmActive = enabling || armed;
            releaseEpoch = epoch;
        }
        if (!newerArmActive) {
            Log.w(TAG, "Arm completed after cancellation; disabling again");
            releaseVirtualStick(releaseEpoch, 1);
        }
        callback.onResult(false, "arm_cancelled");
    }

    public boolean acceptHeartbeat(long sequence) {
        synchronized (lock) {
            if (!armed || sequence <= lastSequence) {
                return false;
            }
            lastSequence = sequence;
            lastHeartbeatMs = SystemClock.elapsedRealtime();
            return true;
        }
    }

    public boolean setVelocity(
            long sequence, double forward, double right, double up, double yawRate) {
        synchronized (lock) {
            if (!armed || sequence <= lastSequence
                    || stickMode == StickMode.OFFICIAL_ADVANCED_ANGLE
                    || !allFinite(forward, right, up, yawRate)) {
                return false;
            }
            lastSequence = sequence;
            lastHeartbeatMs = SystemClock.elapsedRealtime();
            requestedForwardMps = forward;
            requestedRightMps = right;
            requestedUpMps = up;
            requestedYawRateDps = yawRate;
            forwardMps = clamp(forward, MAX_FORWARD_SPEED_MPS);
            rightMps = clamp(right, MAX_LATERAL_SPEED_MPS);
            upMps = clamp(up, MAX_VERTICAL_SPEED_MPS);
            yawRateDps = clamp(yawRate, MAX_YAW_RATE_DPS);
            activeCommandSequence = sequence;
            activeCommandAcceptedMs = lastHeartbeatMs;
            // BASIC updates sticky IStick state immediately. Both Advanced
            // paths are emitted only by the 20 Hz sender.
            if (stickMode == StickMode.BASIC) {
                sendCurrentLocked();
            }
            return true;
        }
    }

    /**
     * Set an explicit BODY-angle command. Semantic signs are intuitive:
     * positive forwardTilt tilts/moves forward and positive rightTilt
     * tilts/moves right. DJI BODY+ANGLE requires negative Pitch for forward,
     * while Roll already uses positive for right; that conversion happens
     * only in buildAdvancedParamLocked().
     */
    public boolean setAttitude(
            long sequence, double forwardTilt, double rightTilt,
            double up, double yawRate) {
        synchronized (lock) {
            if (!armed || sequence <= lastSequence
                    || stickMode != StickMode.OFFICIAL_ADVANCED_ANGLE
                    || !allFinite(forwardTilt, rightTilt, up, yawRate)) {
                return false;
            }
            lastSequence = sequence;
            lastHeartbeatMs = SystemClock.elapsedRealtime();
            requestedForwardTiltDeg = forwardTilt;
            requestedRightTiltDeg = rightTilt;
            requestedUpMps = up;
            requestedYawRateDps = yawRate;
            forwardTiltDeg = clamp(forwardTilt, MAX_TILT_ANGLE_DEG);
            rightTiltDeg = clamp(rightTilt, MAX_TILT_ANGLE_DEG);
            upMps = clamp(up, MAX_VERTICAL_SPEED_MPS);
            yawRateDps = clamp(yawRate, MAX_YAW_RATE_DPS);
            // Velocity setpoints cannot leak into the ANGLE path.
            requestedForwardMps = 0.0;
            requestedRightMps = 0.0;
            forwardMps = 0.0;
            rightMps = 0.0;
            activeCommandSequence = sequence;
            activeCommandAcceptedMs = lastHeartbeatMs;
            return true;
        }
    }

    public boolean zero(long sequence) {
        boolean sendFailed = false;
        synchronized (lock) {
            zeroLocked();
            activeCommandSequence = sequence;
            activeCommandAcceptedMs = SystemClock.elapsedRealtime();
            if (armed) {
                lastHeartbeatMs = activeCommandAcceptedMs;
                if (usesAdvancedMode(stickMode)) {
                    try {
                        sendCurrentLocked();
                    } catch (RuntimeException | Error error) {
                        Log.e(TAG, "Explicit zero send failed; releasing control", error);
                        sendFailed = true;
                    }
                }
            }
        }
        if (sendFailed) {
            emergencyStop();
            return false;
        }
        return true;
    }

    /** Any validly-sequenced command proves the PC link is alive. */
    public void touchKeepalive() {
        synchronized (lock) {
            if (armed) {
                lastHeartbeatMs = SystemClock.elapsedRealtime();
            }
        }
    }

    /** Called when a new PC client connects so its numbering restarts. */
    public void resetSequence() {
        synchronized (lock) {
            lastSequence = -1;
        }
    }

    public void disarm(@NonNull ResultCallback callback) {
        final long releaseEpoch;
        VirtualStickFlightControlParam finalZero = null;
        synchronized (lock) {
            epoch++;
            releaseEpoch = epoch;
            zeroLocked();
            if (armed && usesAdvancedMode(stickMode)) {
                // Capture a final zero while holding the state lock, but do
                // not call the SDK from inside teardown: a runtime exception
                // must never leave armed=true or skip disableVirtualStick().
                finalZero = buildAdvancedParamLocked();
            }
            armed = false;
            enabling = false;
            stopSenderLocked();
        }
        // stopSenderLocked() may have interrupted this thread.
        Thread.interrupted();
        if (finalZero != null) {
            try {
                VirtualStickManager.getInstance().sendVirtualStickAdvancedParam(finalZero);
            } catch (RuntimeException error) {
                Log.e(TAG, "Final zero frame failed; continuing with release", error);
            }
        }
        try {
            VirtualStickManager.getInstance().disableVirtualStick(
                    new CommonCallbacks.CompletionCallback() {
                        @Override
                        public void onSuccess() {
                            callback.onResult(true, "disarmed");
                        }

                        @Override
                        public void onFailure(@NonNull IDJIError error) {
                            callback.onResult(false, error.toString());
                            scheduleRelease(releaseEpoch, 2);
                        }
                    });
        } catch (RuntimeException error) {
            Log.e(TAG, "Initial Virtual Stick release threw", error);
            callback.onResult(false, "disable_virtual_stick_threw");
            scheduleRelease(releaseEpoch, 2);
        }
    }

    public void emergencyStop() {
        disarm((success, detail) ->
                Log.w(TAG, "Emergency stop: success=" + success + ", detail=" + detail));
    }

    public boolean isArmed() {
        synchronized (lock) {
            return armed;
        }
    }

    public boolean isArmedOrEnabling() {
        synchronized (lock) {
            return armed || enabling;
        }
    }

    public long heartbeatAgeMs() {
        synchronized (lock) {
            if (lastHeartbeatMs == 0) {
                return Long.MAX_VALUE;
            }
            return SystemClock.elapsedRealtime() - lastHeartbeatMs;
        }
    }

    @NonNull
    public JSONObject stateJson() {
        JSONObject json = new JSONObject();
        try {
            StickMode modeSnapshot;
            long commandSequenceSnapshot;
            long commandAcceptedSnapshot;
            synchronized (lock) {
                modeSnapshot = stickMode;
                commandSequenceSnapshot = activeCommandSequence;
                commandAcceptedSnapshot = activeCommandAcceptedMs;
                json.put("requested_forward_mps", requestedForwardMps);
                json.put("requested_right_mps", requestedRightMps);
                json.put("requested_up_mps", requestedUpMps);
                json.put("requested_yaw_rate_dps", requestedYawRateDps);
                json.put("requested_forward_tilt_deg", requestedForwardTiltDeg);
                json.put("requested_right_tilt_deg", requestedRightTiltDeg);
                json.put("setpoint_forward_mps", forwardMps);
                json.put("setpoint_right_mps", rightMps);
                json.put("setpoint_up_mps", upMps);
                json.put("setpoint_yaw_rate_dps", yawRateDps);
                json.put("setpoint_forward_tilt_deg", forwardTiltDeg);
                json.put("setpoint_right_tilt_deg", rightTiltDeg);
                if (modeSnapshot == StickMode.OFFICIAL_ADVANCED_ANGLE) {
                    json.put("sdk_roll", rightTiltDeg);
                    json.put("sdk_pitch", -forwardTiltDeg);
                    json.put("sdk_roll_pitch_units", "degrees");
                    json.put("sdk_roll_pitch_mode", "ANGLE");
                } else {
                    json.put("sdk_roll", forwardMps);
                    json.put("sdk_pitch", rightMps);
                    json.put("sdk_roll_pitch_units", "m/s");
                    json.put("sdk_roll_pitch_mode", "VELOCITY");
                }
            }
            json.put("stick_mode", modeSnapshot.name());
            json.put("control_path", modeSnapshot.name());
            if (commandSequenceSnapshot >= 0) {
                json.put("active_command_sequence", commandSequenceSnapshot);
                json.put("active_command_age_ms", SystemClock.elapsedRealtime()
                        - commandAcceptedSnapshot);
            }
            // Time-based heartbeat zero/release is intentionally disabled for
            // this diagnostic build. Physical RC override and an actual TCP
            // disconnect still release Virtual Stick immediately.
            json.put("time_watchdog_enabled", false);
            json.put("disconnect_release_enabled", true);
            RcOverrideMonitor rc = RcOverrideMonitor.getInstance();
            json.put("rc_override_threshold", RcOverrideMonitor.OVERRIDE_THRESHOLD);
            putIfNotNull(json, "rc_stick_left_vertical", rc.leftVerticalValue());
            putIfNotNull(json, "rc_stick_left_horizontal", rc.leftHorizontalValue());
            putIfNotNull(json, "rc_stick_right_vertical", rc.rightVerticalValue());
            putIfNotNull(json, "rc_stick_right_horizontal", rc.rightHorizontalValue());
            json.put("oa_sensors_working", ObstacleAvoidanceController.workingSensors());
            json.put("oa_type", ObstacleAvoidanceController.avoidanceType());
            json.put("oa_horizontal_switch_support",
                    ObstacleAvoidanceController.horizontalSwitchSupport());
            json.put("oa_upward_switch_support",
                    ObstacleAvoidanceController.upwardSwitchSupport());
            json.put("oa_horizontal_enabled",
                    ObstacleAvoidanceController.horizontalAvoidanceEnabled());
            json.put("oa_upward_enabled",
                    ObstacleAvoidanceController.upwardAvoidanceEnabled());
            json.put("oa_downward_enabled",
                    ObstacleAvoidanceController.downwardAvoidanceEnabled());
            json.put("vision_positioning_enabled",
                    ObstacleAvoidanceController.visionPositioningEnabled());
            int[] obstacleDistances =
                    ObstacleAvoidanceController.horizontalObstacleDistancesMm();
            JSONArray obstacleMatrix = new JSONArray();
            for (int distance : obstacleDistances) {
                obstacleMatrix.put(distance);
            }
            json.put("oa_horizontal_angle_interval_deg",
                    ObstacleAvoidanceController.horizontalAngleIntervalDeg());
            json.put("oa_horizontal_distances_mm", obstacleMatrix);
            json.put("oa_horizontal_sample_count", obstacleDistances.length);
            int upwardDistance = ObstacleAvoidanceController.upwardObstacleDistanceMm();
            int downwardDistance = ObstacleAvoidanceController.downwardObstacleDistanceMm();
            if (upwardDistance >= 0) {
                json.put("oa_upward_distance_mm", upwardDistance);
            }
            if (downwardDistance >= 0) {
                json.put("oa_downward_distance_mm", downwardDistance);
            }
            long obstacleAge = ObstacleAvoidanceController.obstacleDataAgeMs();
            if (obstacleAge >= 0) {
                json.put("oa_obstacle_data_age_ms", obstacleAge);
            }
            json.put("direct_frames_sent", directFramesSent.get());
            json.put("direct_frames_succeeded", directFramesSucceeded.get());
            json.put("direct_frames_failed", directFramesFailed.get());
            json.put("direct_consecutive_failures", consecutiveDirectFailures.get());
            if (lastDirectCallbackMs > 0) {
                json.put("direct_callback_age_ms",
                        SystemClock.elapsedRealtime() - lastDirectCallbackMs);
            }
            if (lastDirectError != null) {
                json.put("direct_last_error", lastDirectError);
            }
            json.put("official_advanced_frames_sent", officialAdvancedFramesSent.get());
            if (lastOfficialAdvancedFrameMs > 0) {
                json.put("official_advanced_frame_age_ms",
                        SystemClock.elapsedRealtime() - lastOfficialAdvancedFrameMs);
            }
            json.put("sdk_submit_result", lastSdkSubmitResult);
            if (lastSdkSubmitSequence >= 0) {
                json.put("sdk_submit_sequence", lastSdkSubmitSequence);
                json.put("sdk_submit_frame", lastSdkSubmitFrame);
                json.put("sdk_submit_age_ms",
                        SystemClock.elapsedRealtime() - lastSdkSubmitMs);
            }
            json.put("sdk_result", lastSdkResult);
            if (lastSdkResultSequence >= 0) {
                json.put("sdk_result_sequence", lastSdkResultSequence);
                json.put("sdk_result_frame", lastSdkResultFrame);
                json.put("sdk_result_age_ms",
                        SystemClock.elapsedRealtime() - lastSdkResultMs);
            }
            if (lastSdkResultError != null) {
                json.put("sdk_result_error", lastSdkResultError);
            }
            try {
                json.put("speed_level", VirtualStickManager.getInstance().getSpeedLevel());
            } catch (RuntimeException ignored) {
                // Reported only when the SDK can answer.
            }
            if (vsEnabled != null) {
                json.put("vs_enabled", vsEnabled.booleanValue());
            }
            if (vsAdvancedEnabled != null) {
                json.put("vs_advanced_enabled", vsAdvancedEnabled.booleanValue());
            }
            if (vsAuthorityOwner != null) {
                json.put("vs_authority", vsAuthorityOwner);
            }
            if (vsChangeReason != null) {
                json.put("vs_change_reason", vsChangeReason);
            }
        } catch (JSONException ignored) {
            // Constant keys with primitive values.
        }
        return json;
    }

    private static void putIfNotNull(
            @NonNull JSONObject json, @NonNull String key, Object value)
            throws JSONException {
        if (value != null) {
            json.put(key, value);
        }
    }

    private void releaseVirtualStick(long releaseEpoch, int attempt) {
        synchronized (lock) {
            if (epoch != releaseEpoch || armed || enabling) {
                return;
            }
        }
        try {
            VirtualStickManager.getInstance().disableVirtualStick(
                    new CommonCallbacks.CompletionCallback() {
                        @Override
                        public void onSuccess() {
                            Log.i(TAG, "Virtual Stick released (attempt " + attempt + ")");
                        }

                        @Override
                        public void onFailure(@NonNull IDJIError error) {
                            String text = error.toString();
                            // "no control authority" means Virtual Stick is
                            // already not ours - there is nothing left to
                            // release, so retrying is pointless and spins
                            // forever. Same once the aircraft is mid-takeoff.
                            if (text.contains("CONTROL_AUTH_HAS_NO_CONTROL_AUTH")) {
                                Log.i(TAG, "Release unnecessary: control already with the RC");
                                return;
                            }
                            if (attempt >= MAX_RELEASE_ATTEMPTS) {
                                Log.e(TAG, "Giving up releasing after "
                                        + attempt + " attempts: " + text);
                                return;
                            }
                            Log.e(TAG, "Release failed (" + attempt + "): " + text);
                            scheduleRelease(releaseEpoch, attempt + 1);
                        }
                    });
        } catch (RuntimeException error) {
            Log.e(TAG, "Release threw (" + attempt + ")", error);
            if (attempt < MAX_RELEASE_ATTEMPTS) {
                scheduleRelease(releaseEpoch, attempt + 1);
            }
        }
    }

    private void scheduleRelease(long releaseEpoch, int attempt) {
        releaseExecutor.schedule(
                () -> releaseVirtualStick(releaseEpoch, attempt),
                RELEASE_RETRY_MS, TimeUnit.MILLISECONDS);
    }

    private void startSenderLocked() {
        stopSenderLocked();
        sender = Executors.newSingleThreadScheduledExecutor(runnable -> {
            Thread thread = new Thread(runnable, "stick-20hz");
            thread.setDaemon(true);
            return thread;
        });
        sender.scheduleAtFixedRate(this::tick, 0, 50, TimeUnit.MILLISECONDS);
    }

    private void stopSenderLocked() {
        if (sender != null) {
            sender.shutdownNow();
            sender = null;
        }
    }

    /**
     * scheduleAtFixedRate suppresses all future runs after a throw, which
     * would kill the sender while armed stayed true, so any throw releases
     * control to the RC.
     */
    private void tick() {
        try {
            tickBody();
        } catch (Throwable error) {
            Log.e(TAG, "tick threw; releasing control to RC", error);
            long releaseEpoch;
            synchronized (lock) {
                epoch++;
                releaseEpoch = epoch;
                zeroLocked();
                armed = false;
                enabling = false;
                stopSenderLocked();
            }
            Thread.interrupted();
            releaseVirtualStick(releaseEpoch, 1);
        }
    }

    private void tickBody() {
        boolean disable = false;
        long releaseEpoch = 0;
        synchronized (lock) {
            if (!armed) {
                return;
            }
            boolean expectAdvanced = usesAdvancedMode(stickMode);
            boolean stateMismatch = vsAdvancedEnabled == null
                    || vsAdvancedEnabled.booleanValue() != expectAdvanced;
            boolean graceExpired = SystemClock.elapsedRealtime() - modeRequestedMs
                    > MODE_STATE_GRACE_MS;
            if (stateMismatch && graceExpired) {
                Log.e(TAG, "Virtual Stick mode changed unexpectedly; expectedAdvanced="
                        + expectAdvanced + "; releasing to RC");
                epoch++;
                releaseEpoch = epoch;
                zeroLocked();
                if (usesAdvancedMode(stickMode)) {
                    sendCurrentLocked();
                }
                armed = false;
                stopSenderLocked();
                Thread.interrupted();
                disable = true;
            } else {
                // No time-based heartbeat zero or release in this diagnostic
                // build. The latest setpoint remains active until a newer
                // command, explicit zero/disarm, physical RC override, actual
                // client disconnect, mode mismatch, or sender failure.
                sendCurrentLocked();
            }
        }
        if (disable) {
            Log.w(TAG, stickMode + " control stopped: releasing control to RC");
            releaseVirtualStick(releaseEpoch, 1);
        }
    }

    private void sendCurrentLocked() {
        switch (stickMode) {
            case OFFICIAL_ADVANCED:
            case OFFICIAL_ADVANCED_ANGLE:
                sendOfficialAdvancedLocked();
                break;
            case ADVANCED_DIRECT:
                sendAdvancedDirectLocked();
                break;
            case BASIC:
            default:
                sendBasicLocked();
                break;
        }
    }

    private void sendBasicLocked() {
        IStick left = VirtualStickManager.getInstance().getLeftStick();
        IStick right = VirtualStickManager.getInstance().getRightStick();
        int lv = position(upMps, VERTICAL_FULL_SCALE);
        int lh = position(yawRateDps, YAW_FULL_SCALE);
        int rv = position(forwardMps, HORIZONTAL_FULL_SCALE);
        int rh = position(rightMps, HORIZONTAL_FULL_SCALE);
        left.setVerticalPosition(lv);
        left.setHorizontalPosition(lh);
        right.setVerticalPosition(rv);
        right.setHorizontalPosition(rh);
        // 1 Hz: proves what actually reaches the SDK, so "commanded but the
        // aircraft ignored it" can be told apart from "we sent zero".
        if (++sendTicks % 20 == 0) {
            Log.i(TAG, "sticks L(v=" + lv + ",h=" + lh + ") R(v=" + rv
                    + ",h=" + rh + ")  cmd fwd=" + forwardMps + " right="
                    + rightMps + " up=" + upMps
                    + "  readback R.h=" + right.getHorizontalPosition()
                    + " R.v=" + right.getVerticalPosition()
                    + " OA=" + ObstacleAvoidanceController.avoidanceType()
                    + " sensors=" + ObstacleAvoidanceController.workingSensors()
                    + " " + TelemetryProvider.getInstance().summaryForLog());
        }
    }

    /** DJI's official V5 sample path, continuously refreshed by our 20 Hz loop. */
    private void sendOfficialAdvancedLocked() {
        VirtualStickFlightControlParam param = buildAdvancedParamLocked();
        VirtualStickManager.getInstance().sendVirtualStickAdvancedParam(param);
        long frame = officialAdvancedFramesSent.incrementAndGet();
        long now = SystemClock.elapsedRealtime();
        lastOfficialAdvancedFrameMs = now;
        lastSdkSubmitSequence = activeCommandSequence;
        lastSdkSubmitFrame = frame;
        lastSdkSubmitMs = now;
        // DJI's official manager API returns void and exposes no per-frame
        // completion callback. State and measured motion are the response.
        lastSdkSubmitResult = "SUBMITTED_OFFICIAL_NO_CALLBACK";
        lastSdkResult = "NOT_AVAILABLE_ON_OFFICIAL_MANAGER_API";
        lastSdkResultSequence = activeCommandSequence;
        lastSdkResultFrame = frame;
        lastSdkResultMs = now;
        lastSdkResultError = null;

        if (++sendTicks % 20 == 0) {
            String horizontal = stickMode == StickMode.OFFICIAL_ADVANCED_ANGLE
                    ? "tilt fwd=" + forwardTiltDeg + "deg right=" + rightTiltDeg + "deg"
                    : "velocity fwd=" + forwardMps + "m/s right=" + rightMps + "m/s";
            Log.i(TAG, "official advanced " + horizontal
                    + " up=" + upMps + "m/s yaw=" + yawRateDps + "deg/s"
                    + " frames=" + officialAdvancedFramesSent.get()
                    + " OA=" + ObstacleAvoidanceController.avoidanceType()
                    + " sensors=" + ObstacleAvoidanceController.workingSensors()
                    + " " + TelemetryProvider.getInstance().summaryForLog());
        }
    }

    /**
     * Callback-visible diagnostic FC action. This is not the production path.
     * In BODY/VELOCITY, DJI maps forward to roll and right to pitch.
     */
    private void sendAdvancedDirectLocked() {
        VirtualStickFlightControlParam param = buildAdvancedParamLocked();
        long frame = directFramesSent.incrementAndGet();
        long commandSequence = activeCommandSequence;
        long submittedAt = SystemClock.elapsedRealtime();
        lastSdkSubmitSequence = commandSequence;
        lastSdkSubmitFrame = frame;
        lastSdkSubmitMs = submittedAt;
        lastSdkSubmitResult = "SUBMITTED_DIRECT_WITH_CALLBACK";
        try {
            KeyManager.getInstance().performAction(
                    vsSendKey,
                    param,
                    new CommonCallbacks.CompletionCallbackWithParam<EmptyMsg>() {
                        @Override
                        public void onSuccess(EmptyMsg ignored) {
                            directFramesSucceeded.incrementAndGet();
                            consecutiveDirectFailures.set(0);
                            lastDirectCallbackMs = SystemClock.elapsedRealtime();
                            lastDirectError = null;
                            lastSdkResultSequence = commandSequence;
                            lastSdkResultFrame = frame;
                            lastSdkResultMs = lastDirectCallbackMs;
                            lastSdkResult = "SUCCEEDED";
                            lastSdkResultError = null;
                        }

                        @Override
                        public void onFailure(@NonNull IDJIError error) {
                            directFramesFailed.incrementAndGet();
                            int consecutive = consecutiveDirectFailures.incrementAndGet();
                            lastDirectCallbackMs = SystemClock.elapsedRealtime();
                            lastDirectError = error.toString();
                            lastSdkResultSequence = commandSequence;
                            lastSdkResultFrame = frame;
                            lastSdkResultMs = lastDirectCallbackMs;
                            lastSdkResult = "FAILED";
                            lastSdkResultError = error.toString();
                            Log.e(TAG, "Direct VS frame " + frame + " rejected (consecutive="
                                    + consecutive + "): " + error);
                            if (consecutive == 5 && isArmed()) {
                                Log.e(TAG, "Five consecutive direct frame failures; releasing RC");
                                emergencyStop();
                            }
                        }
                    });
        } catch (RuntimeException error) {
            directFramesFailed.incrementAndGet();
            int consecutive = consecutiveDirectFailures.incrementAndGet();
            lastDirectCallbackMs = SystemClock.elapsedRealtime();
            lastDirectError = error.toString();
            lastSdkResultSequence = commandSequence;
            lastSdkResultFrame = frame;
            lastSdkResultMs = lastDirectCallbackMs;
            lastSdkResult = "THREW";
            lastSdkResultError = error.toString();
            throw error;
        }

        if (++sendTicks % 20 == 0) {
            Log.i(TAG, "direct diagnostic cmd fwd=" + forwardMps + " right=" + rightMps
                    + " up=" + upMps + " yaw=" + yawRateDps
                    + " frames=" + directFramesSent.get() + "/"
                    + directFramesSucceeded.get() + "/" + directFramesFailed.get()
                    + " consecutive_fail=" + consecutiveDirectFailures.get()
                    + " OA=" + ObstacleAvoidanceController.avoidanceType()
                    + " sensors=" + ObstacleAvoidanceController.workingSensors()
                    + " " + TelemetryProvider.getInstance().summaryForLog());
        }
    }

    private VirtualStickFlightControlParam buildAdvancedParamLocked() {
        VirtualStickFlightControlParam param = new VirtualStickFlightControlParam();
        param.setRollPitchCoordinateSystem(FlightCoordinateSystem.BODY);
        param.setVerticalControlMode(VerticalControlMode.VELOCITY);
        param.setYawControlMode(YawControlMode.ANGULAR_VELOCITY);
        if (stickMode == StickMode.OFFICIAL_ADVANCED_ANGLE) {
            param.setRollPitchControlMode(RollPitchControlMode.ANGLE);
            // DJI's official coordinate table: in BODY+ANGLE, positive Pitch
            // moves backward and positive Roll moves right.
            param.setPitch(-forwardTiltDeg);
            param.setRoll(rightTiltDeg);
        } else {
            param.setRollPitchControlMode(RollPitchControlMode.VELOCITY);
            // DJI BODY+VELOCITY: Roll is body X (forward), Pitch is body Y
            // (right). This intentionally differs from ANGLE mode above.
            param.setRoll(forwardMps);
            param.setPitch(rightMps);
        }
        param.setVerticalThrottle(upMps);
        param.setYaw(yawRateDps);
        return param;
    }

    private void resetSendDiagnostics() {
        officialAdvancedFramesSent.set(0);
        lastOfficialAdvancedFrameMs = 0;
        directFramesSent.set(0);
        directFramesSucceeded.set(0);
        directFramesFailed.set(0);
        consecutiveDirectFailures.set(0);
        lastDirectCallbackMs = 0;
        lastDirectError = null;
        lastSdkSubmitSequence = -1;
        lastSdkSubmitFrame = -1;
        lastSdkSubmitMs = 0;
        lastSdkSubmitResult = "NOT_SUBMITTED";
        lastSdkResultSequence = -1;
        lastSdkResultFrame = -1;
        lastSdkResultMs = 0;
        lastSdkResult = "NOT_AVAILABLE";
        lastSdkResultError = null;
    }

    private static boolean usesAdvancedMode(StickMode mode) {
        return mode != StickMode.BASIC;
    }

    private long sendTicks = 0;

    private void centreSticks() {
        IStick left = VirtualStickManager.getInstance().getLeftStick();
        IStick right = VirtualStickManager.getInstance().getRightStick();
        left.setVerticalPosition(0);
        left.setHorizontalPosition(0);
        right.setVerticalPosition(0);
        right.setHorizontalPosition(0);
    }

    /** Inverse of value = speedLevel * fullScale * position / 660. */
    private static int position(double value, double fullScale) {
        double effective = fullScale * BASIC_SPEED_LEVEL;
        long pos = Math.round(value / effective * STICK_RANGE);
        return (int) Math.max(-STICK_RANGE, Math.min(STICK_RANGE, pos));
    }

    private void zeroLocked() {
        requestedForwardMps = 0.0;
        requestedRightMps = 0.0;
        requestedUpMps = 0.0;
        requestedYawRateDps = 0.0;
        requestedForwardTiltDeg = 0.0;
        requestedRightTiltDeg = 0.0;
        forwardMps = 0.0;
        rightMps = 0.0;
        forwardTiltDeg = 0.0;
        rightTiltDeg = 0.0;
        upMps = 0.0;
        yawRateDps = 0.0;
        // Stick positions are sticky state, not per-frame messages: they must
        // be actively centred or the last deflection keeps applying.
        try {
            centreSticks();
        } catch (RuntimeException error) {
            Log.w(TAG, "Failed to centre sticks", error);
        }
    }

    private static boolean allFinite(double... values) {
        for (double value : values) {
            if (!Double.isFinite(value)) {
                return false;
            }
        }
        return true;
    }

    private static double clamp(double value, double limit) {
        return Math.max(-limit, Math.min(limit, value));
    }
}
