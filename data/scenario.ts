import scenario from "@/data/emergency-triage.json";
import type { BriefingBullet, MissionState, MonitorId } from "@/lib/types";

export function isMonitorId(value: string): value is MonitorId {
  return value === "monitor-1" || value === "monitor-2" || value === "monitor-3";
}

export const SCENARIO_TITLE = scenario.title;
// Legacy single-target export retained for the pre-triage-board UI (picker
// card art) — points at the first site's target until the merged triage
// board (phase2-ui-merge) replaces these call sites with per-site art.
export const TARGET_APPEARANCE = scenario.people[0].targetAppearance;
export const SCENARIO_BRIEFING: BriefingBullet[] = scenario.briefing.map(
  (text, index) => ({ id: `briefing-${index}`, text }),
);

export const MONITOR_CLUES = Object.fromEntries(
  scenario.people.map((person) => [person.monitorId, person.clue]),
);

export const INITIAL_MISSION_STATE: MissionState = {
  runId: "",
  revision: 0,
  kind: "triage",
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
      deteriorationMs: person.initiallyInjured ? 0 : Math.max(0, person.deadlineMs - scenario.injuryWindowMs),
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

export interface TriageSite {
  monitorId: MonitorId;
  label: string;
  clue: string;
  image: string;
  referenceImage: string;
  referenceAlt: string;
  vulnerable: boolean;
}

// Static per-site art/copy for the merged triage board (3 site cards),
// keyed in scenario order (fire/sea/rubble). Live confirmation state
// (promptConfirmed/promptText/promptConfidence) comes from the relay
// snapshot's people[] — this only carries what never changes at runtime.
export const TRIAGE_SITES: TriageSite[] = scenario.people.map((person) => {
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
    vulnerable: person.vulnerable,
  };
});
