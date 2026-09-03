import { formatRoute, INITIAL_ROUTE_STATE, MONITOR_MAP } from "@/data/monitors";
import type { AgentResponse, MonitorId, RoutePlanningState } from "@/lib/types";

const MONITOR_IDS: MonitorId[] = ["monitor-1", "monitor-2", "monitor-3"];
const POSITIVE_CONFIRMATION =
  /^(yes|yeah|yep|sure|y|confirm|confirmed|correct|proceed|looks good|that is right|that's right)\b/i;
const NEGATIVE_CONFIRMATION =
  /^(no|nope|n|reject|restart|start over|incorrect|wrong)\b/i;
const MONITOR_NUMBER: Record<string, MonitorId> = {
  "1": "monitor-1",
  one: "monitor-1",
  "2": "monitor-2",
  two: "monitor-2",
  "3": "monitor-3",
  three: "monitor-3",
};

function parseMonitorIds(prompt: string): MonitorId[] {
  if (/\ball(?:\s+three)?\s+monitors?\b/i.test(prompt)) {
    return MONITOR_IDS;
  }

  const matches = prompt.matchAll(
    /\b(?:monitor\s*)?(1|2|3|one|two|three)\b/gi
  );
  return Array.from(
    matches,
    (match) => MONITOR_NUMBER[match[1].toLowerCase()]
  );
}

function monitorList(ids: MonitorId[]): string {
  return ids.map((id) => MONITOR_MAP[id].label).join(", ");
}

function resetResponse(): AgentResponse {
  return {
    reply: "No problem — I cleared that route. Which monitors would you like the drone to visit?",
    state: { ...INITIAL_ROUTE_STATE },
    anomalies: [],
  };
}

function selectDestinations(prompt: string): AgentResponse {
  const enteredIds = parseMonitorIds(prompt);

  if (enteredIds.length === 0) {
    return {
      reply: "Which monitor should the drone visit first: Monitor 1, Monitor 2, or Monitor 3?",
      state: { ...INITIAL_ROUTE_STATE },
      anomalies: [],
    };
  }

  if (enteredIds.length > 1) {
    return {
      reply: "Please choose just one monitor for the first stop.",
      state: { ...INITIAL_ROUTE_STATE },
      anomalies: [],
    };
  }

  const firstMonitor = enteredIds[0];
  const remaining = MONITOR_IDS.filter((id) => id !== firstMonitor);

  return {
    reply:
      `${MONITOR_MAP[firstMonitor].label} will be the first stop. ` +
      `Which monitor should be second: ${monitorList(remaining)}?`,
    state: {
      phase: "selecting-order",
      selectedMonitorIds: [firstMonitor],
      draftRoute: [firstMonitor],
      confirmedRoute: [],
    },
    anomalies: [],
  };
}

function selectOrder(prompt: string, state: RoutePlanningState): AgentResponse {
  const enteredIds = parseMonitorIds(prompt);
  if (enteredIds.length === 0) {
    const remaining = MONITOR_IDS.filter((id) => !state.draftRoute.includes(id));
    return {
      reply: `Choose the next stop from ${monitorList(remaining)}.`,
      state,
      anomalies: [],
    };
  }

  if (enteredIds.length > 1) {
    return {
      reply: "Please choose only one monitor at a time for the next stop.",
      state,
      anomalies: [],
    };
  }

  const nextMonitor = enteredIds[0];
  if (state.draftRoute.includes(nextMonitor)) {
    const remaining = MONITOR_IDS.filter((id) => !state.draftRoute.includes(id));
    return {
      reply:
        `${MONITOR_MAP[nextMonitor].label} is already in the route. ` +
        `Choose from ${monitorList(remaining)}.`,
      state,
      anomalies: [],
    };
  }

  const draftRoute = [...state.draftRoute, nextMonitor];
  const selectedMonitorIds = draftRoute;

  if (draftRoute.length < MONITOR_IDS.length) {
    const remaining = MONITOR_IDS.filter((id) => !draftRoute.includes(id));
    return {
      reply:
        `Draft route: ${formatRoute(draftRoute)}. ` +
        `Which monitor should be third: ${monitorList(remaining)}?`,
      state: { ...state, selectedMonitorIds, draftRoute },
      anomalies: [],
    };
  }

  return {
    reply: `Your proposed route is ${formatRoute(draftRoute)}. Is this the path you want to take?`,
    state: {
      ...state,
      phase: "awaiting-confirmation",
      selectedMonitorIds,
      draftRoute,
    },
    anomalies: [],
  };
}

function confirmOrder(prompt: string, state: RoutePlanningState): AgentResponse {
  const trimmed = prompt.trim();

  if (POSITIVE_CONFIRMATION.test(trimmed)) {
    return {
      reply: `Confirmed — the full route is ${formatRoute(state.draftRoute)}.`,
      state: {
        ...state,
        phase: "confirmed",
        confirmedRoute: state.draftRoute,
      },
      anomalies: [],
    };
  }

  if (NEGATIVE_CONFIRMATION.test(trimmed)) {
    return resetResponse();
  }

  return {
    reply: `Please answer yes to confirm ${formatRoute(state.draftRoute)}, or no to start over.`,
    state,
    anomalies: [],
  };
}

/**
 * Processes one deterministic conversation turn. This is the single integration
 * point that can later be replaced by a real speech or LLM agent.
 */
export function getAgentResponse(
  prompt: string,
  state: RoutePlanningState
): AgentResponse {
  if (state.phase === "selecting-destinations") {
    return selectDestinations(prompt);
  }
  if (state.phase === "selecting-order") {
    return selectOrder(prompt, state);
  }
  if (state.phase === "awaiting-confirmation") {
    return confirmOrder(prompt, state);
  }

  if (NEGATIVE_CONFIRMATION.test(prompt.trim()) || /\b(new route|start over|restart)\b/i.test(prompt)) {
    return resetResponse();
  }

  return {
    reply:
      `The confirmed route is ${formatRoute(state.confirmedRoute)}. ` +
      'Say "start over" when you want to plan another route.',
    state,
    anomalies: [],
  };
}
