"use client";

import { MONITOR_MAP } from "@/data/monitors";
import type { MissionScore } from "@/lib/types";

interface ResultsPanelProps {
  score: MissionScore | null;
  userPromptText: string;
}

/**
 * 임시(placeholder) 채점 결과. 실제 이미지 기반 판정이 아니라, 사용자가 말한
 * 주의사항과 정답 이상 징후 목록을 단순 키워드 매칭으로 비교한 것이다
 * (relay/survey.py의 score_prompt 참고). 실제 시나리오/정답 데이터가 정해지면
 * 채점 방식도 함께 교체될 예정이다.
 */
export default function ResultsPanel({ score, userPromptText }: ResultsPanelProps) {
  return (
    <section className="flex h-full w-full flex-col">
      <div className="flex items-center justify-between gap-4 px-5 pb-3 pt-5 sm:px-6 sm:pt-6">
        <div>
          <p className="mb-1 text-[10px] font-bold uppercase tracking-[0.2em] text-[#8661c5]">
            Mission debrief
          </p>
          <h2 className="text-lg font-semibold tracking-[-0.02em] text-[#091f2c]">
            결과
          </h2>
          <p className="mt-1 text-xs text-[#8c8279]">
            말씀하신 주의사항과 실제 발견된 이상 징후를 비교한 결과입니다.
          </p>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-3 pb-4 sm:px-4">
        {!score ? (
          <div className="flex h-full min-h-[300px] flex-col items-center justify-center gap-2 rounded-2xl border border-[#ded8ea] bg-[linear-gradient(145deg,#ffffff_0%,#f3effb_56%,#e7f4fc_100%)] text-center">
            <p className="text-sm text-[#8c8279]">아직 탐지 결과가 없습니다.</p>
          </div>
        ) : (
          <div className="space-y-4">
            <div className="rounded-2xl border border-[#ded8ea] bg-[linear-gradient(135deg,#8661c5,#463668)] p-6 text-center text-white shadow-[0_18px_36px_rgba(70,54,104,0.25)]">
              <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-white/70">
                Accuracy
              </p>
              <p className="mt-2 text-4xl font-bold tracking-[-0.03em]">
                {score.accuracyPercent}%
              </p>
              <p className="mt-2 text-xs text-white/70">
                {score.matchedCount} / {score.totalGroundTruth}개의 이상 징후를 맞추셨습니다.
              </p>
            </div>

            <div className="rounded-xl border border-[#ded8ea] bg-white/70 p-4">
              <p className="text-[10px] font-bold uppercase tracking-[0.16em] text-[#8c8279]">
                말씀하신 주의사항
              </p>
              <p className="mt-1 text-sm text-[#091f2c]">{userPromptText || "-"}</p>
            </div>

            <div className="grid gap-3 sm:grid-cols-2">
              <div className="rounded-xl border border-[#c5b4e3]/50 bg-[#eee8f7]/50 p-4">
                <p className="mb-2 flex items-center gap-2 text-[10px] font-bold uppercase tracking-[0.16em] text-[#463668]">
                  <span className="h-1.5 w-1.5 rounded-full bg-[#49c5b1]" />
                  맞춘 이상 징후
                </p>
                {score.matched.length === 0 ? (
                  <p className="text-xs text-[#8c8279]">없음</p>
                ) : (
                  <ul className="space-y-1.5">
                    {score.matched.map((a) => (
                      <li key={a.id} className="text-xs text-[#091f2c]">
                        {a.label}
                        <span className="ml-1 text-[#8c8279]">
                          ({MONITOR_MAP[a.monitorId].label})
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              <div className="rounded-xl border border-[#e1d3c7]/70 bg-[#fff8f3]/70 p-4">
                <p className="mb-2 flex items-center gap-2 text-[10px] font-bold uppercase tracking-[0.16em] text-[#7f5a1a]">
                  <span className="h-1.5 w-1.5 rounded-full bg-[#ffb900]" />
                  놓친 이상 징후
                </p>
                {score.missed.length === 0 ? (
                  <p className="text-xs text-[#8c8279]">없음</p>
                ) : (
                  <ul className="space-y-1.5">
                    {score.missed.map((a) => (
                      <li key={a.id} className="text-xs text-[#091f2c]">
                        {a.label}
                        <span className="ml-1 text-[#8c8279]">
                          ({MONITOR_MAP[a.monitorId].label})
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
