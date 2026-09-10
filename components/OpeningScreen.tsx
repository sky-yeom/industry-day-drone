"use client";

import { useEffect, useState } from "react";
import { SCENARIO_BRIEFING, SCENARIO_TITLE } from "@/data/scenario";
import Typewriter from "@/components/Typewriter";

const CALLSIGN = "RAVEN-1";

/** Live-ticking clock for the HUD readout. Starts from the render-time
 * value (may briefly mismatch between server and client, hence
 * suppressHydrationWarning on the rendered text below) and ticks every
 * second on the client only. */
function useClock() {
  const [time, setTime] = useState(() => new Date());
  useEffect(() => {
    const id = window.setInterval(() => setTime(new Date()), 1000);
    return () => window.clearInterval(id);
  }, []);
  return time;
}

export default function OpeningScreen({ onStart }: { onStart: () => void }) {
  const time = useClock();
  // How many briefing bullets have finished typing; gates the next bullet's
  // typewriter reveal so they play one after another instead of at once.
  const [revealedCount, setRevealedCount] = useState(0);
  const clockText = [time.getHours(), time.getMinutes(), time.getSeconds()]
    .map((value) => String(value).padStart(2, "0"))
    .join(":");

  return (
    <main className="relative flex min-h-dvh items-center justify-center overflow-hidden bg-[#f4f3f5] p-5">
      <div aria-hidden className="pointer-events-none absolute inset-0 overflow-hidden">
        <div className="siren-light siren-light--red" />
        <div className="siren-light siren-light--blue" />
      </div>
      <div aria-hidden className="dot-field pointer-events-none absolute inset-0 opacity-60 [mask-image:radial-gradient(circle_at_center,black,transparent_70%)]" />
      <div aria-hidden className="grain-overlay pointer-events-none absolute inset-0" />
      <div aria-hidden className="vignette-overlay pointer-events-none absolute inset-0" />

      <section className="opening-card-in relative w-full max-w-2xl rounded-3xl border border-white bg-white/90 p-8 shadow-xl sm:p-12">
        <span aria-hidden className="hud-corner hud-corner--tl" />
        <span aria-hidden className="hud-corner hud-corner--tr" />
        <span aria-hidden className="hud-corner hud-corner--bl" />
        <span aria-hidden className="hud-corner hud-corner--br" />
        <div aria-hidden className="scanline-overlay pointer-events-none absolute inset-0 rounded-3xl" />

        <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
          <p className="text-xs font-bold tracking-widest text-[#8661c5]">Microsoft Foundry · Industry Day</p>
          <div className="flex items-center gap-1.5 font-mono text-[11px] text-[#6e6575]">
            <span aria-hidden className="live-dot" />
            <span>LIVE</span>
            <span className="text-[#c5b4e3]">·</span>
            <span>{`CALLSIGN ${CALLSIGN}`}</span>
            <span className="text-[#c5b4e3]">·</span>
            <span suppressHydrationWarning>{clockText}</span>
          </div>
        </div>

        <h1 className="mt-5 text-4xl font-bold tracking-[-0.03em] text-[#091f2c]">{SCENARIO_TITLE}</h1>

        <ul className="mt-6 space-y-3 text-sm leading-7 text-gray-500">
          {SCENARIO_BRIEFING.map((bullet, index) => (
            <li key={bullet.id} className="flex items-start gap-3">
              <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-[#8661c5]/15 text-[11px] font-bold text-[#8661c5]">
                {index + 1}
              </span>
              <span>
                <Typewriter
                  text={bullet.text}
                  active={index <= revealedCount}
                  onDone={() => setRevealedCount((count) => Math.max(count, index + 1))}
                />
              </span>
            </li>
          ))}
        </ul>

        <div className="mt-7 flex justify-center">
          <button
            className="rounded-full bg-[#463668] px-10 py-3 font-semibold text-white transition-all duration-150 hover:scale-105 hover:shadow-lg active:scale-100"
            onClick={onStart}
          >
            출동
          </button>
        </div>
        <p className="mt-4 text-center text-xs leading-6 text-[#6e6575]">
          시작하면 마이크 사용 권한을 요청하고 음성 관제 세션에 연결합니다.
        </p>
      </section>
    </main>
  );
}
