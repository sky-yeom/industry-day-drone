export default async function GatePage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string; error?: string }>;
}) {
  const { next = "/", error } = await searchParams;

  return (
    <main className="flex min-h-dvh items-center justify-center bg-[#f4f3f5] p-5">
      <section className="w-full max-w-sm rounded-3xl border border-white bg-white/90 p-8 shadow-xl">
        <p className="text-xs font-bold tracking-widest text-[#8661c5]">Microsoft Foundry · Industry Day</p>
        <h1 className="mt-4 text-xl font-semibold text-[#091f2c]">접속 코드를 입력하세요</h1>
        <form action="/api/gate" method="POST" className="mt-6 space-y-3">
          <input type="hidden" name="next" value={next} />
          <input
            type="password"
            name="pin"
            autoFocus
            placeholder="PIN"
            className="w-full rounded-xl border border-[#c5b4e3] bg-white px-4 py-3 text-sm outline-none focus:border-[#8661c5]"
          />
          {error && <p className="text-xs text-red-500">코드가 올바르지 않습니다.</p>}
          <button
            type="submit"
            className="w-full rounded-full bg-[#463668] px-6 py-3 font-semibold text-white transition-all duration-150 hover:scale-[1.02] active:scale-100"
          >
            입장
          </button>
        </form>
      </section>
    </main>
  );
}
