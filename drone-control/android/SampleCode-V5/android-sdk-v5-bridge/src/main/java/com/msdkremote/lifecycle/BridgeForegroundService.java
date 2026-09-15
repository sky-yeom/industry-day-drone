package com.msdkremote.lifecycle;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.IBinder;

import androidx.annotation.Nullable;

import com.msdkremote.diagnostics.FieldDiagnostics;

import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.Map;

/** Keeps the bridge process alive while the screen is off or the app is backgrounded.
 * This service never touches the SDK; it only stops Android from reclaiming the process. */
public final class BridgeForegroundService extends Service {
    private static final String CHANNEL = "industry_day_drone_bridge";
    private static final int NOTIFICATION_ID = 0x0D9E;
    private static volatile String lastError;
    private static volatile boolean foreground;

    public static void start(Context context) {
        try {
            Context app = context.getApplicationContext();
            BridgeKeepAlive.acquire(app);
            Intent intent = new Intent(app, BridgeForegroundService.class);
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) app.startForegroundService(intent);
            else app.startService(intent);
        } catch (RuntimeException error) {
            lastError = error.toString();
            FieldDiagnostics.event("bridge_foreground_start_failed",
                    Collections.singletonMap("error", lastError));
        }
    }

    public static Map<String, Object> status() {
        Map<String, Object> out = new LinkedHashMap<>(BridgeKeepAlive.status());
        out.put("foreground_service_running", foreground);
        out.put("foreground_service_error", lastError);
        return out;
    }

    @Override public void onCreate() {
        super.onCreate();
        BridgeKeepAlive.acquire(this);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationChannel channel = new NotificationChannel(CHANNEL, "Drone bridge",
                    NotificationManager.IMPORTANCE_LOW);
            channel.setShowBadge(false);
            NotificationManager manager = getSystemService(NotificationManager.class);
            if (manager != null) manager.createNotificationChannel(channel);
        }
    }

    @Override public int onStartCommand(@Nullable Intent intent, int flags, int startId) {
        Notification notification = new Notification.Builder(this,
                Build.VERSION.SDK_INT >= Build.VERSION_CODES.O ? CHANNEL : null)
                .setContentTitle("Drone bridge running")
                .setContentText("Control, video and query servers are held open.")
                .setSmallIcon(android.R.drawable.stat_sys_upload)
                .setOngoing(true)
                .build();
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                startForeground(NOTIFICATION_ID, notification,
                        ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE);
            } else startForeground(NOTIFICATION_ID, notification);
            foreground = true;
            lastError = null;
            FieldDiagnostics.event("bridge_foreground_started", BridgeKeepAlive.status());
        } catch (RuntimeException error) {
            // A refused promotion must not take the bridge down with it; the wake lock still helps.
            foreground = false;
            lastError = error.toString();
            FieldDiagnostics.event("bridge_foreground_denied",
                    Collections.singletonMap("error", lastError));
        }
        return START_STICKY;
    }

    @Override public void onDestroy() {
        foreground = false;
        BridgeKeepAlive.release();
        FieldDiagnostics.event("bridge_foreground_stopped", Collections.emptyMap());
        super.onDestroy();
    }

    @Override public @Nullable IBinder onBind(Intent intent) { return null; }
}
