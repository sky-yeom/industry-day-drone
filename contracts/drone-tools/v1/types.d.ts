export type ToolName = "drone_get_capabilities" | "drone_get_status" | "drone_execute_route"
  | "drone_get_mission" | "drone_stop_mission" | "drone_get_sensor_snapshot" | "drone_get_captures";
export type DestinationId = "tag-1" | "tag-2" | "tag-3";
export type MonitorId = "monitor-1" | "monitor-2" | "monitor-3";
export type Mode = "mock" | "live";
export type MissionState = "accepted" | "preflight" | "taking_off" | "running" | "returning"
  | "awaiting_rc_landing" | "completed" | "stop_requested" | "stopped" | "failed" | "outcome_unknown";
export interface ToolArguments {
  drone_get_capabilities: Record<string, never>;
  drone_get_status: Record<string, never>;
  drone_execute_route: {profile_id: string; site_revision: string; destination_ids: DestinationId[]};
  drone_get_mission: {mission_id: string};
  drone_stop_mission: {mission_id: string};
  drone_get_sensor_snapshot: Record<string, never>;
  drone_get_captures: {mission_id: string};
}
export interface Request<T extends ToolName> {
  arguments: ToolArguments[T]; caller_id: string; request_id: string;
}
export interface ErrorResponse {
  schema_version: 1; ok: false; execution_mode: Mode; physical_execution: boolean;
  error: {code: string; message: string};
}
export interface Success {
  schema_version: 1; ok: true; status: "ok"; execution_mode: Mode; physical_execution: boolean;
}
export interface Visit {
  visit_index: number; destination_id: DestinationId; state: "pending" | "moving" | "arrived" | "captured";
  arrival_confirmed: boolean; capture_ids: string[];
}
export interface Mission {
  mission_id: string; caller_id: string; request_id: string; profile_id: string; site_revision: string;
  state: MissionState; destination_ids: DestinationId[]; execution_mode: Mode; physical_execution: boolean;
  stop_requested: boolean; physical_stop_confirmed: boolean; verification_pending: boolean;
  visits: Visit[]; ground_verified?: boolean; route_completed?: boolean; visited_ids?: number[];
  updated_at_unix_ms?: number; simulated?: boolean; error?: string | null;
}
export interface Capture {
  capture_id: string; mission_id: string; visit_index: number; destination_id: DestinationId;
  arrival_confirmed: true; content_type: "image/png"; image_base64: string; sha256: string;
  captured_at_unix_ms: number; monitor_id?: MonitorId; simulated?: boolean; capture_source?: string;
  frame_generation?: number; frame_id?: number;
}
export interface Capabilities {
  live_ready: boolean; expected_mode_guard: true; profile_id: string; site_revision: string;
  home_tag_id: number; floor_tag_id: number; target_height_m: number; tools: ToolName[];
  destinations: {destination_id: DestinationId; monitor_id: MonitorId; physical_definition: {type: "apriltag"; marker_id: number}}[];
  supported_ordered_sequences: DestinationId[][]; mock_capture_ready?: boolean; adapter?: string; readiness_issues?: string[];
}
export interface Status {
  connected: boolean; ground_verified: boolean; active_mission_id: string | null;
  active_mission: Mission | null;
  active_request: {caller_id: string; request_id: string; mission_id: string} | null;
}
export interface ToolResults {
  drone_get_capabilities: Capabilities;
  drone_get_status: Status;
  drone_execute_route: {mission: Mission};
  drone_get_mission: {mission: Mission};
  drone_stop_mission: {mission: Mission; stop_requested: boolean; physical_stop_confirmed: boolean};
  drone_get_sensor_snapshot: {snapshot: Record<string, unknown>; sensor_semantics: string};
  drone_get_captures: {mission_id: string; captures: Capture[]};
}
export type Response<T extends ToolName> = (Success & ToolResults[T]) | ErrorResponse;
