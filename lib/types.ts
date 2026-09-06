export type MonitorId = "monitor-1" | "monitor-2" | "monitor-3";

export interface MonitorDestination {
  id: MonitorId;
  label: string;
  shortLabel: string;
  image: string;
  /** Percentage position (0-100) from the left edge of the route canvas. */
  x: number;
  /** Percentage position (0-100) from the top edge of the route canvas. */
  y: number;
}

export type RoutePlanningPhase =
  | "selecting-destinations"
  | "selecting-order"
  | "awaiting-confirmation"
  | "confirmed";

export interface RoutePlanningState {
  phase: RoutePlanningPhase;
  /** 계획 중인 경로. 확정 전까지 여기에 쌓인다. */
  draftRoute: MonitorId[];
  /** 확정된 경로. phase가 "confirmed"가 되면 draftRoute와 같아진다. */
  confirmedRoute: MonitorId[];
}

export type Severity = "low" | "medium" | "high";

export interface AnomalyResult {
  id: string;
  monitorId: MonitorId;
  /** Path to a placeholder detection thumbnail under /public/anomalies. */
  image: string;
  label: string;
  severity: Severity;
  /** Detection confidence, 0-1. */
  confidence: number;
  notes: string;
}

export interface ChatMessage {
  id: string;
  role: "user" | "agent";
  text: string;
  timestamp: number;
}
