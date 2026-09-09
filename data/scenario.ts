import scenario from "@/data/emergency-triage.json";
import type { BriefingBullet, MissionState, MonitorId } from "@/lib/types";

export function isMonitorId(value: string): value is MonitorId {
  return value === "monitor-1" || value === "monitor-2" || value === "monitor-3";
}

export const SCENARIO_TITLE = scenario.title;
export const TARGET_APPEARANCE = scenario.targetAppearance;
export const SCENARIO_BRIEFING: BriefingBullet[] = scenario.briefing.map(
  (text, index) => ({ id: `briefing-${index}`, text }),
);

export const MONITOR_CLUES = Object.fromEntries(
  scenario.people.map((person) => [person.monitorId, person.clue]),
);

export const INITIAL_MISSION_STATE: MissionState = {
  runId: "",
  revision: 0,
  promptPhase: "briefing",
  userPromptText: "",
  appearanceConstraints: [],
  unsupportedAppearance: [],
  missionPhase: "briefing",
  mode: "mock",
  droneControlMode: "mock",
  droneMissionId: null,
  droneState: "idle",
  droneStopState: "not_requested",
  droneErrorCode: null,
  activeVisitIndex: null,
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
      targetDescription: TARGET_APPEARANCE.description,
      initiallyInjured: person.initiallyInjured,
      deadlineMs: person.deadlineMs,
      deteriorationMs: person.initiallyInjured ? 0 : Math.max(0, person.deadlineMs - scenario.injuryWindowMs),
      outcome: null,
      resolvedAtMs: null,
      captureId: null,
      attempts: 0,
    };
  }),
  captures: [],
  score: null,
  error: null,
};
