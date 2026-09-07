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

export interface ChatMessage {
  id: string;
  role: "user" | "agent";
  text: string;
  timestamp: number;
}

/** 브리핑 카드 한 줄. data/scenario.ts에 실제 임시(placeholder) 텍스트가 있다. */
export interface BriefingBullet {
  id: string;
  text: string;
}

/**
 * 임시(placeholder) 정답 이상 징후 하나. 아직 실제 드론 이미지/컴퓨터 비전이
 * 없으므로, 사용자가 말한 것과 이 목록을 단순 키워드 매칭으로 비교해 점수를
 * 매긴다 (relay/survey.py의 score_prompt 참고). 실제 시나리오/정답 데이터가
 * 생기면 data/scenario.ts만 교체하면 된다.
 */
export interface GroundTruthAnomaly {
  id: string;
  monitorId: MonitorId;
  label: string;
  /** 사용자의 발화에서 이 이상 징후를 언급했는지 확인할 키워드들. */
  keywords: string[];
}

export type PromptPhase = "briefing" | "confirmed";

export type DetectionPhase = "idle" | "scanning" | "complete";

export interface DetectedAnomaly {
  id: string;
  monitorId: MonitorId;
  label: string;
}

export interface MissionScore {
  matchedCount: number;
  missedCount: number;
  totalGroundTruth: number;
  accuracyPercent: number;
  /** 사용자가 맞춘 이상 징후 목록 (참고/표시용). */
  matched: GroundTruthAnomaly[];
  /** 사용자가 언급하지 않은 이상 징후 목록. */
  missed: GroundTruthAnomaly[];
}

export interface MissionState {
  promptPhase: PromptPhase;
  /** 사용자가 확정한, 자신의 말로 표현한 주의사항. */
  userPromptText: string;
  detectionPhase: DetectionPhase;
  detectedAnomalies: DetectedAnomaly[];
  score: MissionScore | null;
}

/**
 * 릴레이가 매 도구 호출마다 통째로 내려주는 전체 상태.
 * relay/survey.py의 SurveySession.snapshot()이 RouteState와 MissionState를
 * 하나의 딕셔너리로 합쳐 보내므로, 클라이언트도 하나의 타입으로 받는다.
 */
export type DashboardState = RoutePlanningState & MissionState;
