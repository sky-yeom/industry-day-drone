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
  falseAlarm: boolean;
}

// Single-card stand-in for the prompt/confirm phase: like the security and
// construction scenarios, this scenario has exactly one real person (in one
// of the 3 sites — the other two are false alarms) described once by voice.
// Reuses monitor-1's shared promptConfirmed/promptText/promptConfidence
// (apply_prompt_to_all keeps all 3 people[] entries identical after
// confirmation) so TriageSiteCards' existing person-lookup-by-monitorId
// logic works unchanged with a 1-item list.
export const TRIAGE_TARGET_SITE: TriageSite = {
  monitorId: "monitor-1",
  label: "찾는 사람",
  clue: "찾는 사람이 어떤 모습인지 말해줘.",
  image: scenario.people[0].image,
  referenceImage: scenario.people[0].targetAppearance.referenceImage,
  referenceAlt: scenario.people[0].targetAppearance.referenceAlt,
  vulnerable: false,
  falseAlarm: false,
};
