import type { MonitorDestination, MonitorId, RoutePlanningState } from "@/lib/types";
import scenario from "@/data/emergency-triage.json";
import { isMonitorId } from "@/data/scenario";

export const MONITORS: MonitorDestination[] = scenario.people.map((person, index) => {
  if (!isMonitorId(person.monitorId)) {
    throw new Error(`Invalid scenario monitor: ${person.monitorId}`);
  }
  return {
    id: person.monitorId,
    label: `모니터 ${index + 1}`,
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
