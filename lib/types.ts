export type MonitorId = "monitor-1" | "monitor-2" | "monitor-3";
export type DetectionMode = "mock" | "azure";
export type DroneControlMode = "mock" | "live";
export type DroneStopState = "not_requested" | "requesting" | "stop_requested" | "confirmed" | "unknown" | "awaiting_manual";
export type MissionPhase =
  | "briefing"
  | "ready"
  | "flying"
  | "capturing"
  | "analyzing"
  | "paused"
  | "complete"
  | "aborted";
export type RescueOutcome = "rescued" | "rescued_but_hurt" | "too_late";

export interface MonitorDestination {
  id: MonitorId;
  label: string;
  shortLabel: string;
  image: string;
  x: number;
  y: number;
}

export type RoutePlanningPhase =
  | "selecting-destinations"
  | "selecting-order"
  | "awaiting-confirmation"
  | "confirmed";

export interface RoutePlanningState {
  phase: RoutePlanningPhase;
  draftRoute: MonitorId[];
  confirmedRoute: MonitorId[];
}

export interface ChatMessage {
  id: string;
  role: "user" | "agent";
  text: string;
  timestamp: number;
}

export interface BriefingBullet {
  id: string;
  text: string;
}

export interface DetectionEvidence {
  targetPresent: boolean;
  description: string;
  box: [number, number, number, number] | null;
}

export interface CapturedImage {
  id: string;
  monitorId: MonitorId;
  imageUrl: string;
  capturedAtMs: number;
  status: "captured" | "analyzing" | "detected" | "not-found" | "error";
  evidence: DetectionEvidence | null;
  mode: DetectionMode;
  droneControlMode?: DroneControlMode;
  missionId?: string | null;
  visitIndex?: number | null;
  destinationId?: string | null;
  capturedAtUnixMs?: number | null;
}

export interface PersonState {
  id: string;
  monitorId: MonitorId;
  label: string;
  clue: string;
  targetDescription: string;
  initiallyInjured: boolean;
  deadlineMs: number;
  deteriorationMs: number;
  outcome: RescueOutcome | null;
  resolvedAtMs: number | null;
  captureId: string | null;
  attempts: number;
}

export interface MissionScore {
  rescuedCount: number;
  injuredCount: number;
  tooLateCount: number;
  total: number;
}

export interface AppearanceConstraint {
  attribute: "shirtColor" | "hairColor" | "garment";
  operator: "include" | "exclude";
  values: string[];
}

export interface MissionState {
  runId: string;
  revision: number;
  promptPhase: "briefing" | "confirmed";
  userPromptText: string;
  appearanceConstraints: AppearanceConstraint[];
  unsupportedAppearance: string[];
  missionPhase: MissionPhase;
  mode: DetectionMode;
  droneControlMode: DroneControlMode;
  droneMissionId: string | null;
  droneState: string;
  droneStopState: DroneStopState;
  droneErrorCode: string | null;
  activeVisitIndex: number | null;
  elapsedMs: number;
  clockRunning: boolean;
  activeMonitorId: MonitorId | null;
  people: PersonState[];
  captures: CapturedImage[];
  score: MissionScore | null;
  error: string | null;
}

export type DashboardState = RoutePlanningState & MissionState;

export const OUTCOME_LABELS: Record<RescueOutcome, string> = {
  rescued: "구조 완료",
  rescued_but_hurt: "부상 상태로 구조",
  too_late: "구조 시한 초과",
};
