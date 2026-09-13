"use client";

import Image from "next/image";
import { useCallback, useEffect, useRef, useState } from "react";
import { BOARDING_FRAMES, BOARDING_MIRRORED, LAST_BOARDING_FRAME } from "@/lib/gibbyDroneSprite";
import { ANCHOR_W, LAST_FRAME as LAST_MAP_FRAME, gibbyFrameStyle } from "@/lib/gibbyMapSprite";

const PRE_BOARD_MS = 800;
// How long each "getting on" frame holds before advancing to the next.
const BOARD_FRAME_MS = 380;
const SEATED_HOLD_MS = 800;
// Same per-frame timing GibbyMapTransition.tsx uses for the forward
// unroll animation (kept as a local duplicate rather than exported/shared,
// matching this file's existing pattern of small standalone timing
// constants) — reused here to play it in reverse.
const MAP_FRAME_MS = 400;
const MAP_UNROLL_EXTRA_MS = 220;

type Phase = "closing-map" | "preparing" | "boarding" | "seated";

/**
 * Route screen -> live map marker handoff: replaces GibbyRouteDock the
 * moment the mission launches, and stops rendering entirely once it's
 * done (the parent, app/page.tsx, only mounts this while
 * `missionLaunched && !boarded`).
 *
 * Phases:
 * 1. `closing-map`: Gibby is frozen at the dock on frame 7 of
 *    lib/gibbyMapSprite.ts's map-unroll sheet (same pose GibbyRouteDock
 *    freezes on) — this plays that same sprite in reverse (7 -> 0),
 *    putting the map away, before the drone even appears.
 * 2. `preparing`: hold the combined artwork briefly before boarding.
 * 3. `boarding`: Gibby plays through the 4-frame "getting on drone"
 *    sequence (public/gibby/drone-board-1..4.png, cropped from
 *    UI-images/drone riding.png), mirrored (see BOARDING_MIRRORED) since
 *    the reference art faces right but he's docked at the screen's right
 *    edge with the map to his left.
 * 4. `seated`: hold drone-board-4 at the corner before `onBoarded`
 *    fires and this component stops being rendered by the parent — from
 *    that point on there is no dock overlay at all; the same seated
 *    Gibby-on-drone sprite shrinks down and flies onto the map instead
 *    (see components/FlightPathMap.tsx's live marker), so there's only
 *    ever one drone/Gibby visual on screen, never a separate frozen dock
 *    copy plus a distinct map icon.
 */
export default function GibbyDroneBoarding({ onBoarded }: {
  onBoarded: () => void;
}) {
  const [phase, setPhase] = useState<Phase>("closing-map");
  const [mapFrame, setMapFrame] = useState(LAST_MAP_FRAME);
  const [frame, setFrame] = useState(0);
  const boardedFired = useRef(false);
  const onBoardedRef = useRef(onBoarded);

  useEffect(() => { onBoardedRef.current = onBoarded; }, [onBoarded]);

  useEffect(() => {
    if (phase !== "closing-map") return;
    if (mapFrame <= 0) {
      const id = window.setTimeout(() => setPhase("preparing"), 0);
      return () => window.clearTimeout(id);
    }
    const extra = mapFrame === 4 || mapFrame === 5 ? MAP_UNROLL_EXTRA_MS : 0;
    const id = window.setTimeout(() => setMapFrame((f) => f - 1), MAP_FRAME_MS + extra);
    return () => window.clearTimeout(id);
  }, [phase, mapFrame]);

  useEffect(() => {
    if (phase !== "preparing") return;
    const id = window.setTimeout(() => setPhase("boarding"), PRE_BOARD_MS);
    return () => window.clearTimeout(id);
  }, [phase]);

  useEffect(() => {
    if (phase !== "boarding") return;
    if (frame >= LAST_BOARDING_FRAME) {
      const id = window.setTimeout(() => setPhase("seated"), 0);
      return () => window.clearTimeout(id);
    }
    const id = window.setTimeout(() => setFrame((f) => f + 1), BOARD_FRAME_MS);
    return () => window.clearTimeout(id);
  }, [phase, frame]);

  const handleBoarded = useCallback(() => {
    if (boardedFired.current) return;
    boardedFired.current = true;
    onBoardedRef.current();
  }, []);

  useEffect(() => {
    if (phase !== "seated") return;
    const id = window.setTimeout(handleBoarded, SEATED_HOLD_MS);
    return () => window.clearTimeout(id);
  }, [phase, handleBoarded]);

  if (phase === "closing-map") {
    return (
      <div
        data-boarding-phase={phase}
        className="absolute bottom-[16%] z-20 flex items-end justify-center"
        style={{ width: ANCHOR_W, left: `min(calc(94% - 145px), calc(100% - ${ANCHOR_W}px))` }}
      >
        <div aria-hidden className="pixel-rendering shrink-0" style={gibbyFrameStyle(mapFrame)} />
      </div>
    );
  }

  const sprite = BOARDING_FRAMES[frame];
  const mirrorStyle = BOARDING_MIRRORED ? { transform: "scaleX(-1)" } : undefined;

  return (
    <div data-boarding-phase={phase}
      className="absolute bottom-[16%] left-[min(calc(94%-145px),calc(100%-300px))] z-20 flex w-[300px] items-end justify-center">
      <div className="pixel-rendering" style={{ width: sprite.width, height: sprite.height, ...mirrorStyle }}>
        <Image src={sprite.src} alt="" width={sprite.width} height={sprite.height} unoptimized loading="eager"
          className="pixel-rendering h-full w-full object-contain" />
      </div>
    </div>
  );
}
