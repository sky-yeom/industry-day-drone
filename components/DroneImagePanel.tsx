"use client";

import Image from "next/image";
import { useState } from "react";
import { MONITOR_MAP } from "@/data/monitors";
import type { CapturedImage } from "@/lib/types";

const CAPTURE_LABELS: Record<CapturedImage["status"], string> = {
  captured: "촬영 완료", analyzing: "분석 중", detected: "대상자 확인",
  "not-found": "대상자 미확인", error: "분석 오류",
};

export default function DroneImagePanel({ captures }: { captures: CapturedImage[] }) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [dimensions, setDimensions] = useState({ id: "", ratio: 1.6 });
  const current = captures.find((capture) => capture.id === selectedId) ?? captures.at(-1);
  const index = current ? captures.indexOf(current) : -1;
  const ratio = dimensions.id === current?.id ? dimensions.ratio : 1.6;
  const box = current?.evidence?.box;
  const validBox = box && box.every(Number.isFinite) && box[0] >= 0 && box[1] >= 0 &&
    box[2] > 0 && box[3] > 0 && box[0] + box[2] <= 1 && box[1] + box[3] <= 1;
  return <section className="flex h-full min-h-0 flex-col gap-2 p-3 sm:p-4">
    <div className="flex shrink-0 items-center justify-between gap-3">
      <div>
        <p className="text-[10px] font-bold tracking-[0.2em] text-white [text-shadow:2px_2px_0_#091f2c]">카메라 이미지 수신</p>
        <h2 className="text-lg font-semibold text-white [text-shadow:2px_2px_0_#091f2c]">드론 이미지</h2>
      </div>
      <span className="pixel-panel shrink-0 px-3 py-1.5 text-xs font-semibold text-[#7f5a1a]">
        {current ? CAPTURE_LABELS[current.status] : "수신 대기"}
      </span>
    </div>
      <div className="pixel-frame flex min-h-0 flex-1 flex-col overflow-hidden !p-0">
        <div className="flex shrink-0 flex-wrap items-center justify-between gap-1 bg-[#091f2c] px-3 py-2 text-[10px] text-white/80">
          <span>{current ? `${MONITOR_MAP[current.monitorId].label} · ${index + 1}차 촬영` : "촬영 이미지 수신 대기"}</span>
          <span className="tabular-nums">{current ? `촬영 ${(current.capturedAtMs / 1000).toFixed(1)}초` : "출발 전"}</span>
        </div>
        {!current ? <div className="flex min-h-0 flex-1 items-center justify-center p-4 text-center text-sm leading-6 text-[#8c8279]">
          출발 후 자동으로 촬영합니다.<br />카메라 캡처를 기다리고 있습니다.
        </div> : <div className="relative min-h-0 flex-1" style={{ containerType: "size" }}>
          <div className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2"
            style={{ width: `min(100cqw, ${ratio * 100}cqh)`, aspectRatio: ratio }}>
            <Image src={current.imageUrl} alt={`${MONITOR_MAP[current.monitorId].label}에서 실제 분석에 사용한 촬영 이미지`}
              fill unoptimized className="object-contain"
              onLoad={(event) => {
                const image = event.currentTarget;
                if (image.naturalWidth && image.naturalHeight) {
                  setDimensions({ id: current.id, ratio: image.naturalWidth / image.naturalHeight });
                }
              }} />
            {validBox && <div aria-label="탐지 근거에 포함된 대상자 위치" className="pointer-events-none absolute border-[3px] border-emerald-400"
              style={{ left: `${box[0] * 100}%`, top: `${box[1] * 100}%`, width: `${box[2] * 100}%`, height: `${box[3] * 100}%` }} />}
          </div>
        </div>}
      </div>
    {current && <nav className="flex shrink-0 items-center justify-between gap-2 text-xs" aria-label="촬영 기록">
      <button type="button" disabled={index <= 0} onClick={() => setSelectedId(captures[index - 1].id)} className="pixel-button bg-white px-3 py-1.5 text-[#091f2c] disabled:opacity-30">이전 촬영</button>
      <span className="tabular-nums text-[#091f2c] [text-shadow:1px_1px_0_#fff]">{index + 1} / {captures.length}</span>
      <button type="button" onClick={() => setSelectedId(null)} className="pixel-button bg-white px-3 py-1.5 text-[#091f2c]" aria-pressed={selectedId === null}>최신 촬영</button>
      <button type="button" disabled={index >= captures.length - 1} onClick={() => setSelectedId(captures[index + 1].id)} className="pixel-button bg-white px-3 py-1.5 text-[#091f2c] disabled:opacity-30">다음 촬영</button>
    </nav>}
    <p className="shrink-0 text-[10px] leading-4 text-[#091f2c] [text-shadow:1px_1px_0_#fff]">{current?.mode === "azure" ? "Azure 이미지 분석" : "모의 이미지 분석"} · 화면과 분석에 동일한 촬영 이미지를 사용합니다.</p>
  </section>;
}
