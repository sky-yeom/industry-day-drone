"use client";

import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import type { ChatMessage } from "@/lib/types";

interface ChatPanelProps {
  messages: ChatMessage[];
  onSend: (prompt: string) => void;
  isThinking?: boolean;
  /** Voice controls, rendered directly under the panel heading. */
  toolbar?: ReactNode;
  /** Set when there is no live relay session, which disables the composer. */
  disabledReason?: string;
}

/**
 * Small chatbox showing the conversation between the user and the agent,
 * with an input box for submitting new prompts.
 */
export default function ChatPanel({
  messages,
  onSend,
  isThinking,
  toolbar,
  disabledReason,
}: ChatPanelProps) {
  const [draft, setDraft] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, isThinking]);

  const blocked = Boolean(disabledReason);

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = draft.trim();
    if (!trimmed || isThinking || blocked) return;
    onSend(trimmed);
    setDraft("");
  }

  return (
    <section className="flex h-full w-full flex-col">
      <div className="flex items-start justify-between gap-3 px-5 pb-3 pt-5 sm:px-6 sm:pt-6">
        <div>
          <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-[#8661c5]">
            Azure AI voice agent
          </p>
          <h2 className="text-lg font-semibold tracking-[-0.02em] text-[#091f2c]">
            Foundry Copilot
          </h2>
        </div>
        <div className="mt-1 flex gap-1" aria-hidden="true">
          <span className="h-2 w-2 rounded-full bg-[#8661c5]" />
          <span className="h-2 w-2 rounded-full bg-[#8dc8e8]" />
          <span className="h-2 w-2 rounded-full bg-[#49c5b1]" />
        </div>
      </div>

      {toolbar}

      <div
        ref={scrollRef}
        className="soft-scrollbar flex-1 space-y-3 overflow-y-auto px-4 py-4 sm:px-5"
      >
        {messages.length === 0 && !isThinking && (
          <div className="flex h-full min-h-64 flex-col justify-center px-2">
            <div className="mb-6 flex items-start gap-4">
              <span className="font-mono text-4xl font-light tracking-[-0.08em] text-[#c5b4e3]">
                01
              </span>
              <div className="pt-1">
                <h3 className="text-base font-semibold text-[#091f2c]">
                  첫 경유지를 정해볼까요?
                </h3>
                <p className="mt-1 max-w-72 text-xs leading-5 text-[#8c8279]">
                  음성 세션을 시작하거나 아래 문장으로 경로 계획을 시작하세요.
                </p>
              </div>
            </div>
            <div className="divide-y divide-[#e8e6df] border-y border-[#e8e6df]">
              {[
                "모니터 1부터 갈게요",
                "모니터 2를 먼저 확인해줘",
                "현재 경로 상태를 알려줘",
              ].map((prompt) => (
                <button
                  key={prompt}
                  type="button"
                  disabled={blocked}
                  onClick={() => onSend(prompt)}
                  className="group flex w-full items-center justify-between gap-4 py-3 text-left text-xs font-medium text-[#454142] transition-colors hover:text-[#463668] disabled:cursor-not-allowed disabled:opacity-45"
                >
                  <span>{prompt}</span>
                  <span
                    aria-hidden="true"
                    className="translate-x-0 text-[#8661c5] transition-transform group-hover:translate-x-1"
                  >
                    →
                  </span>
                </button>
              ))}
            </div>
          </div>
        )}
        {messages.map((msg) => (
          <div
            key={msg.id}
            className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
          >
            <div
              className={`max-w-[86%] px-3.5 py-2.5 text-sm leading-relaxed shadow-sm ${
                msg.role === "user"
                  ? "rounded-[18px_18px_4px_18px] bg-[#463668] text-white"
                  : "rounded-[18px_18px_18px_4px] border border-[#e1d3c7]/70 bg-white text-[#091f2c]"
              }`}
            >
              {msg.text}
            </div>
          </div>
        ))}
        {isThinking && (
          <div className="flex justify-start">
            <div className="flex items-center gap-1.5 rounded-[18px_18px_18px_4px] border border-[#e1d3c7]/70 bg-white px-4 py-3 shadow-sm">
              {[0, 1, 2].map((dot) => (
                <span
                  key={dot}
                  className="h-1.5 w-1.5 animate-pulse rounded-full bg-[#8661c5]"
                  style={{ animationDelay: `${dot * 140}ms` }}
                />
              ))}
            </div>
          </div>
        )}
      </div>

      <form
        onSubmit={handleSubmit}
        className="m-4 flex gap-2 rounded-xl border border-[#d7d2cb] bg-white/90 p-1.5 shadow-[0_8px_24px_rgba(42,68,111,0.07)] sm:m-5"
      >
        <input
          type="text"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder={disabledReason ?? "음성 대신 텍스트로 지시…"}
          disabled={isThinking || blocked}
          className="min-w-0 flex-1 bg-transparent px-3 py-2.5 text-sm text-[#091f2c] placeholder:text-[#8c8279] focus:outline-none disabled:opacity-60"
        />
        <button
          type="submit"
          className="grid h-10 w-10 shrink-0 place-items-center rounded-lg bg-[#0078d4] text-white shadow-[0_8px_20px_rgba(0,120,212,0.2)] transition hover:-translate-y-0.5 hover:bg-[#2a446f] active:translate-y-0 disabled:cursor-not-allowed disabled:opacity-35"
          disabled={!draft.trim() || isThinking || blocked}
          aria-label="메시지 전송"
        >
          <svg viewBox="0 0 24 24" aria-hidden="true" className="h-4 w-4">
            <path fill="currentColor" d="m4 4 17 8-17 8 3-7 8-1-8-1-3-7Z" />
          </svg>
        </button>
      </form>
    </section>
  );
}
