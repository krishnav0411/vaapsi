/**
 * CountUp: animates a number from 0 (or the previous displayed value)
 * to its target over ~900ms with an ease-out cubic, off
 * requestAnimationFrame. Respects prefers-reduced-motion — the value
 * jumps straight to its final number, no animation. Always tabular
 * numerals so the digits don't jitter while counting. Formatting is the
 * caller's job (the existing format.ts helpers, e.g. paise→₹).
 */

import { useEffect, useRef, useState } from "react";

import { cn } from "@/lib/utils";

const DEFAULT_DURATION_MS = 900;

export function CountUp({
  value,
  format,
  durationMs = DEFAULT_DURATION_MS,
  className,
}: {
  value: number;
  format?: (n: number) => string;
  durationMs?: number;
  className?: string;
}) {
  const fmt = format ?? ((n: number) => Math.round(n).toLocaleString("en-IN"));
  const [display, setDisplay] = useState(0);
  const fromRef = useRef(0);
  const rafRef = useRef<number | null>(null);

  useEffect(() => {
    const reduced =
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduced || durationMs <= 0) {
      fromRef.current = value;
      setDisplay(value);
      return;
    }
    const from = fromRef.current;
    if (from === value) return;
    const start = performance.now();
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / durationMs);
      const eased = 1 - Math.pow(1 - t, 3);
      const current = from + (value - from) * eased;
      fromRef.current = t >= 1 ? value : current;
      setDisplay(t >= 1 ? value : current);
      if (t < 1) rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => {
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
    };
  }, [value, durationMs]);

  return <span className={cn("tnum", className)}>{fmt(display)}</span>;
}
