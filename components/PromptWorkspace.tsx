import Image from "next/image";
import PagedText from "@/components/PagedText";
import { SCENARIO_BRIEFING, TARGET_APPEARANCE } from "@/data/scenario";

export default function PromptWorkspace({ confirmed, userPromptText }: {
  confirmed: boolean;
  userPromptText: string;
}) {
  return <section className="flex h-full min-h-0 w-full flex-col" style={{ containerType: "size" }}>
    <div className="shrink-0 px-4 py-3">
      <p className="mb-1 text-[10px] font-bold tracking-[0.2em] text-[#8661c5]">임무 브리핑</p>
      <h2 className="text-lg font-semibold tracking-[-0.02em] text-[#091f2c]">프롬프트</h2>
    </div>
    <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,0.8fr)_minmax(0,1.2fr)] gap-3 px-3 pb-3">
      <div className="flex min-h-0 flex-col gap-3 rounded-2xl border border-[#c5b4e3]/60 bg-white/70 p-3">
        <div className="relative min-h-0 flex-1">
          <Image src={TARGET_APPEARANCE.referenceImage} alt={TARGET_APPEARANCE.referenceAlt}
            fill sizes="(max-width: 768px) 40vw, 30vw" className="rounded-2xl object-contain" />
        </div>
        <div className="shrink-0 text-center">
          <p className="text-xs font-semibold text-[#8661c5]">찾아야 할 사람의 참고 사진</p>
          <p className="mt-1 text-sm font-semibold text-[#463668]">어떤 모습인지 말해주세요.</p>
        </div>
      </div>
      <div className="flex min-h-0 flex-col gap-3">
        <div className="flex min-h-0 flex-1 items-center rounded-2xl border border-[#ded8ea] bg-[#eee8f7]/50 p-3 text-[#091f2c]">
          <ol aria-label="임무 브리핑" className="space-y-2 leading-relaxed" style={{ fontSize: "clamp(11px, 1.4cqw, 14px)" }}>
            {SCENARIO_BRIEFING.map((bullet, index) => <li key={bullet.id} className="flex gap-2">
              <span className="shrink-0 font-semibold text-[#8661c5]">{index + 1}.</span>
              <span>{bullet.text}</span>
            </li>)}
          </ol>
        </div>
        <div className="flex h-[30%] min-h-0 shrink-0 flex-col gap-2 rounded-xl border border-[#8661c5]/40 bg-white/70 p-3">
          <p className="shrink-0 text-xs font-semibold text-[#8661c5]">확정된 탐지 프롬프트</p>
          <div className="min-h-0 flex-1 text-sm leading-6 text-[#463668]">
            {confirmed ? <PagedText label="확정된 탐지 프롬프트" text={userPromptText} />
              : <p className="text-xs leading-5 text-[#8c8279]">아직 확정된 프롬프트가 없습니다. 사진 속 인물의 모습을 말해주세요.</p>}
          </div>
        </div>
      </div>
    </div>
  </section>;
}
