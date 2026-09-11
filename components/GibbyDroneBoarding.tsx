"use client";

import Image from "next/image";
import { useCallback, useEffect, useRef, useState } from "react";
import { BOARDING_FRAMES, BOARDING_MIRRORED, LAST_BOARDING_FRAME } from "@/lib/gibbyDroneSprite";
import { ANCHOR_W, LAST_FRAME as LAST_MAP_FRAME, gibbyFrameStyle } from "@/lib/gibbyMapSprite";

// How long the drone takes to fly up from below the viewport to Gibby's
// dock (matches .drone-fly-in's own transition duration in globals.css —
// kept here too so the timeout that advances the phase lines up with the
// CSS transition actually finishing).
const FLY_IN_MS = 800;
// How long each "getting on" frame holds before advancing to the next.
const BOARD_FRAME_MS = 380;
// Same per-frame timing GibbyMapTransition.tsx uses for the forward
// unroll animation (kept as a local duplicate rather than exported/shared,
// matching this file's existing pattern of small standalone timing
// constants) — reused here to play it in reverse.
const MAP_FRAME_MS = 400;
const MAP_UNROLL_EXTRA_MS = 220;

type Phase = "closing-map" | "flying-in" | "boarding" | "seated";

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
 * 2. `flying-in`: a small drone flies up from below the viewport to
 *    Gibby's dock corner.
 * 3. `boarding`: Gibby plays through the 4-frame "getting on drone"
 *    sequence (public/gibby/drone-board-1..4.png, cropped from
 *    UI-images/drone riding.png), mirrored (see BOARDING_MIRRORED) since
 *    the reference art faces right but he's docked at the screen's right
 *    edge with the map to his left.
 * 4. `seated`: the instant the last boarding frame is reached, `onBoarded`
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
  const [droneArrived, setDroneArrived] = useState(false);
  const boardedFired = useRef(false);

  useEffect(() => {
    if (phase !== "closing-map") return;
    if (mapFrame <= 0) {
      const id = window.setTimeout(() => setPhase("flying-in"), 0);
      return () => window.clearTimeout(id);
    }
    const extra = mapFrame === 4 || mapFrame === 5 ? MAP_UNROLL_EXTRA_MS : 0;
    const id = window.setTimeout(() => setMapFrame((f) => f - 1), MAP_FRAME_MS + extra);
    return () => window.clearTimeout(id);
  }, [phase, mapFrame]);

  // Mount in the "below viewport" position first, then flip to "docked" a
  // frame later so the bottom-offset change is an actual CSS transition
  // instead of both classes landing together on the very first paint.
  useEffect(() => {
    if (phase !== "flying-in") return;
    const id = requestAnimationFrame(() => setDroneArrived(true));
    return () => cancelAnimationFrame(id);
  }, [phase]);

  useEffect(() => {
    if (phase !== "flying-in") return;
    const id = window.setTimeout(() => setPhase("boarding"), FLY_IN_MS);
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
    onBoarded();
  }, [onBoarded]);

  useEffect(() => {
    if (phase === "seated") handleBoarded();
  }, [phase, handleBoarded]);

  if (phase === "seated") return null;

  if (phase === "closing-map") {
    return (
      <div
        className="absolute bottom-[16%] left-[calc(94%-145px)] z-20 flex items-end justify-center"
        style={{ width: ANCHOR_W }}
      >
        <div aria-hidden className="pixel-rendering shrink-0" style={gibbyFrameStyle(mapFrame)} />
      </div>
    );
  }

  const shownFrame = phase === "flying-in" ? 0 : frame;
  const sprite = BOARDING_FRAMES[shownFrame];
  const mirrorStyle = BOARDING_MIRRORED ? { transform: "scaleX(-1)" } : undefined;

  return (
    <>
      {/* Gibby (idle -> "getting on" sequence), same dock spot
          GibbyRouteDock/the closing-map phase used. */}
      <div className="absolute bottom-[16%] left-[calc(94%-145px)] z-20 flex w-[300px] items-end justify-center">
        <div className="pixel-rendering" style={{ width: sprite.width, height: sprite.height, ...mirrorStyle }}>
          <Image src={sprite.src} alt="" width={sprite.width} height={sprite.height} unoptimized
            className="pixel-rendering h-full w-full object-contain" />
        </div>
      </div>

      {/* The standalone drone flying in from below, only shown before it
          merges into the combined "getting on drone" artwork. */}
      {phase === "flying-in" && (
        <div className={`drone-fly-in absolute left-[calc(94%-165px)] z-10 h-16 w-16 ${droneArrived ? "drone-fly-in--docked" : ""}`}>
          <Image src="/gibby/drone.png" alt="" width={64} height={64} unoptimized className="pixel-rendering h-full w-full object-contain" />
        </div>
      )}
    </>
  );
}
