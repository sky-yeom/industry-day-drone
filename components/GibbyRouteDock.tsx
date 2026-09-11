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
        className="absolute bottom-[16%] left-[calc(94%-145px)] z-20 flex items-end justify-center"
        style={{ width: ANCHOR_W }}
      >
        <div aria-hidden className="pixel-rendering shrink-0" style={gibbyFrameStyle(LAST_FRAME)} />
      </div>
      {typed && (
        // Capped with min(): on short viewports, `16%+146px` pushes the
        // bubble up far enough to overlap the info cards in the right
        // column of FlightPathMap. Capping the offset (and keeping that
        // cap fairly low) means the bubble never climbs far above the
        // ground band, keeping it clear of those cards at any realistic
        // viewport height, while still sitting near Gibby on tall ones.
        <div className="absolute bottom-[min(calc(16%+146px),150px)] z-20 right-[calc(6%+161px)]">
          <div className="pixel-bubble pixel-bubble--right relative max-w-[260px] px-4 py-3 text-sm leading-6 text-[#091f2c] sm:max-w-xs">
            {typed}
          </div>
        </div>
      )}
    </>
  );
}
