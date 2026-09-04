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
    <div className="flex h-full w-full flex-col bg-zinc-950">
      <div className="border-b border-zinc-800 px-4 py-3">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-zinc-200">
          에이전트 대화
        </h2>
      </div>

      {toolbar}

      <div ref={scrollRef} className="flex-1 space-y-2 overflow-y-auto px-3 py-3">
        {messages.map((msg) => (
          <div
            key={msg.id}
            className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
          >
            <div
              className={`max-w-[85%] rounded-xl px-3 py-2 text-sm leading-snug ${
                msg.role === "user"
                  ? "bg-sky-700 text-white"
                  : "bg-zinc-800 text-zinc-100"
              }`}
            >
              {msg.text}
            </div>
          </div>
        ))}
        {isThinking && (
          <div className="flex justify-start">
            <div className="max-w-[85%] rounded-xl bg-zinc-800 px-3 py-2 text-sm text-zinc-400">
              생각하는 중…
            </div>
          </div>
        )}
      </div>

      <form onSubmit={handleSubmit} className="flex gap-2 border-t border-zinc-800 p-2.5">
        <input
          type="text"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder={disabledReason ?? "음성 대신 텍스트로 지시…"}
          disabled={isThinking || blocked}
          className="flex-1 rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-500 focus:border-sky-600 focus:outline-none disabled:opacity-60"
        />
        <button
          type="submit"
          className="rounded-lg bg-sky-600 px-3 py-2 text-sm font-medium text-white transition-colors hover:bg-sky-500 disabled:opacity-50"
          disabled={!draft.trim() || isThinking || blocked}
        >
          전송
        </button>
      </form>
    </div>
  );
}
