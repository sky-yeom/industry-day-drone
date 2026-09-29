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

// The one site that actually has a person (the other two are false alarms).
// Derived from the raw scenario data since `MissionState.people` doesn't
// carry `falseAlarm` (only the raw JSON / `TriageSite` shape does).
const realPerson = scenario.people.find((person) => !person.falseAlarm) ?? scenario.people[0];
export const TRIAGE_REAL_MONITOR_ID: MonitorId = isMonitorId(realPerson.monitorId)
  ? realPerson.monitorId
  : "monitor-1";

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

// Single-card stand-in for the prompt/confirm phase: this scenario has one
// shared target description confirmed once by voice before the user chooses
// which of the three calls to check.
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
