"use client";

import { useEffect, useRef, useState } from "react";

/**
 * Reveals `text` a character at a time whenever it grows (matches the
 * streaming agent transcript), instead of popping the whole line in at
 * once. Shared by every Gibby speech bubble (opening/prompt transition,
 * the map-finding transition, and the persistent Route-screen dock) so they
 * all reveal text at the same pace.
 */
export function useTypewriter(text: string, intervalMs = 18): string {
  const [typed, setTyped] = useState("");
  const typedRef = useRef("");

  useEffect(() => {
    if (!text) {
      typedRef.current = "";
      setTyped("");
      return;
    }
    if (text.startsWith(typedRef.current)) {
      let i = typedRef.current.length;
      const id = window.setInterval(() => {
        i += 1;
        typedRef.current = text.slice(0, i);
        setTyped(typedRef.current);
        if (i >= text.length) window.clearInterval(id);
      }, intervalMs);
      return () => window.clearInterval(id);
    }
    typedRef.current = text;
    setTyped(text);
  }, [text, intervalMs]);

  return typed;
}
