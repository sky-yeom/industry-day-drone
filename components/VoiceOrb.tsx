"use client";

import type { VoiceStatus } from "@/lib/voiceClient";

interface VoiceOrbProps {
  status: VoiceStatus;
  /** Mic peak level, roughly 0–0.6 in practice. Drives the listening bars. */
  level: number;
}

/**
 * Audio-signal visualizer (equalizer-style bars) instead of a static shape,
 * styled to match the dashboard's pastel palette. Pure CSS, no deps.
 */
interface BarTheme {
  /** Bar fill, bottom → top. */
  gradient: string;
  /** CSS animation class applied per bar (idle/connecting/speaking/error
   * drive their own motion; listening is reactive to real mic level). */
  anim?: string;
}

const BAR_THEME: Record<VoiceStatus, BarTheme> = {
  idle: {
    gradient: "linear-gradient(180deg, #d9cdf0, #b6a0dd)",
    anim: "bar-idle",
  },
  connecting: {
    gradient: "linear-gradient(180deg, #c5b4e3, #8661c5)",
    anim: "bar-connecting",
  },
  listening: {
    gradient: "linear-gradient(180deg, #a688d8, #6a3fa0)",
  },
  speaking: {
    gradient: "linear-gradient(180deg, #8661c5, #5a3a95, #33205f)",
    anim: "bar-speaking",
  },
  error: {
    gradient: "linear-gradient(180deg, #8661c5, #33205f)",
    anim: "bar-error",
  },
};

// Resting bar heights (fraction of max), shaped like a little wave rather
// than a flat row, so it still reads as a waveform even at rest.
const RESTING_HEIGHTS = [0.35, 0.6, 0.85, 0.6, 0.35];
// Per-bar weighting + phase so reactive (listening) bars don't all move in
// lockstep with the single mic-level number.
const BAR_WEIGHTS = [0.6, 0.85, 1, 0.85, 0.6];

export default function VoiceOrb({ status, level }: VoiceOrbProps) {
  const theme = BAR_THEME[status];
  const isListening = status === "listening";
  const isError = status === "error";

  return (
    <div className="flex h-28 w-28 shrink-0 items-center justify-center gap-1.5 sm:h-40 sm:w-40 sm:gap-2">
      {RESTING_HEIGHTS.map((rest, i) => {
        const reactiveFrac = isListening
          ? Math.min(1, rest * 0.5 + level * 2.2 * BAR_WEIGHTS[i])
          : rest;
        const heightPct = isError ? rest * 55 : reactiveFrac * 100;

        return (
          <span
            key={i}
            aria-hidden="true"
            className={`w-2.5 rounded-full sm:w-3 ${isListening ? "transition-[height] duration-75" : ""} ${theme.anim ?? ""}`}
            style={{
              height: `${Math.max(12, heightPct)}%`,
              background: theme.gradient,
              animationDelay: theme.anim ? `${i * 0.12}s` : undefined,
              boxShadow: "0 4px 10px rgba(9,31,44,0.12)",
            }}
          />
        );
      })}
    </div>
  );
}
