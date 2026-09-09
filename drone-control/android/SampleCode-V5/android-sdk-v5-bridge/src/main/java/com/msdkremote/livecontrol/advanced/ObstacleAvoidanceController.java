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
import java.util.Map;
import java.util.LinkedHashMap;
import java.util.function.Consumer;
import org.json.JSONObject;
import com.msdkremote.PcBridge;
import com.msdkremote.lifecycle.PerceptionBinding;
import com.msdkremote.lifecycle.MaintenanceGate;
import dji.v5.manager.interfaces.IPerceptionManager;

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
    private static final PerceptionBinding binding=new PerceptionBinding(SystemClock::elapsedRealtime,
            PcBridge::connectionGeneration,PcBridge.processStartId());
    private static volatile long mutationGeneration;
    private static volatile String lastMutation="NONE";
    public static JSONObject snapshotJson() {
        JSONObject result=new JSONObject(binding.snapshot());
        try {result.put("oa_horizontal_switch_support",horizontalSwitchSupport);
            result.put("oa_upward_switch_support",upwardSwitchSupport);result.put("oa_last_mutation",lastMutation);}
        catch(org.json.JSONException ignored){}
        return result;
    }
    public static void stopPerceptionListener(){binding.stop();}


    public interface ResultCallback {
        void onResult(boolean success, String detail);
    }

    private ObstacleAvoidanceController() {
    }

    /** Comma-separated directions whose vision sensors report working. */
    public static String workingSensors() {
        Object v=binding.snapshot().get("oa_sensors_working");return v==null?"":v.toString();
    }

    public static String avoidanceType() {
        Object v=binding.snapshot().get("oa_type");return v==null?"UNKNOWN":v.toString();
    }

    public static String horizontalSwitchSupport() {
        return horizontalSwitchSupport;
    }

    public static String upwardSwitchSupport() {
        return upwardSwitchSupport;
    }

    public static boolean horizontalAvoidanceEnabled() {
        return Boolean.TRUE.equals(binding.snapshot().get("oa_horizontal_enabled"));
    }

    public static boolean upwardAvoidanceEnabled() {
        return Boolean.TRUE.equals(binding.snapshot().get("oa_upward_enabled"));
    }

    public static boolean downwardAvoidanceEnabled() {
        return Boolean.TRUE.equals(binding.snapshot().get("oa_downward_enabled"));
    }

    public static boolean visionPositioningEnabled() {
        return Boolean.TRUE.equals(binding.snapshot().get("vision_positioning_enabled"));
    }

    public static int horizontalAngleIntervalDeg() {
        Object v=binding.snapshot().get("oa_horizontal_angle_interval_deg");return v instanceof Number?((Number)v).intValue():0;
    }

    /** Raw 360-degree aircraft obstacle matrix, in DJI's published order. */
    public static int[] horizontalObstacleDistancesMm() {
        return ((int[])binding.snapshot().get("oa_horizontal_distances_mm")).clone();
    }

    public static int upwardObstacleDistanceMm() {
        Object v=binding.snapshot().get("oa_upward_distance_mm");return v instanceof Number?((Number)v).intValue():-1;
    }

    public static int downwardObstacleDistanceMm() {
        Object v=binding.snapshot().get("oa_downward_distance_mm");return v instanceof Number?((Number)v).intValue():-1;
    }

    public static long obstacleDataAgeMs() {
        return ((Number)binding.snapshot().get("oa_obstacle_data_age_ms")).longValue();
    }

    /** Each registration captures its manager and both listener identities before SDK entry. */
    public static void startPerceptionListener() {
        final IPerceptionManager manager=PerceptionManager.getInstance();
        binding.ensure(new PerceptionBinding.Adapter() {
            private PerceptionInformationListener infoListener;
            private ObstacleDataListener rangeListener;
            @Override public void prepare(Consumer<Map<String,Object>> callback,Consumer<PerceptionBinding.Range> rangeCallback) {
                infoListener=info->{
                    Map<String,Object> values=new LinkedHashMap<>(),directions=new LinkedHashMap<>();
                    ObstacleAvoidanceType type=info.getObstacleAvoidanceType();
                    values.put("oa_type",type==null?null:type.name());
                    values.put("oa_horizontal_enabled",info.isHorizontalObstacleAvoidanceEnabled());
                    values.put("oa_upward_enabled",info.isUpwardObstacleAvoidanceEnabled());
                    values.put("oa_downward_enabled",info.isDownwardObstacleAvoidanceEnabled());
                    values.put("vision_positioning_enabled",info.isVisionPositioningEnabled());
                    directions.put("left",info.getLeftSideObstacleAvoidanceWorking());
                    directions.put("right",info.getRightSideObstacleAvoidanceWorking());
                    directions.put("fwd",info.getForwardObstacleAvoidanceWorking());
                    directions.put("back",info.getBackwardObstacleAvoidanceWorking());
                    directions.put("up",info.getUpwardObstacleAvoidanceWorking());
                    directions.put("down",info.getDownwardObstacleAvoidanceWorking());
                    StringBuilder working=new StringBuilder();
                    for(Map.Entry<String,Object> e:directions.entrySet())append(working,e.getKey(),(Boolean)e.getValue());
                    values.put("oa_sensors_working",working.toString());values.put("working_directions",directions);
                    callback.accept(values);
                };
                rangeListener=data->{
                    List<Integer> list=data.getHorizontalObstacleDistance();
                    int[] values=new int[list==null?0:list.size()];
                    for(int i=0;i<values.length;i++)values[i]=list.get(i)==null?-1:list.get(i);
                    rangeCallback.accept(new PerceptionBinding.Range(data.getHorizontalAngleInterval(),values,
                            data.getUpwardObstacleDistance(),data.getDownwardObstacleDistance()));
                };
            }
            @Override public void addInfo(Consumer<Map<String,Object>> callback){manager.addPerceptionInformationListener(infoListener);}
            @Override public void addRange(Consumer<PerceptionBinding.Range> callback){manager.addObstacleDataListener(rangeListener);}
            @Override public void removeInfo(Consumer<Map<String,Object>> callback){if(infoListener!=null)manager.removePerceptionInformationListener(infoListener);}
            @Override public void removeRange(Consumer<PerceptionBinding.Range> callback){if(rangeListener!=null)manager.removeObstacleDataListener(rangeListener);}
        });
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
        final long permit=MaintenanceGate.SHARED.beginMutation("OA_SET_CLOSE",false,false);
        if(permit==0){callback.onResult(false,"MAINTENANCE_IN_PROGRESS_OR_ACTION_PENDING");return;}
        mutationGeneration=PcBridge.connectionGeneration();
        lastMutation="operation="+permit+",source=PC_EXPLICIT,requested=CLOSE,started_ms="+SystemClock.elapsedRealtime();
        final java.util.concurrent.atomic.AtomicBoolean replied=new java.util.concurrent.atomic.AtomicBoolean();
        setCloseInternal((ok,detail)->{
            if(detail.contains("threw"))MaintenanceGate.SHARED.uncertainMutation(permit);
            else MaintenanceGate.SHARED.completeMutation(permit);
            if(!replied.compareAndSet(false,true))return;
            lastMutation="operation="+permit+",source=PC_EXPLICIT,requested=CLOSE,completed_ms="+SystemClock.elapsedRealtime()+",ok="+ok+",detail="+detail;
            callback.onResult(ok,detail);
        });
    }
    private static void setCloseInternal(@NonNull ResultCallback callback) {
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
        if(mutationGeneration!=PcBridge.connectionGeneration()){callback.onResult(false,"SOURCE_CHANGED");return;}
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
        if(mutationGeneration!=PcBridge.connectionGeneration()){callback.onResult(false,"SOURCE_CHANGED");return;}
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
                                        horizontalAvoidanceEnabled(), callback);
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
                    upwardAvoidanceEnabled(), callback);
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
