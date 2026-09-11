import type { MonitorDestination, MonitorId, RoutePlanningState } from "@/lib/types";
import scenario from "@/data/emergency-triage.json";
import { isMonitorId } from "@/data/scenario";

// Location display names shown throughout the Route/Images/Results screens
// (previously generic "현장 1/2/3" — replaced with the actual place, since
// with the pixel map showing real locations, numbered "site" labels read as
// redundant/confusing next to the map pins).
const MONITOR_LABELS: Record<string, string> = {
  "monitor-1": "바다",
  "monitor-2": "잔해",
  "monitor-3": "불 난 집",
};

export const MONITORS: MonitorDestination[] = scenario.people.map((person, index) => {
  if (!isMonitorId(person.monitorId)) {
    throw new Error(`Invalid scenario monitor: ${person.monitorId}`);
  }
  return {
    id: person.monitorId,
    label: MONITOR_LABELS[person.monitorId] ?? `현장 ${index + 1}`,
    shortLabel: String(index + 1),
    image: person.image,
    x: person.x,
    y: person.y,
  };
});

export const MONITOR_MAP = Object.fromEntries(
  MONITORS.map((monitor) => [monitor.id, monitor])
) as Record<MonitorId, MonitorDestination>;

export const INITIAL_ROUTE_STATE: RoutePlanningState = {
  phase: "selecting-destinations",
  draftRoute: [],
  confirmedRoute: [],
};

export function formatRoute(route: MonitorId[]): string {
  return route.map((id) => MONITOR_MAP[id].shortLabel).join(" → ");
}
