"use client";

import { useEffect, useRef } from "react";

const TILE_ASPECT_RATIO = 1536 / 260;
const SCROLL_SPEED = 32 / 0.6;

export default function PixelGround({ scrolling = false, hidden = false }: { scrolling?: boolean; hidden?: boolean }) {
  const groundRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const ground = groundRef.current;
    if (!ground) return;

    // Loop over a full image tile without changing the original walking speed.
    const observer = new ResizeObserver(([entry]) => {
      const tileWidth = entry.contentRect.height * TILE_ASPECT_RATIO;
      ground.style.setProperty("--ground-tile-width", `${tileWidth}px`);
      ground.style.setProperty("--ground-scroll-duration", `${tileWidth / SCROLL_SPEED}s`);
    });
    observer.observe(ground);
    return () => observer.disconnect();
  }, []);

  // Pausing keeps the final offset instead of resetting the animation.
  // `hidden` permanently slides the ground down out of view once Gibby
  // boards the drone (components/GibbyDroneBoarding.tsx) — the mission
  // never needs it back, so this is a one-way transition rather than a
  // toggle.
  return (
    <div
      ref={groundRef}
      aria-hidden
      style={{
        height: "calc(16% + 35px)",
        zIndex: 50,
        animationPlayState: scrolling ? "running" : "paused",
      }}
      className={`pixel-scene-ground pixel-scene-ground--scrolling pixel-rendering pointer-events-none absolute inset-x-0 bottom-0 ${hidden ? "pixel-ground--hidden" : ""}`}
    />
  );
}
