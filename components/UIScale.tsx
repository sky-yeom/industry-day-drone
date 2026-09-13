"use client";

import { useEffect } from "react";

/**
 * Gibby's sprite sizes and dock/bubble offsets are tuned in fixed pixels
 * against this reference viewport width (a typical Mac laptop window).
 * `--ui-scale` lets every consumer (CSS classes and inline calc() strings)
 * scale those pixel values together so the whole composition — sprite
 * size, his distance from the screen edge, the ground height, the bubble
 * position/size — grows or shrinks as one unit on other screens instead
 * of drifting apart (small sprite, bubble far from it, etc.).
 */
const REFERENCE_WIDTH = 1440;
const MIN_SCALE = 0.6;
const MAX_SCALE = 1.3;

export default function UIScale() {
  useEffect(() => {
    const update = () => {
      const raw = window.innerWidth / REFERENCE_WIDTH;
      const scale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, raw));
      document.documentElement.style.setProperty("--ui-scale", scale.toFixed(4));
    };
    update();
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, []);
  return null;
}
