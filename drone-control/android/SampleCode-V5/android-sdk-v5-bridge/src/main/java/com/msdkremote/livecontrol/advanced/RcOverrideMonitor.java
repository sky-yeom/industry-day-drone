package com.msdkremote.livecontrol.advanced;

import android.os.SystemClock;
import android.util.Log;

import androidx.annotation.NonNull;

import dji.sdk.keyvalue.key.DJIKey;
import dji.sdk.keyvalue.key.KeyTools;
import dji.sdk.keyvalue.key.RemoteControllerKey;
import dji.v5.common.callback.CommonCallbacks;
import dji.v5.common.error.IDJIError;
import dji.v5.manager.KeyManager;

/**
 * Watches the physical RC-N2 sticks while Virtual Stick is armed.
 * Any meaningful stick deflection immediately releases Virtual Stick so the
 * pilot's RC input takes over — the operator's runaway-drone safeguard.
 */
public final class RcOverrideMonitor {
    private static final String TAG = "RcOverrideMonitor";

    /** Stick range is [-660, 660]; ~23% deflection triggers the override. */
    public static final int OVERRIDE_THRESHOLD = 150;

    private static final RcOverrideMonitor INSTANCE = new RcOverrideMonitor();

    private final Object lock = new Object();
    private boolean started = false;
    private volatile long lastOverrideMs = 0;
    private volatile Integer lastLeftVertical = null;
    private volatile Integer lastLeftHorizontal = null;
    private volatile Integer lastRightVertical = null;
    private volatile Integer lastRightHorizontal = null;

    private final DJIKey<Integer> leftVertical =
            KeyTools.createKey(RemoteControllerKey.KeyStickLeftVertical);
    private final DJIKey<Integer> leftHorizontal =
            KeyTools.createKey(RemoteControllerKey.KeyStickLeftHorizontal);
    private final DJIKey<Integer> rightVertical =
            KeyTools.createKey(RemoteControllerKey.KeyStickRightVertical);
    private final DJIKey<Integer> rightHorizontal =
            KeyTools.createKey(RemoteControllerKey.KeyStickRightHorizontal);

    private RcOverrideMonitor() {
    }

    public static RcOverrideMonitor getInstance() {
        return INSTANCE;
    }

    public void start() {
        synchronized (lock) {
            if (started) {
                return;
            }
            started = true;
        }
        Log.i(TAG, "Starting RC stick override monitor");
        try {
        listen(leftVertical);
        listen(leftHorizontal);
        listen(rightVertical);
        listen(rightHorizontal);
        } catch (RuntimeException failure) {
            stop();
            throw failure;
        }
    }

    public void stop() {
        synchronized (lock) {
            if (!started) {
                return;
            }
            started = false;
        }
        try { KeyManager.getInstance().cancelListen(this); }
        catch (RuntimeException e) { Log.w(TAG, "RC listener cleanup failed", e); }
    }

    /** Elapsed-realtime ms of the last override, or 0 if none happened yet. */
    public long lastOverrideMs() {
        return lastOverrideMs;
    }

    public Integer leftVerticalValue() { return lastLeftVertical; }
    public Integer leftHorizontalValue() { return lastLeftHorizontal; }
    public Integer rightVerticalValue() { return lastRightVertical; }
    public Integer rightHorizontalValue() { return lastRightHorizontal; }

    /**
     * Samples the current stick positions once. Listeners only fire on value
     * CHANGES, so a stick already held over when arming completes would be
     * invisible without this — called right after every successful arm.
     */
    public void checkNow() {
        sample(leftVertical);
        sample(leftHorizontal);
        sample(rightVertical);
        sample(rightHorizontal);
    }

    private void sample(DJIKey<Integer> key) {
        KeyManager.getInstance().getValue(key,
                new CommonCallbacks.CompletionCallbackWithParam<Integer>() {
                    @Override
                    public void onSuccess(Integer value) {
                        onStickValue(key, value);
                    }

                    @Override
                    public void onFailure(@NonNull IDJIError error) {
                        // No cached value yet; the change listener covers us.
                    }
                });
    }

    private void listen(DJIKey<Integer> key) {
        KeyManager.getInstance().listen(key, this,
                (oldValue, newValue) -> onStickValue(key, newValue));
    }

    private void onStickValue(DJIKey<Integer> key, Integer value) {
        if (key == leftVertical) {
            lastLeftVertical = value;
        } else if (key == leftHorizontal) {
            lastLeftHorizontal = value;
        } else if (key == rightVertical) {
            lastRightVertical = value;
        } else if (key == rightHorizontal) {
            lastRightHorizontal = value;
        }
        if (value == null || Math.abs(value) < OVERRIDE_THRESHOLD) {
            return;
        }
        // armed OR still enabling: overriding during the arm handshake bumps
        // the epoch, which cancels the in-flight arm as well.
        if (!StickControlManager.getInstance().isArmedOrEnabling()) {
            return;
        }
        lastOverrideMs = SystemClock.elapsedRealtime();
        Log.w(TAG, "RC stick moved (" + value + "/660) while armed - "
                + "releasing Virtual Stick, RC pilot has control");
        StickControlManager.getInstance().emergencyStop();
    }
}
