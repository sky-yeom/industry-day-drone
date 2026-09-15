package com.msdkremote.lifecycle;

import android.content.Context;
import android.net.wifi.WifiManager;
import android.os.Build;
import android.os.PowerManager;

import java.util.LinkedHashMap;
import java.util.Map;

/** Holds the CPU and Wi-Fi awake so a backgrounded screen cannot stall the TCP servers.
 * Acquiring a lock is not flight authorization and touches no SDK state. */
public final class BridgeKeepAlive {
    public static final String WAKE_TAG = "IndustryDayDrone:bridge";
    private static PowerManager.WakeLock wake;
    private static WifiManager.WifiLock wifi;
    private static String wakeError, wifiError;
    private static int wifiMode;

    private BridgeKeepAlive() {}

    public static synchronized void acquire(Context context) {
        Context app = context.getApplicationContext();
        if (wake == null) {
            try {
                PowerManager power = (PowerManager) app.getSystemService(Context.POWER_SERVICE);
                PowerManager.WakeLock lock = power.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, WAKE_TAG);
                lock.setReferenceCounted(false);
                lock.acquire();
                wake = lock;
                wakeError = null;
            } catch (RuntimeException error) { wakeError = error.toString(); }
        } else if (!wake.isHeld()) {
            try { wake.acquire(); } catch (RuntimeException error) { wakeError = error.toString(); }
        }
        if (wifi == null) {
            try {
                WifiManager manager = (WifiManager) app.getSystemService(Context.WIFI_SERVICE);
                // Low latency keeps the radio responsive without the deprecated high performance mode.
                wifiMode = Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q
                        ? WifiManager.WIFI_MODE_FULL_LOW_LATENCY : WifiManager.WIFI_MODE_FULL_HIGH_PERF;
                WifiManager.WifiLock lock = manager.createWifiLock(wifiMode, WAKE_TAG);
                lock.setReferenceCounted(false);
                lock.acquire();
                wifi = lock;
                wifiError = null;
            } catch (RuntimeException error) { wifiError = error.toString(); }
        } else if (!wifi.isHeld()) {
            try { wifi.acquire(); } catch (RuntimeException error) { wifiError = error.toString(); }
        }
    }

    public static synchronized void release() {
        if (wake != null && wake.isHeld()) { try { wake.release(); } catch (RuntimeException ignored) {} }
        if (wifi != null && wifi.isHeld()) { try { wifi.release(); } catch (RuntimeException ignored) {} }
    }

    public static synchronized Map<String, Object> status() {
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("wake_lock_held", wake != null && wake.isHeld());
        out.put("wifi_lock_held", wifi != null && wifi.isHeld());
        out.put("wifi_lock_mode", wifiMode);
        out.put("wake_lock_error", wakeError);
        out.put("wifi_lock_error", wifiError);
        return out;
    }
}
