"use client";

import Image from "next/image";
import { useEffect, useRef, useState } from "react";
import { MAP_MARKER_SRC } from "@/lib/gibbyDroneSprite";
import {
  markerCanvas, PARKED_DRONE, RESULTS_ASSETS, RESULTS_IDLE, RESULTS_SPRITE_HEIGHT,
  RESULTS_SPRITE_WIDTH, returnFrame, type MarkerRect,
} from "@/lib/gibbyResultsSprite";

export default function GibbyResultsTransition({ origin, celebrate, onReady, onError }: {
  origin: MarkerRect | null;
  celebrate: boolean;
  onReady: () => void;
  onError: (message: string) => void;
}) {
  const [elapsed, setElapsed] = useState(0);
  const [loaded, setLoaded] = useState(false);
  const callbacks = useRef({ onReady, onError });
  const readyFired = useRef(false);
  useEffect(() => { callbacks.current = { onReady, onError }; }, [onReady, onError]);

  useEffect(() => {
    let active = true;
    let frameId = 0;
    let start: number | null = null;
    let assetsSettled = false;
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    const ready = () => {
      if (readyFired.current || !active) return;
      readyFired.current = true;
      callbacks.current.onReady();
    };
    const tick = (now: number) => {
      if (!active) return;
      start ??= now;
      const next = !origin || reducedMotion.matches ? Infinity : now - start;
      setLoaded(true);
      setElapsed(next);
      if (returnFrame(next, celebrate).phase === "resting") ready();
      else frameId = requestAnimationFrame(tick);
    };
    const failAssets = () => {
      if (!active || assetsSettled) return;
      assetsSettled = true;
      window.clearTimeout(assetTimeout);
      callbacks.current.onError("기비의 귀환 애니메이션을 불러오지 못했습니다. 결과를 표시합니다.");
      setLoaded(true);
      setElapsed(Infinity);
      ready();
    };
    const assetTimeout = window.setTimeout(failAssets, 8000);
    const sources = !origin || reducedMotion.matches ? [RESULTS_IDLE, PARKED_DRONE] : RESULTS_ASSETS;
    const images = sources.map((src) => {
      const image = new window.Image();
      image.src = src;
      return image.decode();
    });
    void Promise.all(images).then(() => {
      if (!active || assetsSettled) return;
      assetsSettled = true;
      window.clearTimeout(assetTimeout);
      frameId = requestAnimationFrame(tick);
    }, failAssets);
    return () => { active = false; cancelAnimationFrame(frameId); window.clearTimeout(assetTimeout); };
  }, [origin, celebrate]);

  const frame = returnFrame(origin ? elapsed : Infinity, celebrate);
  const atOrigin = loaded && origin && frame.phase === "handoff";
  const waitingAtOrigin = !loaded && origin;

  return <div className="pointer-events-none absolute inset-0 z-[60]" aria-hidden="true" data-return-phase={frame.phase}>
    {waitingAtOrigin ? <div className="absolute" style={origin}>
      <Image src={MAP_MARKER_SRC} alt="" width={origin.width} height={origin.height} unoptimized
        className="pixel-rendering h-full w-full -scale-x-100" />
    </div> : <div className={`gibby-results-sprite ${frame.phase === "flying" ? "gibby-results-sprite--flying" : ""}`}
      style={atOrigin ? markerCanvas(origin) : undefined}>
      {frame.parked && <Image src={PARKED_DRONE} alt="" width={RESULTS_SPRITE_WIDTH} height={RESULTS_SPRITE_HEIGHT}
        unoptimized className="pixel-rendering absolute inset-0 h-full w-full" />}
      <Image src={frame.src} alt="" width={RESULTS_SPRITE_WIDTH} height={RESULTS_SPRITE_HEIGHT}
        unoptimized className="pixel-rendering absolute inset-0 h-full w-full" />
    </div>}
  </div>;
}
