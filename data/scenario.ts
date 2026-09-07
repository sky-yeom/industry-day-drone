import type { BriefingBullet, GroundTruthAnomaly, MissionState } from "@/lib/types";

/**
 * 임시(placeholder) 브리핑/정답 데이터.
 *
 * 실제 시나리오와 정답 이상 징후는 나중에 정해질 예정이다. 그때는 이 파일의
 * 내용만 교체하면 되고, 프롬프트/드론 이미지/결과 탭의 로직은 그대로 동작한다.
 */

export const SCENARIO_BRIEFING: BriefingBullet[] = [
  {
    id: "briefing-1",
    text: "이번 임무는 태풍이 지나간 뒤 시설물 피해를 점검하는 것입니다.",
  },
  {
    id: "briefing-2",
    text: "드론은 선택하신 3개 지점을 순서대로 촬영합니다.",
  },
  {
    id: "briefing-3",
    text: "촬영이 끝나면 어떤 점을 특히 주의 깊게 보실 건지 말씀해주세요.",
  },
];

export const GROUND_TRUTH_ANOMALIES: GroundTruthAnomaly[] = [
  {
    id: "standing-water",
    monitorId: "monitor-1",
    label: "침수 구역",
    keywords: ["침수", "물", "홍수", "고인 물"],
  },
  {
    id: "structural-crack",
    monitorId: "monitor-2",
    label: "구조물 균열",
    keywords: ["균열", "금", "구조물", "붕괴"],
  },
  {
    id: "debris-field",
    monitorId: "monitor-2",
    label: "잔해물 산재",
    keywords: ["잔해", "쓰레기", "파편"],
  },
  {
    id: "crop-stress",
    monitorId: "monitor-3",
    label: "작물 손상",
    keywords: ["작물", "농작물", "식물", "고사"],
  },
];

export const INITIAL_MISSION_STATE: MissionState = {
  promptPhase: "briefing",
  userPromptText: "",
  detectionPhase: "idle",
  detectedAnomalies: [],
  score: null,
};
