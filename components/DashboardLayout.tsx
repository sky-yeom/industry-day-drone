import type { ReactNode } from "react";

interface DashboardLayoutProps {
  header: ReactNode;
  pathPanel: ReactNode;
  anomalyPanel: ReactNode;
  chatPanel: ReactNode;
}

/**
 * Quarter-split dashboard grid:
 * - Left column (full height): flight path panel
 * - Right column, top row: anomaly results panel
 * - Right column, bottom row: chat panel
 */
export default function DashboardLayout({
  header,
  pathPanel,
  anomalyPanel,
  chatPanel,
}: DashboardLayoutProps) {
  return (
    <div className="flex min-h-dvh w-full flex-col bg-black md:h-dvh">
      {header}
      <div className="grid min-h-0 flex-1 grid-cols-1 grid-rows-[minmax(440px,1fr)_minmax(600px,1fr)] gap-2 overflow-y-auto p-2 md:grid-cols-2 md:grid-rows-1 md:overflow-hidden">
        <div className="min-h-0 overflow-hidden rounded-xl border border-zinc-800 md:row-span-1">
          {pathPanel}
        </div>
        <div className="grid min-h-0 grid-rows-2 gap-2 overflow-hidden">
          <div className="min-h-0 overflow-hidden rounded-xl border border-zinc-800">
            {anomalyPanel}
          </div>
          <div className="min-h-0 overflow-hidden rounded-xl border border-zinc-800">
            {chatPanel}
          </div>
        </div>
      </div>
    </div>
  );
}
