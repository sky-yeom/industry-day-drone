"""Fail-safe state machine and exact timeout policies."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .config import SafetyConfig


class State(str, Enum):
    IDLE = "IDLE"
    WAIT_FOR_TAKEOFF = "WAIT_FOR_TAKEOFF"
    INITIALIZE = "INITIALIZE"
    GIMBAL_DOWN = "GIMBAL_DOWN"
    LOCALIZE_START = "LOCALIZE_START"
    FOLLOW_LINE = "FOLLOW_LINE"
    GOAL_APPROACH = "GOAL_APPROACH"
    VISUAL_ALIGN = "VISUAL_ALIGN"
    HOVER = "HOVER"
    TAG_LOST = "TAG_LOST"
    NETWORK_LOST = "NETWORK_LOST"
    EMERGENCY_STOP = "EMERGENCY_STOP"


@dataclass
class Health:
    last_tag_s: float | None = None
    last_network_s: float | None = None


class SafetyFSM:
    def __init__(self, config: SafetyConfig) -> None:
        self.config = config
        self.state = State.IDLE
        self.health = Health()

    @property
    def must_hover(self) -> bool:
        return self.state in {
            State.IDLE, State.WAIT_FOR_TAKEOFF, State.HOVER, State.TAG_LOST,
            State.NETWORK_LOST, State.EMERGENCY_STOP,
        }

    def transition(self, state: State, now_s: float) -> None:
        del now_s
        if self.state is not State.EMERGENCY_STOP:
            self.state = state

    def emergency_stop(self, now_s: float) -> None:
        del now_s
        self.state = State.EMERGENCY_STOP

    def observe_tag(self, now_s: float) -> None:
        self.health.last_tag_s = now_s

    def observe_network(self, now_s: float) -> None:
        self.health.last_network_s = now_s

    def tag_scale(self, now_s: float) -> float:
        """Hold under 0.3 s, decelerate through 1.0 s, then hover."""
        if self.health.last_tag_s is None:
            return 0.0
        age = now_s - self.health.last_tag_s
        if age < self.config.tag_hold_s:
            return 1.0
        if age < self.config.tag_decelerate_s:
            return (self.config.tag_decelerate_s - age) / (
                self.config.tag_decelerate_s - self.config.tag_hold_s
            )
        self.transition(State.TAG_LOST, now_s)
        return 0.0

    def network_action(self, now_s: float) -> str:
        """Compute the PC mission FSM's network-loss action.

        The current Android diagnostic build intentionally has no independent
        time-based heartbeat watchdog; it releases on socket disconnect.
        """
        if self.health.last_network_s is None:
            return "disable"
        age = now_s - self.health.last_network_s
        if age > self.config.network_disable_s:
            self.transition(State.NETWORK_LOST, now_s)
            return "disable"
        if age > self.config.network_zero_s:
            self.transition(State.NETWORK_LOST, now_s)
            return "zero"
        return "continue"

    def check(self, now_s: float) -> State:
        if self.state in {State.IDLE, State.WAIT_FOR_TAKEOFF, State.EMERGENCY_STOP}:
            return self.state
        self.network_action(now_s)
        if self.state in {State.FOLLOW_LINE, State.GOAL_APPROACH, State.VISUAL_ALIGN}:
            self.tag_scale(now_s)
        return self.state
