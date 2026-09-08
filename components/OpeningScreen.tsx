"use client";

interface OpeningScreenProps {
  onStart: () => void;
}

/**
 * 임시(placeholder) 상황 설명 화면. 실제 시나리오 문구는 나중에 이 파일만
 * 교체하면 된다. "브리핑 시작"을 누르면 대시보드로 넘어가면서 음성 세션도
 * 함께 자동으로 시작된다 (수동 마이크 버튼은 없음).
 */
export default function OpeningScreen({ onStart }: OpeningScreenProps) {
  return (
    <main className="relative flex min-h-dvh w-full items-center justify-center overflow-hidden bg-[linear-gradient(125deg,#f4f3f5_0%,#f4f3f5_62%,#eee8f7_100%)] px-4">
      <div className="dot-field pointer-events-none absolute right-0 top-0 h-[44%] w-[38%] opacity-40 [mask-image:linear-gradient(135deg,transparent,black)]" />
      <div className="dot-field pointer-events-none absolute bottom-0 left-0 h-[38%] w-[34%] opacity-30 [mask-image:linear-gradient(-45deg,transparent,black)]" />

      <div className="relative z-10 w-full max-w-xl rounded-[24px] border border-white bg-white/85 p-8 text-center shadow-[0_24px_60px_rgba(42,68,111,0.15)] backdrop-blur-xl sm:p-10">
        <div className="mb-4 flex items-center justify-center gap-2">
          <span className="text-[10px] font-bold uppercase tracking-[0.22em] text-[#8661c5]">
            Microsoft Foundry
          </span>
          <span className="h-1 w-1 rounded-full bg-[#49c5b1]" />
          <span className="text-[10px] font-medium uppercase tracking-[0.16em] text-[#8c8279]">
            Industry Day
          </span>
        </div>

        <h1 className="text-2xl font-semibold tracking-[-0.03em] text-[#091f2c] sm:text-3xl">
          드론 관제 임무 브리핑
        </h1>

        <p className="mx-auto mt-4 max-w-md text-sm leading-relaxed text-[#5c4738]">
          현장에 배치된 드론 한 대가 세 곳의 모니터 지점을 순서대로 촬영할 수
          있습니다. 브리핑을 시작하면 음성 관제 에이전트가 상황을 설명하고,
          무엇을 찾아야 하는지, 그리고 어디부터 살펴볼지 물어봅니다.
        </p>

        <button
          type="button"
          onClick={onStart}
          className="mt-8 inline-flex items-center justify-center gap-2 rounded-full bg-[#463668] px-8 py-3 text-sm font-semibold text-white shadow-[0_10px_24px_rgba(70,54,104,0.3)] transition hover:-translate-y-0.5 hover:bg-[#2a446f] active:translate-y-0"
        >
          브리핑 시작
        </button>

        <p className="mt-4 text-[11px] text-[#8c8279]">
          시작하면 마이크 사용 권한을 요청하고, 음성 세션이 자동으로 시작됩니다.
        </p>
      </div>
    </main>
  );
}
