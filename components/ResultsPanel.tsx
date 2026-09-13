"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import PagedText from "@/components/PagedText";
import { MONITOR_MAP } from "@/data/monitors";
import { OUTCOME_LABELS, type DashboardState } from "@/lib/types";

function ResultTextCard({ title, label, text, compact = false, sharedHeight, onHeightChange, onSpaceChange }: {
  title: string; label: string; text: string;
  compact?: boolean;
  sharedHeight: number;
  onHeightChange: (label: string, height: number) => void;
  onSpaceChange: (label: string, insufficient: boolean) => void;
}) {
  const slotRef = useRef<HTMLDivElement>(null);
  const cardRef = useRef<HTMLElement>(null);
  const titleRef = useRef<HTMLHeadingElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const [textHeight, setTextHeight] = useState(0);

  useEffect(() => {
    const slot = slotRef.current;
    const card = cardRef.current;
    const heading = titleRef.current;
    const content = contentRef.current;
    if (!slot || !card || !heading || !content) return;
    const measure = () => {
      const style = getComputedStyle(card);
      const chrome = ["paddingTop", "paddingBottom", "borderTopWidth", "borderBottomWidth", "rowGap"]
        .reduce((sum, key) => sum + (parseFloat(style.getPropertyValue(
          key.replace(/[A-Z]/g, letter => `-${letter.toLowerCase()}`)
        )) || 0), 0);
      const available = Math.max(0, Math.floor(slot.clientHeight - heading.getBoundingClientRect().height - chrome));
      setTextHeight(previous => previous === available ? previous : available);
      onHeightChange(label, heading.getBoundingClientRect().height + content.getBoundingClientRect().height + chrome);
      // One readable 24px line plus the 24px pager and its 4px gap.
      onSpaceChange(label, available < 52);
    };
    const observer = new ResizeObserver(measure);
    // Observe the allocated slot, not the content-sized card: pagination must not resize its own budget.
    observer.observe(slot);
    observer.observe(heading);
    observer.observe(content);
    measure();
    return () => observer.disconnect();
  }, [label, compact, onHeightChange, onSpaceChange]);

  return <div ref={slotRef} className="h-full min-h-0 min-w-0">
    <article ref={cardRef} style={{ minHeight: sharedHeight }}
      className={`pixel-panel flex min-w-0 flex-col bg-white ${compact ? "gap-1 p-1.5" : "gap-2 p-2"}`}>
      <h3 ref={titleRef} className="text-xs font-semibold leading-4 text-[#091f2c]">{title}</h3>
      <div ref={contentRef} className="min-w-0 text-sm leading-6 text-[#091f2c]">
        <PagedText label={label} text={text} contentFit maxHeight={textHeight} />
      </div>
    </article>
  </div>;
}

export default function ResultsPanel({ state, debrief, onReset, visible = true }: {
  state: DashboardState; debrief: string; onReset: () => void; visible?: boolean;
}) {
  const score = state.score;
  const terminal = state.missionPhase === "complete" || state.missionPhase === "aborted";
  const panelRef = useRef<HTMLElement>(null);
  const [compactColumns, setCompactColumns] = useState(false);
  const [compactSummary, setCompactSummary] = useState(false);
  const [spaceWarnings, setSpaceWarnings] = useState<Record<string, boolean>>({});
  const [cardHeights, setCardHeights] = useState<Record<string, number>>({});
  const onHeightChange = useCallback((label: string, height: number) => {
    setCardHeights(previous => previous[label] === height ? previous : { ...previous, [label]: height });
  }, []);
  const sharedHeight = Math.max(cardHeights["최종 작전 설명"] || 0,
    state.userPromptText ? cardHeights["결과의 탐지 프롬프트"] || 0 : 0);
  const onSpaceChange = useCallback((label: string, insufficient: boolean) => {
    setSpaceWarnings(previous => previous[label] === insufficient ? previous : { ...previous, [label]: insufficient });
  }, []);
  const insufficientSpace = spaceWarnings["최종 작전 설명"]
    || (Boolean(state.userPromptText) && spaceWarnings["결과의 탐지 프롬프트"]);

  useEffect(() => {
    const panel = panelRef.current;
    if (!panel) return;
    const measure = () => {
      // Short phone/landscape regions need columns; tall narrow regions can afford stacked cards.
      setCompactColumns(panel.clientWidth >= 300 && panel.clientWidth < 640 && panel.clientHeight < 460);
      setCompactSummary(panel.clientWidth >= 640 && panel.clientHeight < 360);
    };
    const observer = new ResizeObserver(measure);
    observer.observe(panel);
    measure();
    return () => observer.disconnect();
  }, []);

  return <section ref={panelRef} className={`flex h-full min-h-0 w-full flex-col gap-2 p-2 ${compactSummary ? "" : "sm:p-3"}`}>
    <div className="flex shrink-0 items-center justify-between gap-3">
      <div className="flex items-baseline gap-2">
      <p className="text-[0.625rem] font-bold tracking-[0.2em] text-[#091f2c]">작전 최종 설명</p>
      <h2 className="text-lg font-semibold text-[#091f2c]">결과</h2>
      </div>
      {terminal && <button type="button" onClick={onReset}
        className={`pixel-button bg-[#ffd23f] px-4 text-sm font-semibold text-[#091f2c] ${compactSummary ? "py-1 leading-5" : "py-2"}`}>처음으로</button>}
    </div>
    {!terminal ? <div hidden={!visible} className="pixel-panel flex min-h-0 flex-1 items-center justify-center bg-white p-4 text-center text-sm text-[#091f2c]">
      아직 구조 결과가 없습니다. 작전 종료 후 여기에 표시됩니다.
    </div> : <div aria-hidden={!visible} inert={!visible}
      className={`relative min-h-0 flex-1 transition-opacity duration-500 motion-reduce:transition-none ${visible ? "opacity-100" : "pointer-events-none opacity-0"}`}>
      {insufficientSpace && <p role="status" className="absolute inset-0 flex items-center justify-center text-center text-sm leading-6 text-[#091f2c]">
        결과를 표시할 공간이 부족합니다. 창 높이를 늘리거나 화면을 가로로 돌려 주세요.
      </p>}
      <div aria-hidden={insufficientSpace || undefined} inert={Boolean(insufficientSpace)}
        className={`flex h-full min-h-0 flex-col gap-2 ${insufficientSpace ? "invisible overflow-hidden" : ""}`}>
      <div className="flex shrink-0 flex-col gap-2">
      <div className={`pixel-panel flex w-full shrink-0 flex-col items-center bg-[#ffd23f] text-center ${compactSummary ? "px-2 py-1" : "px-3 py-2"}`}>
        <p className="text-[0.625rem] font-bold leading-3 tracking-[0.2em] text-[#091f2c]">{state.missionPhase === "aborted" ? "작전 중단" : "구조 작전 종료"}</p>
        <p className={`${compactSummary ? "text-lg leading-6" : "text-xl"} font-bold text-[#091f2c]`}>{score ? `${score.total}명 중 ${score.rescuedCount}명 구조` : "결과 확인 중"}</p>
      </div>
      <div className="grid shrink-0 grid-cols-3 items-start gap-2">
        {state.people.map((person) => <article key={person.id} className={`pixel-panel min-w-0 [overflow-wrap:anywhere] ${compactSummary ? "p-1" : "p-1.5"}`}>
          <div className={compactSummary ? "flex flex-wrap items-baseline gap-x-2" : undefined}>
          <h3 className="text-xs font-semibold text-[#091f2c]">{MONITOR_MAP[person.monitorId].label}</h3>
          <p className={`${compactSummary ? "" : "mt-1"} text-[0.6875rem] text-[#091f2c]`}>{person.label}</p>
          </div>
          <p className={`${compactSummary ? "mt-0.5" : "mt-1"} text-xs font-semibold text-[#091f2c]`}>{person.outcome ? OUTCOME_LABELS[person.outcome] : "미해결 · 작전 중단"}</p>
        </article>)}
      </div>
      </div>
      <div className={`grid min-h-0 flex-1 items-start gap-2 ${state.userPromptText
        ? (compactColumns ? "grid-cols-2 grid-rows-1" : "grid-cols-1 grid-rows-2 sm:grid-cols-2 sm:grid-rows-1")
        : "grid-cols-1 grid-rows-1"}`}>
        <ResultTextCard title="최종 작전 설명" label="최종 작전 설명"
          text={debrief || "작전 결과를 정리하고 있어!"} compact={compactSummary}
          sharedHeight={sharedHeight} onHeightChange={onHeightChange} onSpaceChange={onSpaceChange} />
        {state.userPromptText && <ResultTextCard title="확정한 탐지 프롬프트" label="결과의 탐지 프롬프트"
          text={state.userPromptText} compact={compactSummary}
          sharedHeight={sharedHeight} onHeightChange={onHeightChange} onSpaceChange={onSpaceChange} />}
      </div>
      </div>
    </div>}
  </section>;
}
