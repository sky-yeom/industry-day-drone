"use client";

import { Press_Start_2P } from "next/font/google";
import { useCallback, useEffect, useState } from "react";
import PixelGround from "@/components/PixelGround";
import PixelPromptScreen from "@/components/PixelPromptScreen";
import { useTypewriter } from "@/lib/useTypewriter";
import type { BriefingBullet } from "@/lib/types";
import type { VoiceStatus } from "@/lib/voiceClient";

// Pixel-game display font for headings/labels/buttons only; Korean copy
// (the speech bubble) stays in the app's normal Korean-capable font since
// this Latin-only pixel font has no Hangul glyphs.
const pixelFont = Press_Start_2P({ weight: "400", subsets: ["latin"] });

const GIBBY_TITLE = "코드레드";
const GIBBY_LINE = "긴급 구조 요청이 세 건이나 들어왔어!\n우리 얼른 사람들을 구하러 가자!";

// How long Gibby smiles (row 1, frame 2 of the sprite sheet) before he
// resets back to a neutral idle pose and then sets off walking.
const SMILE_DURATION_MS = 550;
const RESET_DURATION_MS = 250;
// How long the ground scrolls / content slides before Gibby peels off
// toward his docked corner (the "ground stops when content is near him"
// beat) — timed so Gibby's corner walk (gibby-travel's 1.5s base transition)
// finishes at roughly the same moment the slower .intro-stage content slide
// (2.6s) settles into place, keeping both paced together.
const SLIDE_TO_EXIT_MS = 1100;

type Phase = "idle" | "smiling" | "resetting" | "walking" | "sliding" | "exiting" | "done";

/**
 * Pixel-art opening screen + the choreographed handoff into the "prompt"
 * step. Pressing "Let's Go!" starts the walk-in; onReady starts voice once docked.
 * and plays idle -> smile -> idle -> walk-to-center; once Gibby reaches
 * center the ground starts scrolling and the prompt screen's content slides
 * in from the right; when it's nearly in place the ground stops and Gibby
 * walks off to his docked bottom-right spot while the content settles.
 * Stays mounted for the whole prompt step so there's no remount/flash
 * between the transition and the live prompt screen.
 */
export default function GibbyIntroSequence({
  onReady,
  targetImage,
  targetAlt,
  briefing,
  agentText,
  voiceStatus,
  error,
  onRetry,
}: {
  onReady: () => void;
  targetImage: string;
  targetAlt: string;
  briefing: BriefingBullet[];
  agentText: string;
  voiceStatus?: VoiceStatus;
  error?: string | null;
  onRetry?: () => void;
}) {
  const [phase, setPhase] = useState<Phase>("idle");
  const typed = useTypewriter(agentText);

  const handleGo = useCallback(() => {
    if (phase !== "idle") return;
    setPhase("smiling");
  }, [phase]);

  useEffect(() => {
    if (phase !== "smiling") return;
    const id = window.setTimeout(() => setPhase("resetting"), SMILE_DURATION_MS);
    return () => window.clearTimeout(id);
  }, [phase]);

  useEffect(() => {
    if (phase !== "resetting") return;
    const id = window.setTimeout(() => setPhase("walking"), RESET_DURATION_MS);
    return () => window.clearTimeout(id);
  }, [phase]);

  // Once the ground+slide-in beat has been running a while, Gibby peels off
  // toward the corner while the content finishes settling.
  useEffect(() => {
    if (phase !== "sliding") return;
    const id = window.setTimeout(() => setPhase("exiting"), SLIDE_TO_EXIT_MS);
    return () => window.clearTimeout(id);
  }, [phase]);

  const handleGibbyLeftTransitionEnd = useCallback(() => {
    if (phase === "walking") setPhase("sliding");
    else if (phase === "exiting") {
      setPhase("done");
      // Only once Gibby is docked and everything has settled on screen do
      // we start the voice agent, so it never talks over the walk-in.
      onReady();
    }
  }, [phase, onReady]);

  const showIdleCard = phase === "idle";
  const isWalkingSprite = phase === "walking" || phase === "sliding" || phase === "exiting";
  const travelClass =
    phase === "walking" || phase === "sliding" ? "gibby-travel--center"
    : phase === "exiting" || phase === "done" ? "gibby-travel--corner"
    : "";

  return (
    <main className="relative flex h-dvh w-full flex-col overflow-hidden">
      <div aria-hidden className="pixel-scene-sky absolute inset-0" />

      {/* Floating pixel clouds, purely decorative. */}
      <div aria-hidden className="absolute left-[8%] top-[14%] h-6 w-16 bg-white/80 [clip-path:polygon(10%_100%,10%_40%,30%_40%,30%_0%,70%_0%,70%_40%,90%_40%,90%_100%)]" />
      <div aria-hidden className="absolute right-[14%] top-[24%] h-5 w-12 bg-white/70 [clip-path:polygon(10%_100%,10%_40%,30%_40%,30%_0%,70%_0%,70%_40%,90%_40%,90%_100%)]" />

      <PixelGround scrolling={phase === "sliding"} />

      <div
        className={`gibby-travel absolute bottom-[16%] ${travelClass}`}
        onTransitionEnd={(event) => event.propertyName === "left" && handleGibbyLeftTransitionEnd()}
      >
        {isWalkingSprite ? (
          <div aria-hidden className="gibby-walk-sprite pixel-rendering" />
        ) : (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={phase === "smiling" ? "/gibby/smile.png" : "/gibby/idle.png"}
            alt=""
            aria-hidden
            className="pixel-rendering h-[214px] w-auto"
            style={{ transform: "scale(var(--ui-scale))", transformOrigin: "bottom center" }}
          />
        )}
      </div>

      {/* Gibby's speech bubble once he's docked: anchored from the right
          edge (not attached to the gibby-travel box itself), so as the
          typewriter text grows the bubble only widens leftward — it never
          nudges Gibby's own fixed resting position. */}
      {phase === "done" && typed && (
        <div className="absolute bottom-[calc(16%+(146px*var(--ui-scale)))] z-20 right-[calc(6%+(161px*var(--ui-scale)))]">
          <div className="pixel-bubble pixel-bubble--right relative max-w-[16.25rem] px-4 py-3 text-sm leading-6 text-[#091f2c] sm:max-w-xs">
            {typed}
          </div>
        </div>
      )}

      {showIdleCard && (
        <div className="relative z-10 flex flex-1 items-center justify-center p-5">
          <div className="pixel-bubble pixel-bubble--gibby relative w-full max-w-3xl px-8 pb-20 pt-10 sm:px-14 sm:pb-24 sm:pt-12">
            <div className="ml-[1.25rem]">
              <h2 className={`${pixelFont.className} pixel-title text-2xl text-[#463668] sm:text-3xl`}>{GIBBY_TITLE}</h2>
              <p className="mt-5 whitespace-pre-line text-xl leading-8 text-[#091f2c] sm:text-2xl sm:leading-9">{GIBBY_LINE}</p>
            </div>
            <button
              type="button"
              onClick={handleGo}
              className={`${pixelFont.className} pixel-button absolute bottom-6 right-6 bg-[#ffd23f] px-6 py-3 text-xs text-[#091f2c] sm:bottom-8 sm:right-8 sm:text-sm`}
            >
              Let&apos;s Go!
            </button>
          </div>
        </div>
      )}

      {/* Prompt screen content: mounted the whole time so images/panels are
          ready before it becomes visible, translated off-screen right until
          the "sliding"/"exiting"/"done" phases bring it in. */}
      <div className={`intro-stage absolute inset-0 z-10 ${phase === "sliding" || phase === "exiting" || phase === "done" ? "intro-stage--in" : ""}`}>
        <PixelPromptScreen
          targetImage={targetImage}
          targetAlt={targetAlt}
          briefing={briefing}
          voiceStatus={phase === "done" ? voiceStatus : undefined}
          error={phase === "done" ? error : null}
          onRetry={onRetry}
        />
      </div>
    </main>
  );
}
