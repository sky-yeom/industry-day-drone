"use client";

/**
 * Operator-only manual override: always visible top-right on the two
 * screens that wait on the participant's voice (prompt confirmation,
 * route/departure confirmation), so a mic/venue-audio failure never
 * strands the booth. Drives the exact same relay tool pipeline a real
 * voice confirmation would (see the `onForceNext` callers in
 * PixelPromptScreen/FlightPathMap for the tool sequence), so it can't
 * desync mission state — it only substitutes *how* the confirmation
 * arrives, not what gets confirmed.
 */
export default function ForceNextButton({
  onClick,
  label = "다음으로 넘어가기 (강제 진행)",
  disabled,
}: {
  onClick: () => void;
  label?: string;
  disabled?: boolean;
}) {
  return (
    <div className="absolute right-4 top-4 z-30 sm:right-6 sm:top-6">
      <button
        type="button"
        onClick={onClick}
        disabled={disabled}
        className="pixel-button bg-[#ffd23f] px-3 py-1.5 text-xs font-semibold text-[#091f2c] disabled:cursor-not-allowed disabled:opacity-50"
      >
        {label}
      </button>
    </div>
  );
}
