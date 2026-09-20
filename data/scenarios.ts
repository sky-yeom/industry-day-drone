import { SCENARIO_BRIEFING, TARGET_APPEARANCE } from "@/data/scenario";
import { SECURITY_SCENARIO_BRIEFING, SECURITY_TARGET_APPEARANCE } from "@/data/security-scenario";
import { CONSTRUCTION_SCENARIO_BRIEFING, CONSTRUCTION_TARGET_APPEARANCE } from "@/data/construction-scenario";
import type { BriefingBullet, ScenarioKind } from "@/lib/types";

export type ScenarioId = "saving-people" | "security-breach" | "safety-check";

export interface ScenarioConfig {
  id: ScenarioId;
  /** Which relay scenario kind this button launches (see `scenario` WS param). */
  kind: ScenarioKind;
  /** Label shown on the scenario-picker button. */
  buttonLabel: string;
  /** Title shown on the briefing/prompt screen. */
  title: string;
  briefing: BriefingBullet[];
  targetImage: string;
  targetAlt: string;
  /** Ribbon label over the target photo (e.g. "신고 대상"). */
  badgeLabel: string;
}

// All three scenarios are wired to real, independent relay/voice backends
// (see the `scenario` WS query param threaded from GibbyIntroSequence's
// onScenarioChosen).
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
  "security-breach": {
    id: "security-breach",
    kind: "security",
    buttonLabel: "보안 침입 감지",
    title: "보안 침입 감지",
    briefing: SECURITY_SCENARIO_BRIEFING,
    targetImage: SECURITY_TARGET_APPEARANCE.referenceImage,
    targetAlt: SECURITY_TARGET_APPEARANCE.referenceAlt,
    badgeLabel: "용의자",
  },
  "safety-check": {
    id: "safety-check",
    kind: "construction",
    buttonLabel: "건설 현장 안전 점검",
    title: "건설 현장 안전 점검",
    briefing: CONSTRUCTION_SCENARIO_BRIEFING,
    targetImage: CONSTRUCTION_TARGET_APPEARANCE.referenceImage,
    targetAlt: CONSTRUCTION_TARGET_APPEARANCE.referenceAlt,
    badgeLabel: "점검 대상",
  },
};

export const SCENARIO_LIST: ScenarioConfig[] = [
  SCENARIOS["saving-people"],
  SCENARIOS["safety-check"],
  SCENARIOS["security-breach"],
];

