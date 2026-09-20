export type MonitorId = "monitor-1" | "monitor-2" | "monitor-3";
export type DetectionMode = "mock" | "azure";
export type ScenarioKind = "triage" | "security" | "construction";
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
export type SecurityOutcome = "caught" | "escaped";
export type ConstructionOutcome = "reported" | "not_found" | "unchecked";

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
  initiallyInjured: boolean;
  vulnerable: boolean;
  deadlineMs: number;
  deteriorationMs: number;
  outcome: ReportOutcome | SecurityOutcome | ConstructionOutcome | null;
  resolvedAtMs: number | null;
  captureId: string | null;
  attempts: number;
  promptConfirmed: boolean;
  promptText: string;
  promptConfidence: number | null;
  promptConfidenceReason: string;
  // Only present for the "security" scenario kind.
  falseAlarm?: boolean;
  falseAlarmReveal?: string;
}

export interface MissionScore {
  total: number;
  // Triage ("119 신고") scenario fields.
  reportedCount?: number;
  injuredCount?: number;
  reportMissedCount?: number;
  // Security Breach ("112 신고") scenario fields.
  caughtCount?: number;
  escapedCount?: number;
  falseAlarmCount?: number;
  // Construction Site Safety (현장 안전관리자 신고) scenario fields.
  violationsReportedCount?: number;
  notFoundCount?: number;
  uncheckedCount?: number;
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
  dangerOrder: MonitorId[];
  vulnerableAdjustedOrder: MonitorId[];
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
  report_missed: "신고 시한 초과",
};

export const SECURITY_OUTCOME_LABELS: Record<SecurityOutcome, string> = {
  caught: "112 신고 완료",
  escaped: "놓침 · 확인 실패",
};

export const CONSTRUCTION_OUTCOME_LABELS: Record<ConstructionOutcome, string> = {
  reported: "안전관리자 신고 완료",
  not_found: "확인했지만 대상 없음",
  unchecked: "미확인",
};
