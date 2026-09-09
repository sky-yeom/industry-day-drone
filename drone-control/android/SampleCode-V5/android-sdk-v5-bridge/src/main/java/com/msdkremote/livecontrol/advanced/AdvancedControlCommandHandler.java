package com.msdkremote.livecontrol.advanced;

import androidx.annotation.NonNull;

import com.msdkremote.commandserver.CommandHandler;
import com.msdkremote.commandserver.CommandServer;

import org.json.JSONException;
import org.json.JSONObject;

public final class AdvancedControlCommandHandler implements CommandHandler {
    private static final int PROTOCOL_VERSION = 1;
    private final StickControlManager stickManager;
    private final String armToken;
    private final Object sessionLock = new Object();
    private long lastSequence = -1;
    private long lastTimestampNs = -1;

    public AdvancedControlCommandHandler(
            @NonNull StickControlManager stickManager, @NonNull String armToken) {
        this.stickManager = stickManager;
        this.armToken = armToken;
    }

    /**
     * Called when a PC client connects or disconnects, so a fresh client can
     * start sequence numbering from zero again.
     */
    public void resetSession() {
        synchronized (sessionLock) {
            lastSequence = -1;
            lastTimestampNs = -1;
        }
        stickManager.resetSequence();
    }

    @Override
    public void onCommand(@NonNull CommandServer server, @NonNull String command) {
        onCommand(server,command,server.getConnectionEpoch());
    }

    @Override public void onCommand(@NonNull CommandServer server,@NonNull String command,long connectionEpoch) {
        if(!server.isSessionActive(connectionEpoch))return;

        final JSONObject request;
        try {
            request = new JSONObject(command);
            if (request.length() != 5
                    || request.getInt("version") != PROTOCOL_VERSION) {
                send(server, connectionEpoch, request.optLong("sequence", -1),
                        false, "unsupported_or_invalid_envelope");
                return;
            }
        } catch (JSONException error) {
            send(server, connectionEpoch, -1, false, "invalid_json");
            return;
        }

        long sequence = request.optLong("sequence", -1);
        long timestampNs = request.optLong("timestamp_ns", -1);
        String type = request.optString("type", "");
        JSONObject payload = request.optJSONObject("payload");
        synchronized (sessionLock) {
            // Monotonic, not strictly consecutive. A "+1" gate turns a single
            // dropped or duplicated frame into a permanent reject loop:
            // lastSequence never advances, so every later command is rejected
            // and motion stops until reconnect.
            // TCP already guarantees order and delivery, so "strictly
            // increasing" is as strong a replay guard as this needs, and it
            // matches what StickControlManager.setVelocity already does.
            if (sequence <= lastSequence || timestampNs <= lastTimestampNs
                    || type.isEmpty() || payload == null) {
                send(server, connectionEpoch, sequence, false, "missing_type_or_sequence");
                return;
            }
            lastSequence = sequence;
            lastTimestampNs = timestampNs;
        }

        // Link activity is diagnostic only; it cannot renew the motion lease.
        stickManager.touchKeepalive();

        switch (type) {
            case "arm":
                if (!isTokenValid(payload)) {
                    send(server, connectionEpoch, sequence, false, "invalid_confirmation_token");
                } else {
                    stickManager.arm((success, detail) ->
                            send(server, connectionEpoch, sequence, success, detail));
                }
                break;
            case "takeoff":
                if (!isTokenValid(payload)) {
                    send(server, connectionEpoch, sequence, false, "invalid_confirmation_token");
                } else {
                    FlightCommands.startTakeoff((success, detail) ->
                            send(server, connectionEpoch, sequence, success, detail));
                }
                break;
            case "land":
                if (!isTokenValid(payload)) {
                    send(server, connectionEpoch, sequence, false, "invalid_confirmation_token");
                } else {
                    final long landingGeneration=com.msdkremote.PcBridge.connectionGeneration();
                    stickManager.disarm((ignoredSuccess, ignoredDetail) ->
                            FlightCommands.startLanding(landingGeneration,(success, detail) ->
                                    send(server, connectionEpoch, sequence, success, detail)));
                }
                break;
            case "heartbeat":
                send(server, connectionEpoch, sequence,
                        stickManager.acceptHeartbeat(sequence), "heartbeat");
                break;
            case "stick_mode":
                stickManager.selectMode(payload.optString("mode", ""),
                        (success, detail) ->
                                send(server, connectionEpoch, sequence, success, detail));
                break;
            case "velocity":
                try {
                    boolean accepted = stickManager.setVelocity(
                            sequence,
                            payload.getDouble("forward_mps"),
                            payload.getDouble("right_mps"),
                            payload.getDouble("up_mps"),
                            Math.toDegrees(payload.getDouble("yaw_rate_rps")));
                    send(server, connectionEpoch, sequence, accepted,
                            accepted ? "velocity_accepted" : "rejected");
                } catch (JSONException error) {
                    send(server, connectionEpoch, sequence, false, "missing_velocity_field");
                }
                break;
            case "attitude":
                try {
                    boolean accepted = stickManager.setAttitude(
                            sequence,
                            payload.getDouble("forward_tilt_deg"),
                            payload.getDouble("right_tilt_deg"),
                            payload.getDouble("up_mps"),
                            Math.toDegrees(payload.getDouble("yaw_rate_rps")));
                    send(server, connectionEpoch, sequence, accepted,
                            accepted ? "attitude_accepted" : "rejected");
                } catch (JSONException error) {
                    send(server, connectionEpoch, sequence, false, "missing_attitude_field");
                }
                break;
            case "zero":
                boolean zeroed = stickManager.zero(sequence);
                send(server, connectionEpoch, sequence, zeroed,
                        zeroed ? "zeroed" : "zero_failed_released");
                break;
            case "disarm":
            case "emergency_stop":
                stickManager.disarm((success, detail) ->
                        send(server, connectionEpoch, sequence, success, detail));
                break;
            case "gimbal":
                try {
                    GimbalController.setPitch(payload.getDouble("pitch_deg"),
                            (success, detail) -> {
                                stickManager.touchKeepalive();
                                send(server, connectionEpoch, sequence, success, detail);
                            });
                } catch (JSONException error) {
                    send(server, connectionEpoch, sequence, false, "missing_pitch_deg");
                }
                break;
            case "obstacle_avoidance":
                ObstacleAvoidanceController.setClose((success, detail) -> {
                    stickManager.touchKeepalive();
                    send(server, connectionEpoch, sequence, success, detail);
                });
                break;
            case "status":
                send(server, connectionEpoch, sequence, true,
                        stickManager.isArmed() ? "status_armed" : "status_disarmed");
                break;
            default:
                send(server, connectionEpoch, sequence, false, "unknown_type");
                break;
        }
    }

    private boolean isTokenValid(JSONObject payload) {
        return armToken.equals(
                payload.optString("confirmation_token", ""));
    }

    private void send(
            CommandServer server, long epoch, long sequence, boolean ok, String detail) {
        server.sendMessage(baseResponse(sequence, ok, detail).toString(), epoch);
    }

    private JSONObject baseResponse(long sequence, boolean ok, String detail) {
        JSONObject response = new JSONObject();
        try {
            JSONObject payload = new JSONObject();
            payload.put("ok", ok);
            payload.put("detail", detail);
            payload.put("result_schema_version", 1);
            payload.put("status", semanticStatus(ok, detail));
            payload.put("message_ko", semanticMessage(ok, detail));
            payload.put("telemetry", TelemetryProvider.getInstance().snapshotJson());
            response.put("version", PROTOCOL_VERSION);
            response.put("type", "ack");
            response.put("sequence", sequence);
            response.put("timestamp_ns", System.nanoTime());
            response.put("payload", payload);
        } catch (JSONException ignored) {
            // Keys and primitive values above are JSON-safe.
        }
        return response;
    }

    @NonNull
    private static String semanticStatus(boolean ok, @NonNull String detail) {
        if (!ok) {
            if (detail.contains("authority") || detail.contains("advanced")) {
                return "CONTROL_STATE_REJECTED";
            }
            if (detail.contains("token")) {
                return "AUTHORIZATION_REJECTED";
            }
            return "COMMAND_REJECTED";
        }
        switch (detail) {
            case "velocity_accepted":
                return "PHONE_ACCEPTED_WAITING_FOR_MOTION";
            case "attitude_accepted":
                return "PHONE_ACCEPTED_ANGLE_WAITING_FOR_MOTION";
            case "zeroed":
                return "SETPOINT_ZEROED";
            case "armed":
            case "already_armed":
                return "CONTROL_ARMED";
            case "heartbeat":
                return "HEARTBEAT_ACKNOWLEDGED";
            case "status_armed":
            case "status_disarmed":
                return "STATUS_REPORTED";
            default:
                if (detail.startsWith("oa_type=CLOSE")
                        && detail.contains("switch_support=UNSUPPORTED")) {
                    return "OBSTACLE_AVOIDANCE_CLOSE_VERIFIED_DIRECTIONAL_SWITCH_UNSUPPORTED";
                }
                if (detail.startsWith("oa_type=CLOSE")
                        && detail.contains("horizontal_enabled=false")
                        && detail.contains("upward_enabled=false")) {
                    return "DIRECTIONAL_OBSTACLE_AVOIDANCE_VERIFIED_OFF";
                }
                if (detail.contains("advanced")) {
                    return "CONTROL_PATH_SELECTED";
                }
                return "DJI_ACTION_SUCCEEDED";
        }
    }

    @NonNull
    private static String semanticMessage(boolean ok, @NonNull String detail) {
        if (!ok) {
            return "명령이 거부되었습니다. 원인: " + detail;
        }
        switch (detail) {
            case "velocity_accepted":
                return "속도 명령이 휴대폰 제어기에 저장되었습니다. 실제 기체 이동은 아직 확인되지 않았습니다.";
            case "attitude_accepted":
                return "기울기 명령이 휴대폰 제어기에 저장되었습니다. 실제 기체 이동은 아직 확인되지 않았습니다.";
            case "zeroed":
                return "모든 이동 목표값을 0으로 설정했습니다.";
            case "armed":
            case "already_armed":
                return "Virtual Stick 제어가 활성화되었습니다.";
            case "heartbeat":
                return "제어 연결이 정상입니다.";
            case "status_armed":
                return "현재 Virtual Stick 제어가 활성화된 상태입니다.";
            case "status_disarmed":
                return "현재 Virtual Stick 제어가 비활성화된 상태입니다.";
            default:
                if (detail.startsWith("oa_type=CLOSE")
                        && detail.contains("switch_support=UNSUPPORTED")) {
                    return "장애물 회피 모드는 CLOSE로 확인되었습니다. 이 Mini 4 Pro는 일부 방향별 회피 하위 스위치를 지원하지 않으므로 펌웨어가 좁은 공간에서 제동할 수 있습니다.";
                }
                if (detail.startsWith("oa_type=CLOSE")
                        && detail.contains("horizontal_enabled=false")
                        && detail.contains("upward_enabled=false")) {
                    return "장애물 회피 모드는 CLOSE이고 수평·상방 회피 하위 스위치는 OFF로 확인되었습니다. 하방 비전 위치 유지는 켜 둡니다.";
                }
                return "DJI 작업이 완료되었습니다. 세부 결과: " + detail;
        }
    }
}
