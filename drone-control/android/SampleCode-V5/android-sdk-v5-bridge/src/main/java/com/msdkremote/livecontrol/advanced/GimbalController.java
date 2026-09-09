package com.msdkremote.livecontrol.advanced;

import androidx.annotation.NonNull;
import com.msdkremote.lifecycle.MaintenanceGate;

import dji.sdk.keyvalue.key.GimbalKey;
import dji.sdk.keyvalue.key.KeyTools;
import dji.sdk.keyvalue.value.common.EmptyMsg;
import dji.sdk.keyvalue.value.gimbal.GimbalAngleRotation;
import dji.sdk.keyvalue.value.gimbal.GimbalAngleRotationMode;
import dji.v5.common.callback.CommonCallbacks;
import dji.v5.common.error.IDJIError;
import dji.v5.manager.KeyManager;

public final class GimbalController {
    /** Mini 4 Pro gimbal travel: straight down to slightly above horizon. */
    private static final double MIN_PITCH_DEG = -90.0;
    private static final double MAX_PITCH_DEG = 35.0;

    private GimbalController() {
    }

    public static void setPitch(double pitchDeg, @NonNull StickControlManager.ResultCallback callback) {
        // Any angle in the Mini 4 Pro's mechanical range. Tilting part-way
        // (about -45 deg) is what lets one floor tag stay in view across the
        // whole traverse: straight down sees only ~0.6 m of floor, -45 deg
        // sees 0.7-3.3 m.
        if (!(pitchDeg >= MIN_PITCH_DEG && pitchDeg <= MAX_PITCH_DEG)) {
            callback.onResult(false, "pitch_out_of_range");
            return;
        }

        GimbalAngleRotation rotation = new GimbalAngleRotation();
        rotation.setMode(GimbalAngleRotationMode.ABSOLUTE_ANGLE);
        rotation.setPitch(pitchDeg);
        rotation.setDuration(1.0);
        final long permit=MaintenanceGate.SHARED.beginMutation("GIMBAL_PITCH",false,false);
        if(permit==0){callback.onResult(false,"MAINTENANCE_IN_PROGRESS_OR_ACTION_PENDING");return;}
        try { KeyManager.getInstance().performAction(
                KeyTools.createKey(GimbalKey.KeyRotateByAngle),
                rotation,
                new CommonCallbacks.CompletionCallbackWithParam<EmptyMsg>() {
                    @Override
                    public void onSuccess(EmptyMsg value) {
                        MaintenanceGate.SHARED.completeMutation(permit);
                        callback.onResult(true, "gimbal_set");
                    }

                    @Override
                    public void onFailure(@NonNull IDJIError error) {
                        MaintenanceGate.SHARED.completeMutation(permit);
                        callback.onResult(false, error.toString());
                    }
                }); }catch(RuntimeException error){callback.onResult(false,"SDK_SUBMISSION_UNCERTAIN");}
    }
}
