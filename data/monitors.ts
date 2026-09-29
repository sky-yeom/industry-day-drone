import type { MonitorDestination, MonitorId, RoutePlanningState, ScenarioKind } from "@/lib/types";
import triageScenario from "@/data/emergency-triage.json";
import { isMonitorId } from "@/data/scenario";

export const MAP_IMAGE_BY_KIND: Record<ScenarioKind, string> = {
  triage: "/gibby/map.png",
};

const MONITOR_LABELS_BY_KIND: Record<ScenarioKind, Record<string, string>> = {
  triage: {
    "monitor-1": "바다",
    "monitor-2": "잔해",
    "monitor-3": "불 난 집",
  },
};

function buildMonitors(kind: ScenarioKind, scenario: typeof triageScenario): MonitorDestination[] {
  const labels = MONITOR_LABELS_BY_KIND[kind];
  return scenario.people.map((person, index) => {
    if (!isMonitorId(person.monitorId)) {
      throw new Error(`Invalid scenario monitor: ${person.monitorId}`);
    }
    return {
      id: person.monitorId,
      label: labels[person.monitorId] ?? `현장 ${index + 1}`,
      shortLabel: String(index + 1),
      image: person.image,
      x: person.x,
      y: person.y,
    };
  });
}

export const MONITORS_BY_KIND: Record<ScenarioKind, MonitorDestination[]> = {
  triage: buildMonitors("triage", triageScenario),
};

export const MONITOR_MAP_BY_KIND: Record<ScenarioKind, Record<MonitorId, MonitorDestination>> = {
  triage: Object.fromEntries(MONITORS_BY_KIND.triage.map((monitor) => [monitor.id, monitor])) as Record<MonitorId, MonitorDestination>,
};

export const MONITORS = MONITORS_BY_KIND.triage;
export const MONITOR_MAP = MONITOR_MAP_BY_KIND.triage;

export const INITIAL_ROUTE_STATE: RoutePlanningState = {
  phase: "selecting-destinations",
  draftRoute: [],
  confirmedRoute: [],
};

export function formatRoute(route: MonitorId[], kind: ScenarioKind = "triage"): string {
  const map = MONITOR_MAP_BY_KIND[kind];
  return route.map((id) => map[id].shortLabel).join(" → ");
}
