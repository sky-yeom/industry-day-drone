"use client";

import { SCENARIO_BRIEFING, SCENARIO_TITLE } from "@/data/scenario";

export default function OpeningScreen({ onStart }: { onStart: () => void }) {
  return (
    <main className="relative flex min-h-dvh items-center justify-center overflow-hidden bg-[#f4f3f5] p-5">
      <div aria-hidden className="pointer-events-none absolute -left-24 -top-24 h-96 w-96 rounded-full bg-[#8661c5]/25 blur-3xl" />
      <div aria-hidden className="pointer-events-none absolute -bottom-32 -right-16 h-[28rem] w-[28rem] rounded-full bg-[#c5b4e3]/40 blur-3xl" />
      <section className="opening-card-in relative w-full max-w-2xl rounded-3xl border border-white bg-white/90 p-8 shadow-xl sm:p-12">
        <span className="inline-block rounded-full bg-[#8661c5]/10 px-3 py-1 text-xs font-bold tracking-widest text-[#8661c5]">
          Microsoft Foundry · Industry Day
        </span>
        <h1 className="mt-5 text-4xl font-bold tracking-[-0.03em] text-[#091f2c]">{SCENARIO_TITLE}</h1>
        <ul className="mt-6 space-y-3 text-sm leading-7 text-gray-500">
          {SCENARIO_BRIEFING.map((bullet, index) => (
            <li key={bullet.id} className="flex items-start gap-3">
              <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-[#8661c5]/15 text-[11px] font-bold text-[#8661c5]">
                {index + 1}
              </span>
              <span>{bullet.text}</span>
            </li>
          ))}
        </ul>
        <div className="mt-7 flex justify-center">
          <button
            className="rounded-full bg-[#463668] px-10 py-3 font-semibold text-white transition-all duration-150 hover:scale-105 hover:shadow-lg active:scale-100"
            onClick={onStart}
          >
            시작
          </button>
        </div>
        <p className="mt-4 text-center text-xs leading-6 text-[#6e6575]">
          시작하면 마이크 사용 권한을 요청하고 음성 관제 세션에 연결합니다.
        </p>
      </section>
    </main>
  );
}
