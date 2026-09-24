import type { MonitorDestination, MonitorId, RoutePlanningState, ScenarioKind } from "@/lib/types";
import triageScenario from "@/data/emergency-triage.json";
import securityScenario from "@/data/security-breach.json";
import constructionScenario from "@/data/construction-safety.json";
import { isMonitorId } from "@/data/scenario";

// Each scenario gets its own map background art (Route screen + the
// map-unroll transition) matching its 3 monitor locations — a shared
// island map wouldn't make sense for e.g. an indoor security breach.
export const MAP_IMAGE_BY_KIND: Record<ScenarioKind, string> = {
  triage: "/gibby/map.png",
  security: "/gibby/security-map.png",
  construction: "/gibby/construction-map.png",
};

// Location display names shown throughout the Route/Images/Results screens
// (previously generic "현장 1/2/3" — replaced with the actual place, since
// with the pixel map showing real locations, numbered "site" labels read as
// redundant/confusing next to the map pins).
const MONITOR_LABELS_BY_KIND: Record<ScenarioKind, Record<string, string>> = {
  triage: {
    "monitor-1": "바다",
    "monitor-2": "잔해",
    "monitor-3": "불 난 집",
  },
  security: {
    "monitor-1": "금고",
    "monitor-2": "서버실",
    "monitor-3": "임원실",
  },
  construction: {
    "monitor-1": "위쪽 통로",
    "monitor-2": "기초 공사 구역",
    "monitor-3": "오른쪽 플랫폼",
  },
};

function buildMonitors(
  kind: ScenarioKind,
  scenario: typeof triageScenario | typeof securityScenario | typeof constructionScenario,
): MonitorDestination[] {
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
  security: buildMonitors("security", securityScenario),
  construction: buildMonitors("construction", constructionScenario),
};

export const MONITOR_MAP_BY_KIND: Record<ScenarioKind, Record<MonitorId, MonitorDestination>> = {
  triage: Object.fromEntries(MONITORS_BY_KIND.triage.map((monitor) => [monitor.id, monitor])) as Record<MonitorId, MonitorDestination>,
  security: Object.fromEntries(MONITORS_BY_KIND.security.map((monitor) => [monitor.id, monitor])) as Record<MonitorId, MonitorDestination>,
  construction: Object.fromEntries(MONITORS_BY_KIND.construction.map((monitor) => [monitor.id, monitor])) as Record<MonitorId, MonitorDestination>,
};


// Back-compat aliases for call sites that haven't been made scenario-aware
// yet — always the triage (119) scenario's data.
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
