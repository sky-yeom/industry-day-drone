"use client";

import { SCENARIO_BRIEFING, SCENARIO_TITLE } from "@/data/scenario";

export default function OpeningScreen({ onStart }: { onStart: () => void }) {
  return (
    <main className="flex min-h-dvh items-center justify-center bg-[#f4f3f5] p-5">
      <section className="w-full max-w-2xl rounded-3xl border border-white bg-white/90 p-8 shadow-xl sm:p-12">
        <p className="text-xs font-bold tracking-widest text-[#8661c5]">Microsoft Foundry · Industry Day</p>
        <h1 className="mt-4 text-3xl font-semibold text-[#091f2c]">{SCENARIO_TITLE}</h1>
        <ul className="mt-6 space-y-3 text-sm leading-7 text-[#5c4738]">
          {SCENARIO_BRIEFING.map((bullet) => <li key={bullet.id}>{bullet.text}</li>)}
        </ul>
        <div className="mt-7 flex justify-center">
          <button className="rounded-full bg-[#463668] px-6 py-3 font-semibold text-white" onClick={onStart}>시작</button>
        </div>
        <p className="mt-4 text-center text-xs leading-6 text-[#6e6575]">
          시작하면 마이크 사용 권한을 요청하고 음성 관제 세션에 연결합니다.
        </p>
      </section>
    </main>
  );
}
