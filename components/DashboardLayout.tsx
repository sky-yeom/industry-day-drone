import type { ReactNode } from "react";

interface DashboardLayoutProps {
  header: ReactNode;
  pathPanel: ReactNode;
  chatPanel: ReactNode;
}

/**
 * Two-column dashboard with the flight path and voice chat side by side.
 */
export default function DashboardLayout({
  header,
  pathPanel,
  chatPanel,
}: DashboardLayoutProps) {
  return (
    <main className="relative flex min-h-dvh w-full flex-col overflow-hidden bg-[linear-gradient(125deg,#f4f3f5_0%,#f4f3f5_62%,#eee8f7_100%)] md:h-dvh">
      <div className="dot-field pointer-events-none absolute right-0 top-0 h-[44%] w-[38%] opacity-40 [mask-image:linear-gradient(135deg,transparent,black)]" />

      <div className="relative z-10 flex min-h-0 flex-1 flex-col px-3 pb-3 pt-2 sm:px-5 sm:pb-5 sm:pt-3">
        {header}
        <div className="grid min-h-0 flex-1 grid-cols-1 grid-rows-[minmax(500px,1fr)_minmax(620px,1fr)] gap-4 overflow-y-auto pt-4 md:grid-cols-[minmax(0,1.35fr)_minmax(360px,0.85fr)] md:grid-rows-1 md:overflow-hidden">
          <div className="min-h-0 overflow-hidden rounded-[20px] border border-white bg-white/80 shadow-[0_18px_50px_rgba(42,68,111,0.1)] backdrop-blur-xl">
            {pathPanel}
          </div>
          <div className="min-h-0 overflow-hidden rounded-[20px] border border-white bg-white/85 shadow-[0_18px_50px_rgba(70,54,104,0.1)] backdrop-blur-xl">
            {chatPanel}
          </div>
        </div>
      </div>
    </main>
  );
}
