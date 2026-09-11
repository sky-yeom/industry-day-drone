"use client";

import type { ReactNode } from "react";

/**
 * Shared full-bleed pixel backdrop (sky + ground) for the Route/Images/
 * Results steps (no tab bar — page.tsx swaps which step is mounted). Banners
 * (error/status) render above the step's own content, all pixel-styled.
 */
export default function PixelShell({ banners, children }: { banners?: ReactNode; children: ReactNode }) {
  return (
    <main className="relative flex h-dvh w-full flex-col overflow-hidden">
      <div aria-hidden className="pixel-scene-sky absolute inset-0" />
      <div aria-hidden className="pixel-scene-dirt absolute inset-x-0 bottom-0 h-[16%]" />
      <div aria-hidden className="pixel-scene-ground pixel-rendering absolute inset-x-0 bottom-[12%] h-6" />
      <div className="relative z-10 flex h-full min-h-0 flex-col gap-3 p-4 sm:p-6">
        {banners}
        <div className="min-h-0 flex-1">{children}</div>
      </div>
    </main>
  );
}
