"use client";

import { useEffect, useState } from "react";

/** Reveals `text` progressively (character by character) with a blinking
 * cursor while active, then calls `onDone` once fully revealed. Renders the
 * full text immediately (no animation) when the user prefers reduced
 * motion — checked once via a lazy `useState` initializer so it never
 * needs to call `setShown` from inside the effect for that case. */
export default function Typewriter({
  text,
  active = true,
  charIntervalMs = 18,
  onDone,
}: {
  text: string;
  active?: boolean;
  charIntervalMs?: number;
  onDone?: () => void;
}) {
  const [reduceMotion] = useState(
    () =>
      typeof window !== "undefined" &&
      Boolean(window.matchMedia?.("(prefers-reduced-motion: reduce)").matches),
  );
  const [shown, setShown] = useState(0);

  useEffect(() => {
    if (!active || reduceMotion) return;

    let index = 0;
    const intervalId = window.setInterval(() => {
      index += 1;
      setShown(index);
      if (index >= text.length) {
        window.clearInterval(intervalId);
        onDone?.();
      }
    }, charIntervalMs);

    return () => window.clearInterval(intervalId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text, active, reduceMotion]);

  useEffect(() => {
    if (active && reduceMotion) onDone?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, reduceMotion]);

  if (reduceMotion) return <span>{text}</span>;

  const done = shown >= text.length;
  return (
    <span>
      {text.slice(0, shown)}
      {active && !done && <span aria-hidden className="typewriter-cursor" />}
    </span>
  );
}
