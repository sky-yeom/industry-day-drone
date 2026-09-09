"use client";

import { useEffect, useRef, useState } from "react";

export default function PagedText({ text, label }: { text: string; label: string }) {
  const bodyRef = useRef<HTMLDivElement>(null);
  const measureRef = useRef<HTMLParagraphElement>(null);
  const [pages, setPages] = useState([text]);
  const [page, setPage] = useState(0);

  useEffect(() => {
    const body = bodyRef.current;
    const measure = measureRef.current;
    if (!body || !measure) return;
    let active = true;
    const fit = () => {
      if (!active || !body.clientHeight || !body.clientWidth) return;
      const characters = Array.from(text);
      const next: string[] = [];
      let start = 0;
      // Measure with the displayed font and width, so long Korean text stays readable.
      while (start < characters.length) {
        let low = 1, high = characters.length - start, length = 1;
        while (low <= high) {
          const middle = Math.floor((low + high) / 2);
          measure.textContent = characters.slice(start, start + middle).join("");
          if (measure.getBoundingClientRect().height <= body.clientHeight) {
            length = middle;
            low = middle + 1;
          } else {
            high = middle - 1;
          }
        }
        next.push(characters.slice(start, start + length).join(""));
        start += length;
      }
      measure.textContent = "";
      setPages(next.length ? next : [""]);
      setPage(0);
    };
    const observer = new ResizeObserver(fit);
    observer.observe(body);
    void document.fonts.ready.then(fit);
    return () => { active = false; observer.disconnect(); };
  }, [text]);

  const index = Math.min(page, pages.length - 1);
  return <div className="flex h-full min-h-0 flex-col gap-1" aria-label={label}>
    <div ref={bodyRef} className="relative min-h-0 flex-1 overflow-hidden">
      <p className="whitespace-pre-wrap [overflow-wrap:anywhere]">{pages[index]}</p>
      <p ref={measureRef} aria-hidden="true" className="invisible absolute inset-x-0 top-0 whitespace-pre-wrap [overflow-wrap:anywhere]" />
    </div>
    <nav className={`flex h-6 shrink-0 items-center justify-end gap-3 text-[11px] leading-5 ${pages.length < 2 ? "invisible" : ""}`} aria-label={`${label} 페이지`}>
      <button type="button" disabled={index === 0} onClick={() => setPage(index - 1)}
        className="text-[#8661c5] disabled:opacity-30" aria-label={`${label} 이전 페이지`}>이전</button>
      <span className="tabular-nums" aria-live="polite">{index + 1} / {pages.length}</span>
      <button type="button" disabled={index === pages.length - 1} onClick={() => setPage(index + 1)}
        className="text-[#8661c5] disabled:opacity-30" aria-label={`${label} 다음 페이지`}>다음</button>
    </nav>
  </div>;
}
