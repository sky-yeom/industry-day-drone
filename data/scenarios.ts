import { SCENARIO_BRIEFING, TARGET_APPEARANCE } from "@/data/scenario";
import type { BriefingBullet, ScenarioKind } from "@/lib/types";

export type ScenarioId = "saving-people";

export interface ScenarioConfig {
  id: ScenarioId;
  kind: ScenarioKind;
  buttonLabel: string;
  title: string;
  briefing: BriefingBullet[];
  targetImage: string;
  targetAlt: string;
  badgeLabel: string;
}

export const SCENARIOS: Record<ScenarioId, ScenarioConfig> = {
  "saving-people": {
    id: "saving-people",
    kind: "triage",
    buttonLabel: "인명 탐지·신고",
    title: "인명 탐지·신고",
    briefing: SCENARIO_BRIEFING,
    targetImage: TARGET_APPEARANCE.referenceImage,
    targetAlt: TARGET_APPEARANCE.referenceAlt,
    badgeLabel: "신고 대상",
  },
};

export const SCENARIO_LIST: ScenarioConfig[] = [SCENARIOS["saving-people"]];
