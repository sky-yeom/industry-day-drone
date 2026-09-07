import { MONITORS } from "@/data/monitors";

export default function DroneImagePanel() {
  return (
    <section className="flex h-full min-h-0 flex-col">
      <div className="flex items-center justify-between gap-4 px-5 pb-3 pt-5 sm:px-6 sm:pt-6">
        <div>
          <p className="mb-1 text-[10px] font-bold uppercase tracking-[0.2em] text-[#8661c5]">
            Camera ingestion
          </p>
          <h2 className="text-lg font-semibold tracking-[-0.02em] text-[#091f2c]">
            드론 이미지
          </h2>
          <p className="mt-1 text-xs text-[#8c8279]">
            비행 중 수신한 카메라 캡처가 여기에 표시됩니다.
          </p>
        </div>
        <span className="flex items-center gap-2 rounded-full bg-[#fff8f3] px-3 py-2 text-xs font-semibold text-[#7f5a1a]">
          <span className="h-1.5 w-1.5 rounded-full bg-[#ffb900]" />
          수신 대기
        </span>
      </div>

      <div className="min-h-0 flex-1 px-3 pb-4 sm:px-4">
        <div className="relative flex h-full min-h-[390px] overflow-hidden rounded-2xl border border-[#ded8ea] bg-[linear-gradient(145deg,#ffffff_0%,#f3effb_56%,#e7f4fc_100%)] text-[#091f2c]">
          <div className="dot-field absolute inset-0 opacity-55 [mask-image:linear-gradient(to_right,black,transparent_75%)]" />
          <div className="absolute -right-20 top-[-6rem] h-64 w-64 rounded-full bg-[#8dc8e8]/60 blur-3xl" />
          <div className="absolute -left-16 bottom-[-6rem] h-56 w-56 rounded-full bg-[#c5b4e3]/60 blur-3xl" />

          <div className="relative hidden w-32 shrink-0 flex-col border-r border-[#ded8ea] bg-[#eee8f7]/70 p-3 sm:flex">
            <span className="mb-4 font-mono text-[9px] uppercase tracking-[0.18em] text-[#7a6a97]">
              Sources
            </span>
            {MONITORS.map((monitor, index) => (
              <div
                key={monitor.id}
                className="flex items-center gap-2 border-t border-[#d7cdeb] py-3 first:border-t-0"
              >
                <span
                  className={`h-2 w-2 rounded-full shadow-[0_0_0_3px_rgba(134,97,197,0.12)] ${
                    index === 0
                      ? "bg-[#49c5b1]"
                      : index === 1
                        ? "bg-[#0078d4]"
                        : "bg-[#d59ed7]"
                  }`}
                />
                <div>
                  <p className="text-[10px] font-semibold text-[#091f2c]">{monitor.label}</p>
                  <p className="mt-0.5 text-[8px] uppercase tracking-[0.12em] text-[#8c6fae]">
                    Offline
                  </p>
                </div>
              </div>
            ))}
          </div>

          <div className="relative flex min-w-0 flex-1 flex-col">
            <div className="flex items-center justify-between border-b border-[#ded8ea] bg-[#463668] px-4 py-3 font-mono text-[9px] uppercase tracking-[0.16em] text-white/70">
              <span>Live receiver / channel 01</span>
              <span>00:00:00</span>
            </div>

            <div className="flex flex-1 flex-col items-center justify-center px-6 text-center">
              <div className="mb-5 grid h-14 w-14 place-items-center rounded-xl bg-[linear-gradient(135deg,#8661c5,#463668)] shadow-[0_10px_24px_rgba(134,97,197,0.35)]">
                <svg viewBox="0 0 24 24" aria-hidden="true" className="h-7 w-7 text-white">
                  <path
                    fill="currentColor"
                    d="M8.4 5.5 9.7 3h4.6l1.3 2.5H19A2.5 2.5 0 0 1 21.5 8v9A2.5 2.5 0 0 1 19 19.5H5A2.5 2.5 0 0 1 2.5 17V8A2.5 2.5 0 0 1 5 5.5h3.4ZM12 8a4.25 4.25 0 1 0 0 8.5A4.25 4.25 0 0 0 12 8Zm0 2a2.25 2.25 0 1 1 0 4.5A2.25 2.25 0 0 1 12 10Z"
                  />
                </svg>
              </div>
              <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-[#8661c5]">
                Receiver not connected
              </p>
              <h3 className="mt-2 text-xl font-semibold tracking-[-0.03em] text-[#091f2c]">
                카메라 신호를 기다리고 있습니다
              </h3>
              <p className="mt-2 max-w-sm text-xs leading-5 text-[#8c8279]">
                드론 스트림이 연결되면 이 뷰포트에 라이브 영상과 캡처가 시간순으로
                나타납니다.
              </p>
            </div>

            <div className="grid grid-cols-3 border-t border-[#ded8ea] bg-[#eee8f7]/70 sm:hidden">
              {MONITORS.map((monitor) => (
                <div
                  key={monitor.id}
                  className="border-r border-[#d7cdeb] px-2 py-3 text-center last:border-r-0"
                >
                  <p className="text-[9px] font-semibold text-[#091f2c]">{monitor.label}</p>
                  <p className="mt-1 text-[8px] uppercase tracking-[0.1em] text-[#8c6fae]">
                    Offline
                  </p>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
