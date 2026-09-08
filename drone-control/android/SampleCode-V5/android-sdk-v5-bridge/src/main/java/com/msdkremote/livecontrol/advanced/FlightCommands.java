package com.msdkremote.livecontrol.advanced;

import androidx.annotation.NonNull;

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
        KeyManager.getInstance().performAction(
                KeyTools.createKey(FlightControllerKey.KeyStartTakeoff),
                new CommonCallbacks.CompletionCallbackWithParam<EmptyMsg>() {
                    @Override
                    public void onSuccess(EmptyMsg value) {
                        callback.onResult(true, "takeoff_started");
                    }

                    @Override
                    public void onFailure(@NonNull IDJIError error) {
                        callback.onResult(false, error.toString());
                    }
                });
    }

    public static void startLanding(@NonNull StickControlManager.ResultCallback callback) {
        KeyManager.getInstance().performAction(
                KeyTools.createKey(FlightControllerKey.KeyStartAutoLanding),
                new CommonCallbacks.CompletionCallbackWithParam<EmptyMsg>() {
                    @Override
                    public void onSuccess(EmptyMsg value) {
                        callback.onResult(true, "landing_started");
                    }

                    @Override
                    public void onFailure(@NonNull IDJIError error) {
                        callback.onResult(false, error.toString());
                    }
                });
    }
}
