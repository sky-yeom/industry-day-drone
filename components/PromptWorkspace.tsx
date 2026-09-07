"use client";

import { useEffect, useRef } from "react";
import { SCENARIO_BRIEFING } from "@/data/scenario";
import type { ChatMessage } from "@/lib/types";

interface PromptWorkspaceProps {
  transcript: ChatMessage[];
  userPromptText: string;
  confirmed: boolean;
}

/**
 * 브리핑 카드 + 실시간 STT 대화 로그.
 *
 * 음성 입력은 여전히 오른쪽 오브(VoiceControl)에서 이뤄진다. 이 패널은 읽기
 * 전용으로, 사용자가 한 말이 텍스트로 잘 옮겨지고 있는지 보여주는 용도다.
 */
export default function PromptWorkspace({
  transcript,
  userPromptText,
  confirmed,
}: PromptWorkspaceProps) {
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [transcript]);

  return (
    <section className="flex h-full w-full flex-col">
      <div className="px-5 pb-3 pt-5 sm:px-6 sm:pt-6">
        <p className="mb-1 text-[10px] font-bold uppercase tracking-[0.2em] text-[#8661c5]">
          Mission briefing
        </p>
        <h2 className="text-lg font-semibold tracking-[-0.02em] text-[#091f2c]">
          프롬프트
        </h2>
        <p className="mt-1 text-xs text-[#8c8279]">
          에이전트가 임무를 설명하면, 주의 깊게 볼 점을 말씀해주세요.
        </p>
      </div>

      <div className="px-3 sm:px-4">
        <ul className="space-y-2 rounded-2xl border border-[#ded8ea] bg-[#eee8f7]/50 p-4">
          {SCENARIO_BRIEFING.map((bullet, index) => (
            <li key={bullet.id} className="flex items-start gap-3 text-sm text-[#091f2c]">
              <span className="mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-full bg-[#8661c5] text-[10px] font-bold text-white">
                {index + 1}
              </span>
              <span className="leading-relaxed">{bullet.text}</span>
            </li>
          ))}
        </ul>

        {confirmed && (
          <div className="mt-3 flex items-start gap-2 rounded-xl border border-[#8661c5]/40 bg-white/70 px-4 py-3">
            <span className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-[#49c5b1]" />
            <p className="text-xs leading-relaxed text-[#463668]">
              <span className="font-semibold">확정된 주의사항: </span>
              {userPromptText}
            </p>
          </div>
        )}
      </div>

      <div
        ref={scrollRef}
        className="soft-scrollbar min-h-0 flex-1 space-y-3 overflow-y-auto px-4 py-4 sm:px-5"
      >
        {transcript.length === 0 && (
          <p className="px-2 text-xs text-[#8c8279]">
            음성 세션이 시작되면 대화 내용이 여기에 텍스트로 표시됩니다.
          </p>
        )}
        {transcript.map((msg) => (
          <div
            key={msg.id}
            className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
          >
            <div
              className={`max-w-[86%] px-3.5 py-2.5 text-sm leading-relaxed shadow-sm ${
                msg.role === "user"
                  ? "rounded-[18px_18px_4px_18px] bg-[#463668] text-white"
                  : "rounded-[18px_18px_18px_4px] border border-[#ded8ea] bg-white text-[#091f2c]"
              }`}
            >
              {msg.text}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
