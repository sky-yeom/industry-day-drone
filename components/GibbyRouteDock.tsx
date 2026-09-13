"use client";

import { ANCHOR_W, LAST_FRAME, gibbyFrameStyle } from "@/lib/gibbyMapSprite";
import { useTypewriter } from "@/lib/useTypewriter";

/**
 * Gibby stays visible (docked bottom-right, same spot as the prompt screen
 * and the map-finding transition) on the Route screen, frozen on the last
 * "looking at the map" pose from map-finding.png (not the idle/smile
 * sprite), with his speech bubble continuing to relay the agent's
 * transcript — consistent with how he behaves on the Prompt screen.
 */
export default function GibbyRouteDock({ agentText }: { agentText: string }) {
  const typed = useTypewriter(agentText);
  return (
    <>
      <div
        className="absolute bottom-[16%] z-20 flex items-end justify-center"
        style={{
          width: ANCHOR_W,
          left: `min(calc(94% - (145px * var(--ui-scale))), calc(100% - ${ANCHOR_W}px))`,
        }}
      >
        <div
          aria-hidden
          className="pixel-rendering shrink-0"
          style={{ ...gibbyFrameStyle(LAST_FRAME), transform: "scale(var(--ui-scale))", transformOrigin: "bottom center" }}
        />
      </div>
      {typed && (
        <div className="absolute bottom-[calc(16%+(196px*var(--ui-scale)))] z-20 right-[calc(6%+(161px*var(--ui-scale)))]">
          <div className="pixel-bubble pixel-bubble--right relative max-w-[16.25rem] px-4 py-3 text-sm leading-6 text-[#091f2c] sm:max-w-xs">
            {typed}
          </div>
        </div>
      )}
    </>
  );
}
