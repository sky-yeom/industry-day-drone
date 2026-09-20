import scenario from "@/data/construction-safety.json";
import type { BriefingBullet, MissionState, MonitorId } from "@/lib/types";
import { isMonitorId } from "@/data/scenario";

export const CONSTRUCTION_SCENARIO_TITLE = scenario.title;
// One shared target description (hot-pink workwear, no hard hat) for every
// zone (unlike the triage scenario's per-site targetAppearance) — the
// reference photo/appearance is identical across people[], so any one
// entry's targetAppearance works here.
export const CONSTRUCTION_TARGET_APPEARANCE = scenario.people[0].targetAppearance;
export const CONSTRUCTION_SCENARIO_BRIEFING: BriefingBullet[] = scenario.briefing.map(
  (text, index) => ({ id: `construction-briefing-${index}`, text }),
);

export const CONSTRUCTION_INITIAL_MISSION_STATE: MissionState = {
  runId: "",
  revision: 0,
  kind: "construction",
  promptPhase: "briefing",
  activePromptMonitorId: "monitor-1",
  userPromptText: "",
  appearanceConstraints: [],
  unsupportedAppearance: [],
  promptConfidence: null,
  promptConfidenceReason: "",
  dangerOrder: [],
  vulnerableAdjustedOrder: [],
  missionPhase: "briefing",
  mode: "mock",
  elapsedMs: 0,
  clockRunning: false,
  activeMonitorId: null,
  people: scenario.people.map((person) => {
    if (!isMonitorId(person.monitorId)) {
      throw new Error(`Invalid scenario monitor: ${person.monitorId}`);
    }
    return {
      id: person.id,
      monitorId: person.monitorId,
      label: person.label,
      clue: person.clue,
      targetDescription: person.targetAppearance.description,
      initiallyInjured: person.initiallyInjured,
      vulnerable: person.vulnerable,
      deadlineMs: person.deadlineMs,
      deteriorationMs: Math.max(0, person.deadlineMs - scenario.injuryWindowMs),
      outcome: null,
      resolvedAtMs: null,
      captureId: null,
      attempts: 0,
      promptConfirmed: false,
      promptText: "",
      promptConfidence: null,
      promptConfidenceReason: "",
    };
  }),
  captures: [],
  score: null,
  error: null,
};

export interface ConstructionZone {
  monitorId: MonitorId;
  label: string;
  clue: string;
  image: string;
  referenceImage: string;
  referenceAlt: string;
}

// Single shared-target card, mirroring SecurityZone/SECURITY_SUSPECT_SITE:
// one description ("hot-pink workwear, no hard hat") applies to all 3
// zones, confirmed once by voice via apply_prompt_to_all.
export const CONSTRUCTION_TARGET_SITE: ConstructionZone = {
  monitorId: "monitor-1",
  label: "점검 대상",
  clue: "찾아야 할 사람이 어떤 모습인지 말해줘.",
  image: scenario.people[0].image,
  referenceImage: scenario.people[0].targetAppearance.referenceImage,
  referenceAlt: scenario.people[0].targetAppearance.referenceAlt,
};

// Static per-zone art/copy for the merged triage board reused for this
// scenario, shaped like TriageSite (see data/scenario.ts) so the board
// components can render any scenario's site list interchangeably. There is
// only one shared target reference photo (see CONSTRUCTION_TARGET_APPEARANCE
// above), unlike the triage scenario's per-site reference photos.
export const CONSTRUCTION_ZONES: ConstructionZone[] = scenario.people.map((person) => {
  if (!isMonitorId(person.monitorId)) {
    throw new Error(`Invalid scenario monitor: ${person.monitorId}`);
  }
  return {
    monitorId: person.monitorId,
    label: person.label,
    clue: person.clue,
    image: person.image,
    referenceImage: person.targetAppearance.referenceImage,
    referenceAlt: person.targetAppearance.referenceAlt,
  };
});
