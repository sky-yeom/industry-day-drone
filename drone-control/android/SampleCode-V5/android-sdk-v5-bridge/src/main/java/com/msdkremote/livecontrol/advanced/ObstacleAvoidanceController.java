package com.msdkremote.livecontrol.advanced;

import android.os.SystemClock;
import android.util.Log;

import androidx.annotation.NonNull;

import dji.v5.common.callback.CommonCallbacks;
import dji.v5.common.error.IDJIError;
import dji.v5.manager.aircraft.perception.PerceptionManager;
import dji.v5.manager.aircraft.perception.data.ObstacleData;
import dji.v5.manager.aircraft.perception.data.ObstacleAvoidanceType;
import dji.v5.manager.aircraft.perception.data.PerceptionInfo;
import dji.v5.manager.aircraft.perception.data.PerceptionDirection;
import dji.v5.manager.aircraft.perception.listener.ObstacleDataListener;
import dji.v5.manager.aircraft.perception.listener.PerceptionInformationListener;

import java.util.List;

/**
 * Public obstacle-avoidance settings and observations, not firmware bypass.
 *
 * The PC used to try this with "SET FlightController VisionAvoidEnable false".
 * Those keys are not in the public API and the aircraft refuses to set them
 * ("Cannot command 'SET' on key"), so avoidance stayed active and quietly
 * braked commanded motion - the aircraft accepted every Virtual Stick command
 * and then refused to translate. setObstacleAvoidanceType is the interface DJI
 * actually exposes for this.
 *
 * A successful CLOSE request does not prove that all firmware constraints
 * are disabled. A working sensor flag is not an active braking event.
 */
public final class ObstacleAvoidanceController {
    private static final String TAG = "ObstacleAvoidance";

    // These flags mean that a direction's vision sensor is operating. DJI's
    // API does NOT say that the aircraft is actively braking, so do not label
    // or interpret them as a brake event.
    private static volatile String workingSensors = "";
    private static volatile String avoidanceType = "UNKNOWN";
    // Some aircraft (confirmed on Mini 4 Pro) expose the global CLOSE mode but
    // reject the separate HORIZONTAL switch as COMMON/UNSUPPORTED. Keep that
    // capability result distinct from the live enabled state so callers do not
    // mistake an unsupported product API for a failed CLOSE request.
    private static volatile String horizontalSwitchSupport = "UNKNOWN";
    private static volatile String upwardSwitchSupport = "UNKNOWN";
    private static volatile boolean horizontalAvoidanceEnabled = false;
    private static volatile boolean upwardAvoidanceEnabled = false;
    private static volatile boolean downwardAvoidanceEnabled = false;
    private static volatile boolean visionPositioningEnabled = false;
    private static volatile int horizontalAngleIntervalDeg = 0;
    private static volatile int[] horizontalObstacleDistancesMm = new int[0];
    private static volatile int upwardObstacleDistanceMm = -1;
    private static volatile int downwardObstacleDistanceMm = -1;
    private static volatile long obstacleDataUpdatedMs = 0;
    private static boolean listenerStarted = false;

    public interface ResultCallback {
        void onResult(boolean success, String detail);
    }

    private ObstacleAvoidanceController() {
    }

    /** Comma-separated directions whose vision sensors report working. */
    public static String workingSensors() {
        return workingSensors;
    }

    public static String avoidanceType() {
        return avoidanceType;
    }

    public static String horizontalSwitchSupport() {
        return horizontalSwitchSupport;
    }

    public static String upwardSwitchSupport() {
        return upwardSwitchSupport;
    }

    public static boolean horizontalAvoidanceEnabled() {
        return horizontalAvoidanceEnabled;
    }

    public static boolean upwardAvoidanceEnabled() {
        return upwardAvoidanceEnabled;
    }

    public static boolean downwardAvoidanceEnabled() {
        return downwardAvoidanceEnabled;
    }

    public static boolean visionPositioningEnabled() {
        return visionPositioningEnabled;
    }

    public static int horizontalAngleIntervalDeg() {
        return horizontalAngleIntervalDeg;
    }

    /** Raw 360-degree aircraft obstacle matrix, in DJI's published order. */
    public static int[] horizontalObstacleDistancesMm() {
        return horizontalObstacleDistancesMm.clone();
    }

    public static int upwardObstacleDistanceMm() {
        return upwardObstacleDistanceMm;
    }

    public static int downwardObstacleDistanceMm() {
        return downwardObstacleDistanceMm;
    }

    public static long obstacleDataAgeMs() {
        long updated = obstacleDataUpdatedMs;
        return updated <= 0 ? -1 : SystemClock.elapsedRealtime() - updated;
    }

    /** Subscribe once so ACKs can carry sensor working states and ranges. */
    public static void startPerceptionListener() {
        synchronized (ObstacleAvoidanceController.class) {
            if (listenerStarted) {
                return;
            }
            listenerStarted = true;
        }
        try {
            PerceptionManager.getInstance().addPerceptionInformationListener(
                    new PerceptionInformationListener() {
                        @Override
                        public void onUpdate(@NonNull PerceptionInfo info) {
                            ObstacleAvoidanceType liveType = info.getObstacleAvoidanceType();
                            avoidanceType = liveType == null ? "UNKNOWN" : liveType.name();
                            StringBuilder active = new StringBuilder();
                            append(active, "left", info.getLeftSideObstacleAvoidanceWorking());
                            append(active, "right", info.getRightSideObstacleAvoidanceWorking());
                            append(active, "fwd", info.getForwardObstacleAvoidanceWorking());
                            append(active, "back", info.getBackwardObstacleAvoidanceWorking());
                            append(active, "up", info.getUpwardObstacleAvoidanceWorking());
                            append(active, "down", info.getDownwardObstacleAvoidanceWorking());
                            horizontalAvoidanceEnabled =
                                    info.isHorizontalObstacleAvoidanceEnabled();
                            upwardAvoidanceEnabled = info.isUpwardObstacleAvoidanceEnabled();
                            downwardAvoidanceEnabled = info.isDownwardObstacleAvoidanceEnabled();
                            visionPositioningEnabled = info.isVisionPositioningEnabled();
                            String now = active.toString();
                            if (!now.equals(workingSensors)) {
                                Log.i(TAG, "working vision sensors: "
                                        + (now.isEmpty() ? "(none)" : now));
                            }
                            workingSensors = now;
                        }
                    });
            PerceptionManager.getInstance().addObstacleDataListener(
                    new ObstacleDataListener() {
                        @Override
                        public void onUpdate(@NonNull ObstacleData data) {
                            horizontalAngleIntervalDeg = data.getHorizontalAngleInterval();
                            List<Integer> values = data.getHorizontalObstacleDistance();
                            if (values == null || values.isEmpty()) {
                                horizontalObstacleDistancesMm = new int[0];
                            } else {
                                int[] snapshot = new int[values.size()];
                                for (int i = 0; i < values.size(); i++) {
                                    Integer value = values.get(i);
                                    snapshot[i] = value == null ? -1 : value;
                                }
                                horizontalObstacleDistancesMm = snapshot;
                            }
                            upwardObstacleDistanceMm = data.getUpwardObstacleDistance();
                            downwardObstacleDistanceMm = data.getDownwardObstacleDistance();
                            obstacleDataUpdatedMs = SystemClock.elapsedRealtime();
                        }
                    });
            Log.i(TAG, "perception listener started");
        } catch (RuntimeException error) {
            Log.e(TAG, "perception listener failed", error);
        }
    }

    private static void append(StringBuilder out, String name, Boolean working) {
        if (working != null && working) {
            if (out.length() > 0) {
                out.append(',');
            }
            out.append(name);
        }
    }

    /**
     * Turn horizontal and upward braking/bypass off through the documented
     * controls, then read each supported value back. CLOSE alone is not
     * sufficient evidence: Mini 4 Pro can report CLOSE while a directional
     * sub-switch remains on.
     * Downward perception is deliberately left enabled for indoor positioning
     * and precision landing.
     */
    public static void setClose(@NonNull ResultCallback callback) {
        horizontalSwitchSupport = "UNKNOWN";
        upwardSwitchSupport = "UNKNOWN";
        try {
            PerceptionManager.getInstance().setObstacleAvoidanceType(
                    ObstacleAvoidanceType.CLOSE,
                    new CommonCallbacks.CompletionCallback() {
                        @Override
                        public void onSuccess() {
                            disableHorizontal(callback);
                        }

                        @Override
                        public void onFailure(@NonNull IDJIError error) {
                            Log.w(TAG, "setObstacleAvoidanceType failed: " + error);
                            callback.onResult(false, error.toString());
                        }
                    });
        } catch (RuntimeException error) {
            Log.e(TAG, "setObstacleAvoidanceType threw", error);
            callback.onResult(false, "threw: " + error);
        }
    }

    private static void disableHorizontal(@NonNull ResultCallback callback) {
        try {
            PerceptionManager.getInstance().setObstacleAvoidanceEnabled(
                    false,
                    PerceptionDirection.HORIZONTAL,
                    new CommonCallbacks.CompletionCallback() {
                        @Override
                        public void onSuccess() {
                            horizontalSwitchSupport = "SUPPORTED";
                            disableUpward(callback);
                        }

                        @Override
                        public void onFailure(@NonNull IDJIError error) {
                            Log.w(TAG, "horizontal OA disable failed: " + error);
                            if (isUnsupported(error)) {
                                horizontalSwitchSupport = "UNSUPPORTED";
                                Log.w(TAG, "aircraft does not expose the horizontal OA "
                                        + "sub-switch; verifying global CLOSE instead");
                                disableUpward(callback);
                            } else {
                                callback.onResult(false,
                                        "oa_close_ok_horizontal_disable_failed: " + error);
                            }
                        }
                    });
        } catch (RuntimeException error) {
            Log.e(TAG, "horizontal OA disable threw", error);
            callback.onResult(false,
                    "oa_close_ok_horizontal_disable_threw: " + error);
        }
    }

    private static void disableUpward(@NonNull ResultCallback callback) {
        try {
            PerceptionManager.getInstance().setObstacleAvoidanceEnabled(
                    false,
                    PerceptionDirection.UPWARD,
                    new CommonCallbacks.CompletionCallback() {
                        @Override
                        public void onSuccess() {
                            upwardSwitchSupport = "SUPPORTED";
                            readBack(callback);
                        }

                        @Override
                        public void onFailure(@NonNull IDJIError error) {
                            Log.w(TAG, "upward OA disable failed: " + error);
                            if (isUnsupported(error)) {
                                upwardSwitchSupport = "UNSUPPORTED";
                                Log.w(TAG, "aircraft does not expose the upward OA "
                                        + "sub-switch; verifying global CLOSE instead");
                                readBack(callback);
                            } else {
                                callback.onResult(false,
                                        "oa_close_ok_upward_disable_failed: " + error);
                            }
                        }
                    });
        } catch (RuntimeException error) {
            Log.e(TAG, "upward OA disable threw", error);
            callback.onResult(false,
                    "oa_close_ok_upward_disable_threw: " + error);
        }
    }

    private static void readBack(@NonNull ResultCallback callback) {
        try {
            PerceptionManager.getInstance().getObstacleAvoidanceType(
                    new CommonCallbacks.CompletionCallbackWithParam<ObstacleAvoidanceType>() {
                        @Override
                        public void onSuccess(ObstacleAvoidanceType value) {
                            String name = value == null ? "null" : value.name();
                            avoidanceType = name;
                            boolean typeClosed = value == ObstacleAvoidanceType.CLOSE;
                            if ("UNSUPPORTED".equals(horizontalSwitchSupport)) {
                                readBackUpward(
                                        typeClosed, name,
                                        horizontalAvoidanceEnabled, callback);
                            } else {
                                readBackHorizontal(typeClosed, name, callback);
                            }
                        }

                        @Override
                        public void onFailure(@NonNull IDJIError error) {
                            callback.onResult(false,
                                    "oa_type_readback_failed: " + error);
                        }
                    });
        } catch (RuntimeException error) {
            callback.onResult(false, "oa_type_readback_threw: " + error);
        }
    }

    private static void readBackHorizontal(
            boolean typeClosed,
            @NonNull String typeName,
            @NonNull ResultCallback callback) {
        try {
            PerceptionManager.getInstance().getObstacleAvoidanceEnabled(
                    PerceptionDirection.HORIZONTAL,
                    new CommonCallbacks.CompletionCallbackWithParam<Boolean>() {
                        @Override
                        public void onSuccess(Boolean enabled) {
                            boolean horizontalEnabled = Boolean.TRUE.equals(enabled);
                            horizontalAvoidanceEnabled = horizontalEnabled;
                            readBackUpward(
                                    typeClosed, typeName,
                                    horizontalEnabled, callback);
                        }

                        @Override
                        public void onFailure(@NonNull IDJIError error) {
                            callback.onResult(false,
                                    "horizontal_oa_readback_failed: " + error);
                        }
                    });
        } catch (RuntimeException error) {
            callback.onResult(false,
                    "horizontal_oa_readback_threw: " + error);
        }
    }

    private static void readBackUpward(
            boolean typeClosed,
            @NonNull String typeName,
            boolean horizontalEnabled,
            @NonNull ResultCallback callback) {
        if ("UNSUPPORTED".equals(upwardSwitchSupport)) {
            finishReadBack(
                    typeClosed, typeName, horizontalEnabled,
                    upwardAvoidanceEnabled, callback);
            return;
        }
        try {
            PerceptionManager.getInstance().getObstacleAvoidanceEnabled(
                    PerceptionDirection.UPWARD,
                    new CommonCallbacks.CompletionCallbackWithParam<Boolean>() {
                        @Override
                        public void onSuccess(Boolean enabled) {
                            boolean upwardEnabled = Boolean.TRUE.equals(enabled);
                            upwardAvoidanceEnabled = upwardEnabled;
                            finishReadBack(
                                    typeClosed, typeName, horizontalEnabled,
                                    upwardEnabled, callback);
                        }

                        @Override
                        public void onFailure(@NonNull IDJIError error) {
                            callback.onResult(false,
                                    "upward_oa_readback_failed: " + error);
                        }
                    });
        } catch (RuntimeException error) {
            callback.onResult(false, "upward_oa_readback_threw: " + error);
        }
    }

    private static void finishReadBack(
            boolean typeClosed,
            @NonNull String typeName,
            boolean horizontalEnabled,
            boolean upwardEnabled,
            @NonNull ResultCallback callback) {
        String detail = "oa_type=" + typeName
                + ",horizontal_switch_support=" + horizontalSwitchSupport
                + ",horizontal_enabled=" + horizontalEnabled
                + ",upward_switch_support=" + upwardSwitchSupport
                + ",upward_enabled=" + upwardEnabled
                + ",downward_preserved=true";
        boolean horizontalVerified =
                "UNSUPPORTED".equals(horizontalSwitchSupport) || !horizontalEnabled;
        boolean upwardVerified =
                "UNSUPPORTED".equals(upwardSwitchSupport) || !upwardEnabled;
        boolean verified = typeClosed && horizontalVerified && upwardVerified;
        if (verified) {
            Log.i(TAG, "verified " + detail);
        } else {
            Log.w(TAG, "not verified " + detail);
        }
        callback.onResult(verified, detail);
    }

    private static boolean isUnsupported(@NonNull IDJIError error) {
        return String.valueOf(error).toUpperCase().contains("UNSUPPORTED");
    }
}
