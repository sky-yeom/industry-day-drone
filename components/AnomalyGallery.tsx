"use client";

import Image from "next/image";
import type { AnomalyResult } from "@/lib/types";
import { MONITOR_MAP } from "@/data/monitors";

interface AnomalyGalleryProps {
  anomalies: AnomalyResult[];
}

const SEVERITY_STYLES: Record<AnomalyResult["severity"], string> = {
  low: "bg-sky-900/60 text-sky-200 border-sky-700/60",
  medium: "bg-amber-900/60 text-amber-200 border-amber-700/60",
  high: "bg-red-900/60 text-red-200 border-red-700/60",
};

/**
 * Shows the anomaly-detection image results returned for the current flight.
 */
export default function AnomalyGallery({ anomalies }: AnomalyGalleryProps) {
  return (
    <div className="flex h-full w-full flex-col bg-zinc-900">
      <div className="flex items-center justify-between border-b border-zinc-800 px-4 py-3">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-zinc-200">
          탐지된 이상 징후
        </h2>
        <span className="rounded-full bg-zinc-800 px-3 py-1 text-xs font-medium text-zinc-300">
          {anomalies.length > 0 ? `${anomalies.length}건 발견` : "데이터 대기 중"}
        </span>
      </div>

      <div className="flex-1 overflow-y-auto p-3">
        {anomalies.length > 0 && (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {anomalies.map((anomaly) => {
              const monitor = MONITOR_MAP[anomaly.monitorId];
              return (
                <div
                  key={anomaly.id}
                  className="overflow-hidden rounded-lg border border-zinc-800 bg-zinc-950/60"
                >
                  <div className="relative aspect-[16/11] w-full bg-zinc-800">
                    <Image
                      src={anomaly.image}
                      alt={anomaly.label}
                      fill
                      className="object-cover"
                      sizes="(max-width: 640px) 100vw, 33vw"
                    />
                  </div>
                  <div className="space-y-1 p-2.5">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-sm font-medium text-zinc-100">{anomaly.label}</span>
                      <span
                        className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase ${SEVERITY_STYLES[anomaly.severity]}`}
                      >
                        {anomaly.severity}
                      </span>
                    </div>
                    <p className="text-[11px] text-zinc-400">
                      {monitor?.label ?? "알 수 없는 모니터"} · 신뢰도{" "}
                      {Math.round(anomaly.confidence * 100)}%
                    </p>
                    <p className="text-xs leading-snug text-zinc-400">{anomaly.notes}</p>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
