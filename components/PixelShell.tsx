"use client";

import type { ReactNode } from "react";
import PixelGround from "@/components/PixelGround";

/**
 * Shared full-bleed pixel backdrop (sky + ground) for the Route/Images/
 * Results steps (no tab bar — page.tsx swaps which step is mounted). Banners
 * (error/status) render above the step's own content, all pixel-styled.
 */
export default function PixelShell({ banners, children, groundHidden }: { banners?: ReactNode; children: ReactNode; groundHidden?: boolean }) {
  return (
    <main className="relative flex h-dvh w-full flex-col overflow-hidden">
      <div aria-hidden className="pixel-scene-sky absolute inset-0" />
      <PixelGround hidden={groundHidden} />
      <div className="relative z-10 flex h-full min-h-0 flex-col gap-3 p-4 sm:p-6">
        {banners}
        <div className="min-h-0 flex-1">{children}</div>
      </div>
    </main>
  );
}
