"use client";

import { useCallback, useState } from "react";

/**
 * Booth-safety net: if the mic/venue audio ever fails, this lets the
 * operator type instead of speak. `onSendText` routes through the exact
 * same relay pipeline a transcribed voice reply uses (see
 * `VoiceSession.sendText` / relay `mtype === "text"`), so a typed reply is
 * authorized and processed identically to a spoken one — this never
 * bypasses the mission's own consent checks, it just changes how the
 * reply arrives. The "네" button is a one-tap shortcut for the common
 * "just get me past this confirmation" case.
 */
export default function VoiceTextFallback({
  onSendText,
}: {
  onSendText: (text: string) => boolean;
}) {
  const [value, setValue] = useState("");
  const [rejected, setRejected] = useState(false);

  const send = useCallback((text: string) => {
    const trimmed = text.trim();
    if (!trimmed) return;
    const accepted = onSendText(trimmed);
    setRejected(!accepted);
    if (accepted) setValue("");
  }, [onSendText]);

  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-2">
        <input
          type="text"
          value={value}
          onChange={(event) => { setValue(event.target.value); setRejected(false); }}
          onKeyDown={(event) => { if (event.key === "Enter") send(value); }}
          placeholder="음성이 안 되면 여기에 대신 입력해줘"
          className="min-w-0 flex-1 border-2 border-[#091f2c] bg-white px-2 py-1 text-xs text-[#091f2c] outline-none focus:border-[#8661c5]"
        />
        <button
          type="button"
          onClick={() => send(value)}
          className="pixel-button shrink-0 bg-white px-2 py-1 text-xs font-semibold text-[#091f2c]"
        >
          전송
        </button>
        <button
          type="button"
          onClick={() => send("네")}
          className="pixel-button shrink-0 bg-[#d6f4cf] px-2 py-1 text-xs font-semibold text-[#091f2c]"
        >
          네 (강제 진행)
        </button>
      </div>
      {rejected && (
        <p role="alert" className="text-[0.7rem] leading-snug text-[#7a1f1f]">
          지금은 참가자 차례가 아니라 전송되지 않았어요. 기비가 말을 마칠 때까지 기다렸다가 다시 시도해줘.
        </p>
      )}
    </div>
  );
}
