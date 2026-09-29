export type MonitorId = "monitor-1" | "monitor-2" | "monitor-3";
export type DetectionMode = "mock" | "azure";
export type ScenarioKind = "triage";
export type MissionPhase =
  | "briefing"
  | "ready"
  | "flying"
  | "capturing"
  | "analyzing"
  | "paused"
  | "complete"
  | "aborted";
export type ReportOutcome = "reported" | "reported_injured" | "report_missed";

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
  confidence: number | null;
  box: [number, number, number, number] | null;
  boxes?: [number, number, number, number][];
  violatorCount?: number;
}

export interface CapturedImage {
  id: string;
  monitorId: MonitorId;
  imageUrl: string;
  capturedAtMs: number;
  status: "captured" | "analyzing" | "detected" | "not-found" | "error";
  evidence: DetectionEvidence | null;
  mode: DetectionMode;
}

export interface PersonState {
  id: string;
  monitorId: MonitorId;
  label: string;
  clue: string;
  targetDescription: string;
  outcome: ReportOutcome | null;
  resolvedAtMs: number | null;
  captureId: string | null;
  attempts: number;
  promptConfirmed: boolean;
  promptText: string;
  promptConfidence: number | null;
  promptConfidenceReason: string;
  falseAlarm?: boolean;
  falseAlarmReveal?: string;
}

export interface MissionScore {
  total: number;
  reportedCount?: number;
  injuredCount?: number;
  reportMissedCount?: number;
  falseAlarmCount?: number;
}

export interface AppearanceConstraint {
  attribute: "shirtColor" | "hairColor" | "garment" | "headwear";
  operator: "include" | "exclude";
  values: string[];
}

export interface MissionState {
  runId: string;
  revision: number;
  kind: ScenarioKind;
  promptPhase: "briefing" | "confirmed";
  activePromptMonitorId: MonitorId;
  userPromptText: string;
  appearanceConstraints: AppearanceConstraint[];
  unsupportedAppearance: string[];
  promptConfidence: number | null;
  promptConfidenceReason: string;
  missionPhase: MissionPhase;
  mode: DetectionMode;
  elapsedMs: number;
  clockRunning: boolean;
  activeMonitorId: MonitorId | null;
  people: PersonState[];
  captures: CapturedImage[];
  score: MissionScore | null;
  error: string | null;
}

export type DashboardState = RoutePlanningState & MissionState;

export const OUTCOME_LABELS: Record<ReportOutcome, string> = {
  reported: "119 신고 완료",
  reported_injured: "부상 확인 · 119 신고 완료",
  report_missed: "구조 실패",
};
