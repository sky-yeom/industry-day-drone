"use client";

import { useEffect, useRef, useState } from "react";

type PagedTextProps = {
  text: string;
  label: string;
} & ({ contentFit?: false; maxHeight?: never } | { contentFit: true; maxHeight: number });

export default function PagedText({ text, label, contentFit = false, maxHeight }: PagedTextProps) {
  const bodyRef = useRef<HTMLDivElement>(null);
  const measureRef = useRef<HTMLParagraphElement>(null);
  const pagerRef = useRef<HTMLElement>(null);
  const [pages, setPages] = useState([text]);
  const [page, setPage] = useState(0);
  const [bodyHeight, setBodyHeight] = useState<number>();
  const [spaceWarning, setSpaceWarning] = useState(false);

  useEffect(() => {
    const body = bodyRef.current;
    const measure = measureRef.current;
    const pager = pagerRef.current;
    if (!body || !measure || !pager) return;
    let active = true;
    let lastSize = "";
    const fit = () => {
      if (!active || !body.clientWidth || (!contentFit && !body.clientHeight)) return;
      const size = `${body.clientWidth}:${contentFit ? maxHeight : body.clientHeight}`;
      if (lastSize === size) return;
      lastSize = size;
      const available = contentFit ? Math.max(0, maxHeight ?? 0) : body.clientHeight;
      measure.textContent = text;
      const fullHeight = measure.getBoundingClientRect().height;
      const needsPages = fullHeight > available;
      const height = contentFit && needsPages
        ? Math.max(0, available - pager.getBoundingClientRect().height - 4)
        : available;
      measure.textContent = "가";
      const lineHeight = measure.getBoundingClientRect().height;
      if (text && height < lineHeight) {
        measure.textContent = "";
        setSpaceWarning(true);
        setPages([text]);
        setBodyHeight(undefined);
        return;
      }
      setSpaceWarning(false);
      const characters = Array.from(text);
      const next: string[] = [];
      let start = 0;
      // Measure with the displayed font and width, so long Korean text stays readable.
      while (start < characters.length) {
        let low = 1, high = characters.length - start, length = 1;
        while (low <= high) {
          const middle = Math.floor((low + high) / 2);
          measure.textContent = characters.slice(start, start + middle).join("");
          if (measure.getBoundingClientRect().height <= height) {
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
      setBodyHeight(contentFit ? Math.min(fullHeight, height) : undefined);
    };
    const observer = new ResizeObserver(fit);
    observer.observe(body);
    const refitFonts = () => {
      lastSize = "";
      fit();
    };
    fit();
    void document.fonts.ready.then(refitFonts);
    document.fonts.addEventListener("loadingdone", refitFonts);
    return () => {
      active = false;
      observer.disconnect();
      document.fonts.removeEventListener("loadingdone", refitFonts);
    };
  }, [text, contentFit, maxHeight]);

  const index = Math.min(page, pages.length - 1);
  const showPager = !spaceWarning && pages.length > 1;
  return <div className={`relative flex min-h-0 flex-col ${contentFit ? "" : "h-full gap-1"}`} aria-label={label}>
    <div ref={bodyRef} className={`relative min-h-0 ${contentFit ? (bodyHeight === undefined && !spaceWarning ? "overflow-hidden" : "") : "flex-1 overflow-hidden"}`}
      style={contentFit && !spaceWarning ? { height: bodyHeight ?? 0, visibility: bodyHeight === undefined ? "hidden" : undefined } : undefined}>
      {spaceWarning ? <p role="status" className="text-xs leading-5">텍스트를 표시할 공간이 부족합니다. 창 높이를 늘리거나 화면을 가로로 돌려 주세요.</p>
        : <p className="whitespace-pre-wrap [overflow-wrap:anywhere]">{pages[index]}</p>}
      <p ref={measureRef} aria-hidden="true" className="invisible absolute inset-x-0 top-0 whitespace-pre-wrap [overflow-wrap:anywhere]" />
    </div>
    <nav ref={pagerRef} aria-hidden={!showPager}
      className={`flex h-6 shrink-0 items-center justify-end gap-2 whitespace-nowrap text-[11px] leading-5 ${showPager ? (contentFit ? "mt-1" : "") : (contentFit ? "invisible absolute bottom-0 right-0" : "invisible")}`}
      aria-label={`${label} 페이지`}>
      <button type="button" disabled={index === 0} onClick={() => setPage(index - 1)}
        className="text-[#8661c5] disabled:opacity-30" aria-label={`${label} 이전 페이지`}>이전</button>
      <span className="tabular-nums" aria-live="polite">{index + 1} / {pages.length}</span>
      <button type="button" disabled={index === pages.length - 1} onClick={() => setPage(index + 1)}
        className="text-[#8661c5] disabled:opacity-30" aria-label={`${label} 다음 페이지`}>다음</button>
    </nav>
  </div>;
}
