import PagedText from "@/components/PagedText";
import { OUTCOME_LABELS, type DashboardState } from "@/lib/types";

export default function ResultsPanel({ state, debrief, onReset }: {
  state: DashboardState; debrief: string; onReset: () => void;
}) {
  const score = state.score;
  const terminal = state.missionPhase === "complete" || state.missionPhase === "aborted";
  return <section className="flex h-full min-h-0 w-full flex-col gap-3 p-3">
    <div className="flex shrink-0 items-center justify-between gap-3">
      <div>
      <p className="text-[10px] font-bold tracking-[0.2em] text-[#8661c5]">작전 최종 설명</p>
      <h2 className="text-lg font-semibold text-[#091f2c]">결과</h2>
      </div>
      {terminal && <button type="button" onClick={onReset}
        className="rounded-full bg-[#463668] px-4 py-2 text-sm font-semibold text-white hover:bg-[#2a446f]">처음으로</button>}
    </div>
    {!terminal ? <div className="flex min-h-0 flex-1 items-center justify-center rounded-2xl border border-[#ded8ea] bg-[linear-gradient(145deg,#ffffff_0%,#f3effb_56%,#e7f4fc_100%)] p-4 text-center text-sm text-[#8c8279]">
      아직 구조 결과가 없습니다. 작전 종료 후 여기에 표시됩니다.
    </div> : <>
      <div className="shrink-0 rounded-2xl bg-[linear-gradient(135deg,#8661c5,#463668)] px-4 py-3 text-center text-white">
        <p className="text-[10px] font-bold tracking-[0.2em] text-white/70">{state.missionPhase === "aborted" ? "작전 중단" : "구조 작전 종료"}</p>
        <p className="mt-1 text-2xl font-bold">{score ? `${score.total}명 중 ${score.rescuedCount}명 구조` : "결과 확인 중"}</p>
      </div>
      <div className="grid shrink-0 grid-cols-3 gap-2">
        {state.people.map((person) => <article key={person.id} className={`rounded-xl border p-2 ${person.outcome === "too_late" ? "border-[#e1d3c7]/70 bg-[#fff8f3]/70" : "border-[#c5b4e3]/50 bg-[#eee8f7]/50"}`}>
          <h3 className="text-xs font-semibold text-[#463668]">모니터 {person.monitorId.slice(-1)}</h3>
          <p className="mt-1 text-[11px] text-[#6e6575]">{person.label}</p>
          <p className="mt-1 text-xs font-semibold text-[#091f2c]">{person.outcome ? OUTCOME_LABELS[person.outcome] : "미해결 · 작전 중단"}</p>
        </article>)}
      </div>
      <div className={`grid min-h-0 flex-1 gap-3 ${state.userPromptText ? "grid-cols-2" : "grid-cols-1"}`}>
        <div className="flex min-h-0 flex-col gap-2 rounded-xl border border-[#ded8ea] bg-white/70 p-3">
          <h3 className="shrink-0 text-xs font-semibold text-[#8661c5]">최종 작전 설명</h3>
          <div className="min-h-0 flex-1 text-sm leading-6 text-[#091f2c]">
            <PagedText label="최종 작전 설명" text={debrief || "최종 설명을 준비하고 있습니다."} />
          </div>
        </div>
        {state.userPromptText && <div className="flex min-h-0 flex-col gap-2 rounded-xl border border-[#ded8ea] bg-white/70 p-3">
          <h3 className="shrink-0 text-xs font-semibold text-[#8661c5]">확정한 탐지 프롬프트</h3>
          <div className="min-h-0 flex-1 text-sm leading-6 text-[#091f2c]">
            <PagedText label="결과의 탐지 프롬프트" text={state.userPromptText} />
          </div>
        </div>}
      </div>
      <p className="shrink-0 text-[10px] leading-4 text-[#6e6575]">마지막 5초 안에 구조하면 부상 상태로 집계됩니다. 모니터 2는 처음부터 부상이 있습니다. 부상 상태도 구조 성공이며 실제 의학적 판단은 아닙니다.</p>
    </>}
  </section>;
}
