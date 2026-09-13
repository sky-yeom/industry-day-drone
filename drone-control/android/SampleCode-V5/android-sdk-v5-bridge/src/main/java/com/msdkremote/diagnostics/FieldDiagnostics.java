package com.msdkremote.diagnostics;

import android.app.Activity;
import android.app.Application;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.hardware.usb.UsbManager;
import android.os.Build;
import android.os.Bundle;
import android.os.Process;
import android.os.SystemClock;
import android.util.Log;

import java.io.File;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicLong;

/**
 * Opt-in, process-local diagnostics in the app's private files directory.
 * Call start once during application startup, then submit sanitized facts only.
 * Never pass SDK objects, raw errors, command payloads, identifiers, images or coordinates.
 * All directory access, writes, rotation and per-record fsync run on the dedicated writer.
 */
public final class FieldDiagnostics {
    private static final String TAG = "FieldDiagnostics";
    private static final AtomicBoolean START_ATTEMPTED = new AtomicBoolean();
    private static final AtomicBoolean CALLBACK_ERROR_LOGGED = new AtomicBoolean();
    private static final AtomicLong BEFORE_START_DROPPED = new AtomicLong();
    private static final AtomicLong CALLBACK_FAILURES = new AtomicLong();
    private static final AtomicLong LOGGING_FAILURES = new AtomicLong();
    private static volatile DiagnosticRecorder recorder;
    private static volatile String initializationState = "not_started";
    private static volatile String lastCallbackSafeError;
    private static volatile boolean activityLifecycleRegistered;
    private static volatile boolean usbReceiverRegistered;

    private FieldDiagnostics() {}

    /** Idempotent. A failed initialization remains visible through status(), without retry loops. */
    public static void start(Context context) {
        if (!START_ATTEMPTED.compareAndSet(false, true)) {
            return;
        }
        initializationState = "starting";
        try {
            if (context == null) {
                throw new IllegalArgumentException("Context is required");
            }
            Context applicationContext = context.getApplicationContext();
            final Context appContext = applicationContext != null ? applicationContext : context;
            DiagnosticRecorder created = new DiagnosticRecorder(
                    () -> new DiagnosticRecorder.RotatingFiles(
                            new File(appContext.getFilesDir(), DiagnosticRecorder.DIRECTORY_NAME),
                            DiagnosticRecorder.DEFAULT_LIMITS),
                    DiagnosticRecorder.DEFAULT_LIMITS, UUID.randomUUID().toString(), Process.myPid(),
                    FieldDiagnostics::logSafeError);
            Map<String, Object> fields = new LinkedHashMap<>();
            fields.put("schemaVersion", 1);
            fields.put("androidApi", Build.VERSION.SDK_INT);
            fields.put("storage", "private_internal");
            fields.put("syncEachRecord", true);
            Thread thread = Thread.currentThread();
            created.record("diagnostics.process_start", fields, SystemClock.elapsedRealtime(),
                    System.currentTimeMillis(), thread.getName(), thread.getId());
            recorder = created;
            created.start();
            registerActivityLifecycle(appContext);
            registerUsbReceiver(appContext);
            initializationState = "started";
            Map<String, Object> registrations = new LinkedHashMap<>();
            registrations.put("activityLifecycleRegistered", activityLifecycleRegistered);
            registrations.put("usbReceiverRegistered", usbReceiverRegistered);
            event("diagnostics.registrations", registrations);
        } catch (RuntimeException | LinkageError error) {
            initializationState = "failed";
            callbackFailure("start", error);
        }
    }

    /** Non-blocking with respect to disk; fields are bounded and copied into JSON before return. */
    public static void event(String name, Map<String, Object> fields) {
        DiagnosticRecorder current = recorder;
        if (current == null) {
            BEFORE_START_DROPPED.incrementAndGet();
            return;
        }
        try {
            Thread thread = Thread.currentThread();
            current.record(name, fields, SystemClock.elapsedRealtime(), System.currentTimeMillis(),
                    thread.getName(), thread.getId());
        } catch (RuntimeException | LinkageError error) {
            callbackFailure("event", error);
        }
    }

    /** Records up to 24 class/method/line frames, never a Throwable or its message. */
    public static void callSite(String name) {
        if (recorder == null) {
            BEFORE_START_DROPPED.incrementAndGet();
            return;
        }
        try {
            List<String> frames = new ArrayList<>();
            for (StackTraceElement frame : Thread.currentThread().getStackTrace()) {
                if (frame.getClassName().equals(FieldDiagnostics.class.getName())
                        || frame.getClassName().equals(Thread.class.getName())) {
                    continue;
                }
                frames.add(frame.getClassName() + "." + frame.getMethodName()
                        + ":" + frame.getLineNumber());
                if (frames.size() == 24) {
                    break;
                }
            }
            event(name, Collections.singletonMap("frames", frames));
        } catch (RuntimeException | LinkageError error) {
            callbackFailure("call_site", error);
        }
    }

    /** In-memory snapshot only. File names are basenames, never private absolute paths. */
    public static Map<String, Object> status() {
        DiagnosticRecorder current = recorder;
        Map<String, Object> result;
        if (current == null) {
            result = DiagnosticRecorder.configuration(DiagnosticRecorder.DEFAULT_LIMITS);
            result.put("enabled", false);
            result.put("state", initializationState);
            result.put("queueDropped", 0L);
            result.put("writeFailures", 0L);
            result.put("lastSafeError", lastCallbackSafeError);
        } else {
            result = current.status();
            if (result.get("lastSafeError") == null) {
                result.put("lastSafeError", lastCallbackSafeError);
            }
        }
        result.put("initializationState", initializationState);
        result.put("startAttempted", START_ATTEMPTED.get());
        result.put("beforeStartDropped", BEFORE_START_DROPPED.get());
        result.put("callbackFailures", CALLBACK_FAILURES.get());
        result.put("loggingFailures", LOGGING_FAILURES.get());
        result.put("lastCallbackSafeError", lastCallbackSafeError);
        result.put("activityLifecycleRegistered", activityLifecycleRegistered);
        result.put("usbReceiverRegistered", usbReceiverRegistered);
        result.put("networkCallbackRegistered", false);
        return result;
    }

    private static void registerActivityLifecycle(Context context) {
        if (!(context instanceof Application)) {
            callbackFailure("activity_registration",
                    new IllegalStateException("Application context is unavailable"));
            return;
        }
        try {
            ((Application) context).registerActivityLifecycleCallbacks(
                    new Application.ActivityLifecycleCallbacks() {
                        @Override public void onActivityCreated(Activity activity, Bundle state) {
                            activityEvent(activity, "created");
                        }
                        @Override public void onActivityStarted(Activity activity) {
                            activityEvent(activity, "started");
                        }
                        @Override public void onActivityResumed(Activity activity) {
                            activityEvent(activity, "resumed");
                        }
                        @Override public void onActivityPaused(Activity activity) {
                            activityEvent(activity, "paused");
                        }
                        @Override public void onActivityStopped(Activity activity) {
                            activityEvent(activity, "stopped");
                        }
                        @Override public void onActivitySaveInstanceState(Activity activity, Bundle state) {
                            activityEvent(activity, "save_instance_state");
                        }
                        @Override public void onActivityDestroyed(Activity activity) {
                            activityEvent(activity, "destroyed");
                        }
                    });
            activityLifecycleRegistered = true;
        } catch (RuntimeException | LinkageError error) {
            callbackFailure("activity_registration", error);
        }
    }

    private static void activityEvent(Activity activity, String lifecycle) {
        try {
            Map<String, Object> fields = new LinkedHashMap<>();
            fields.put("activityClass", activity.getClass().getName());
            fields.put("lifecycle", lifecycle);
            event("activity.lifecycle", fields);
        } catch (RuntimeException | LinkageError error) {
            callbackFailure("activity_callback", error);
        }
    }

    private static void registerUsbReceiver(Context context) {
        try {
            IntentFilter filter = new IntentFilter();
            filter.addAction(UsbManager.ACTION_USB_ACCESSORY_ATTACHED);
            filter.addAction(UsbManager.ACTION_USB_ACCESSORY_DETACHED);
            BroadcastReceiver receiver = new BroadcastReceiver() {
                @Override
                public void onReceive(Context receiverContext, Intent intent) {
                    try {
                        String action = intent == null ? null : intent.getAction();
                        if (UsbManager.ACTION_USB_ACCESSORY_ATTACHED.equals(action)) {
                            event("usb.accessory_attached", Collections.emptyMap());
                        } else if (UsbManager.ACTION_USB_ACCESSORY_DETACHED.equals(action)) {
                            event("usb.accessory_detached", Collections.emptyMap());
                        }
                    } catch (RuntimeException | LinkageError error) {
                        callbackFailure("usb_callback", error);
                    }
                }
            };
            if (Build.VERSION.SDK_INT >= 33) {
                context.registerReceiver(receiver, filter, Context.RECEIVER_NOT_EXPORTED);
            } else {
                context.registerReceiver(receiver, filter);
            }
            usbReceiverRegistered = true;
        } catch (RuntimeException | LinkageError error) {
            callbackFailure("usb_registration", error);
        }
    }

    private static void callbackFailure(String phase, Throwable error) {
        CALLBACK_FAILURES.incrementAndGet();
        lastCallbackSafeError = DiagnosticRecorder.safeError(phase, error);
        if (CALLBACK_ERROR_LOGGED.compareAndSet(false, true)) {
            logSafeError(lastCallbackSafeError);
        }
        DiagnosticRecorder current = recorder;
        if (current != null) {
            Map<String, Object> fields = new LinkedHashMap<>();
            fields.put("operation", phase);
            fields.put("errorClass", error.getClass().getName());
            try {
                Thread thread = Thread.currentThread();
                current.record("diagnostics.callback_failure", fields, SystemClock.elapsedRealtime(),
                        System.currentTimeMillis(), thread.getName(), thread.getId());
            } catch (RuntimeException | LinkageError recordingError) {
                CALLBACK_FAILURES.incrementAndGet();
                lastCallbackSafeError = DiagnosticRecorder.safeError("failure_record", recordingError);
            }
        }
    }

    private static void logSafeError(String safeError) {
        try {
            Log.e(TAG, "Diagnostic recorder error (details in status): " + safeError);
        } catch (RuntimeException | LinkageError loggingError) {
            LOGGING_FAILURES.incrementAndGet();
        }
    }
}
