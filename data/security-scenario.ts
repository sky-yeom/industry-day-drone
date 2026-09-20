import scenario from "@/data/security-breach.json";
import type { BriefingBullet, MissionState, MonitorId } from "@/lib/types";
import { isMonitorId } from "@/data/scenario";

export const SECURITY_SCENARIO_TITLE = scenario.title;
// One shared suspect description for every zone (unlike the triage
// scenario's per-site targetAppearance) — the reference photo/appearance is
// identical across people[], so any one entry's targetAppearance works here.
export const SECURITY_TARGET_APPEARANCE = scenario.people[0].targetAppearance;
export const SECURITY_SCENARIO_BRIEFING: BriefingBullet[] = scenario.briefing.map(
  (text, index) => ({ id: `security-briefing-${index}`, text }),
);

export const SECURITY_INITIAL_MISSION_STATE: MissionState = {
  runId: "",
  revision: 0,
  kind: "security",
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
      falseAlarm: person.falseAlarm,
      falseAlarmReveal: person.falseAlarmReveal,
    };
  }),
  captures: [],
  score: null,
  error: null,
};

export interface SecurityZone {
  monitorId: MonitorId;
  label: string;
  clue: string;
  image: string;
  referenceImage: string;
  referenceAlt: string;
  vulnerable: boolean;
  falseAlarm: boolean;
}

// Single-card stand-in for the prompt/confirm phase: unlike the triage
// scenario's 3 different people, this scenario has exactly one suspect
// described once by voice. Reuses monitor-1's shared promptConfirmed/
// promptText/promptConfidence (apply_prompt_to_all keeps all 3 people[]
// entries identical after confirmation) so TriageSiteCards' existing
// person-lookup-by-monitorId logic works unchanged with a 1-item list.
export const SECURITY_SUSPECT_SITE: SecurityZone = {
  monitorId: "monitor-1",
  label: "용의자",
  clue: "용의자가 어떤 모습인지 말해줘.",
  image: scenario.people[0].image,
  referenceImage: scenario.people[0].targetAppearance.referenceImage,
  referenceAlt: scenario.people[0].targetAppearance.referenceAlt,
  vulnerable: false,
  falseAlarm: false,
};

// Static per-zone art/copy for the merged triage board reused for this
// scenario, shaped like TriageSite (see data/scenario.ts) so the board
// components can render either scenario's site list interchangeably. There
// is only one shared suspect reference photo (see SECURITY_TARGET_APPEARANCE
// above), unlike the triage scenario's per-site reference photos.
export const SECURITY_ZONES: SecurityZone[] = scenario.people.map((person) => {
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
    vulnerable: false,
    falseAlarm: person.falseAlarm,
  };
});
