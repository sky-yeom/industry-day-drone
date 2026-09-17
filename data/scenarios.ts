import { SCENARIO_BRIEFING, TARGET_APPEARANCE } from "@/data/scenario";
import type { BriefingBullet } from "@/lib/types";

export type ScenarioId = "saving-people" | "missing-person" | "safety-check";

export interface ScenarioConfig {
  id: ScenarioId;
  /** Label shown on the scenario-picker button. */
  buttonLabel: string;
  /** Title shown on the briefing/prompt screen. */
  title: string;
  briefing: BriefingBullet[];
  targetImage: string;
  targetAlt: string;
  /** Ribbon label over the target photo (e.g. "구조 필요"). */
  badgeLabel: string;
}

// Only "saving-people" is wired to the real relay/voice backend right now
// (see app/page.tsx) — picking either of the other two still launches that
// same real mission underneath; only this pre-mission intro/briefing screen
// is scenario-specific for now. The other two get real, distinct backend
// logic in a later pass.
const MISSING_PERSON_BRIEFING: BriefingBullet[] = [
  {
    id: "missing-person-0",
    text: "세 구역에서 동시에 경보가 울렸어! 그런데 드론(감시 장비)은 하나뿐이라, 한 번에 한 곳만 확인할 수 있어.",
  },
  {
    id: "missing-person-1",
    text: "금고: 문 센서만 반응하고 움직임은 없음 · 서버실: 출입 카드 실패가 이어지다 결국 성공 · 임원실: 움직임 감지 + 창문 열림.",
  },
  {
    id: "missing-person-2",
    text: "셋 중 둘은 센서 오작동으로 인한 오경보고, 하나만 진짜 침입이야. 사진 속 인물을 서두르지 말고 단서부터 신중히 짚어가며 찾아줘.",
  },
];

export const SCENARIOS: Record<ScenarioId, ScenarioConfig> = {
  "saving-people": {
    id: "saving-people",
    buttonLabel: "인명 구조",
    title: "인명 구조",
    briefing: SCENARIO_BRIEFING,
    targetImage: TARGET_APPEARANCE.referenceImage,
    targetAlt: TARGET_APPEARANCE.referenceAlt,
    badgeLabel: "구조 필요",
  },
  "missing-person": {
    id: "missing-person",
    buttonLabel: "실종자 수색",
    title: "실종자 수색",
    briefing: MISSING_PERSON_BRIEFING,
    // Placeholder — no dedicated reference photo yet, reuse the existing
    // target image until a real asset is provided.
    targetImage: TARGET_APPEARANCE.referenceImage,
    targetAlt: TARGET_APPEARANCE.referenceAlt,
    badgeLabel: "수색 대상",
  },
  "safety-check": {
    id: "safety-check",
    buttonLabel: "안전 점검",
    title: "안전 점검",
    // Placeholder — exact duplicate of Saving People's content until this
    // scenario is designed for real.
    briefing: SCENARIO_BRIEFING,
    targetImage: TARGET_APPEARANCE.referenceImage,
    targetAlt: TARGET_APPEARANCE.referenceAlt,
    badgeLabel: "점검 대상",
  },
};

export const SCENARIO_LIST: ScenarioConfig[] = [
  SCENARIOS["saving-people"],
  SCENARIOS["safety-check"],
  SCENARIOS["missing-person"],
];
