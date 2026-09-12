package com.msdkremote.livecontrol.advanced;

import androidx.annotation.NonNull;
import com.msdkremote.PcBridge;
import com.msdkremote.lifecycle.MaintenanceGate;

import dji.sdk.keyvalue.key.FlightControllerKey;
import dji.sdk.keyvalue.key.KeyTools;
import dji.sdk.keyvalue.value.common.EmptyMsg;
import dji.v5.common.callback.CommonCallbacks;
import dji.v5.common.error.IDJIError;
import dji.v5.manager.KeyManager;

/**
 * Token-gated automatic takeoff/landing. DJI auto-takeoff climbs to a 1.2 m
 * hover; auto-landing descends and stops the motors.
 */
public final class FlightCommands {
    private FlightCommands() {
    }

    public static void startTakeoff(@NonNull StickControlManager.ResultCallback callback) {
        final long generation=PcBridge.connectionGeneration();
        final long permit=MaintenanceGate.SHARED.beginMutation("TAKEOFF",true,false);
        if(permit==0){callback.onResult(false,"MAINTENANCE_IN_PROGRESS_OR_ACTION_PENDING");return;}
        try {
            if(generation!=PcBridge.connectionGeneration()){MaintenanceGate.SHARED.completeMutation(permit);callback.onResult(false,"SOURCE_CHANGED");return;}
            KeyManager.getInstance().performAction(KeyTools.createKey(FlightControllerKey.KeyStartTakeoff),
                new CommonCallbacks.CompletionCallbackWithParam<EmptyMsg>() {
                    @Override public void onSuccess(EmptyMsg value){MaintenanceGate.SHARED.completeMutation(permit);callback.onResult(true,"takeoff_started");}
                    @Override public void onFailure(@NonNull IDJIError error){MaintenanceGate.SHARED.completeMutation(permit);callback.onResult(false,error.toString());}
                });
        }catch(RuntimeException error){callback.onResult(false,"SDK_SUBMISSION_UNCERTAIN");}
    }

    public static void startLanding(@NonNull StickControlManager.ResultCallback callback) {
        startLanding(PcBridge.connectionGeneration(),callback);
    }
    public static void startLanding(long expectedGeneration,@NonNull StickControlManager.ResultCallback callback) {
        if(expectedGeneration!=PcBridge.connectionGeneration()){callback.onResult(false,"SOURCE_CHANGED");return;}
        final long permit=MaintenanceGate.SHARED.beginMutation("LANDING",false,true);
        if(permit==0){callback.onResult(false,"LANDING_ALREADY_PENDING");return;}
        try {
            if(expectedGeneration!=PcBridge.connectionGeneration()){MaintenanceGate.SHARED.completeMutation(permit);callback.onResult(false,"SOURCE_CHANGED");return;}
            KeyManager.getInstance().performAction(KeyTools.createKey(FlightControllerKey.KeyStartAutoLanding),
                new CommonCallbacks.CompletionCallbackWithParam<EmptyMsg>() {
                    @Override public void onSuccess(EmptyMsg value){MaintenanceGate.SHARED.completeMutation(permit);callback.onResult(true,"landing_started");}
                    @Override public void onFailure(@NonNull IDJIError error){MaintenanceGate.SHARED.completeMutation(permit);callback.onResult(false,error.toString());}
                });
        }catch(RuntimeException error){callback.onResult(false,"SDK_SUBMISSION_UNCERTAIN");}
    }
}
