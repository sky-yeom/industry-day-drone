import type { MonitorDestination, MonitorId, RoutePlanningState } from "@/lib/types";

export const MONITORS: MonitorDestination[] = [
  {
    id: "monitor-1",
    label: "Monitor 1",
    shortLabel: "1",
    image: "/monitors/monitor-1.svg",
    x: 20,
    y: 24,
  },
  {
    id: "monitor-2",
    label: "Monitor 2",
    shortLabel: "2",
    image: "/monitors/monitor-2.svg",
    x: 78,
    y: 28,
  },
  {
    id: "monitor-3",
    label: "Monitor 3",
    shortLabel: "3",
    image: "/monitors/monitor-3.svg",
    x: 50,
    y: 76,
  },
];

export const MONITOR_MAP = Object.fromEntries(
  MONITORS.map((monitor) => [monitor.id, monitor])
) as Record<MonitorId, MonitorDestination>;

export const INITIAL_ROUTE_STATE: RoutePlanningState = {
  phase: "selecting-destinations",
  selectedMonitorIds: [],
  draftRoute: [],
  confirmedRoute: [],
};

export function formatRoute(route: MonitorId[]): string {
  return route.map((id) => MONITOR_MAP[id].shortLabel).join(" → ");
}
