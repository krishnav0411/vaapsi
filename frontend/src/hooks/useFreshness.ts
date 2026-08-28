/**
 * Shared data-freshness clock for the shell. One module-level /health
 * poll (every 30s, first one fires on subscribe) and one 10s tick back
 * every subscriber — no matter how many components call useFreshness,
 * there is exactly one of each timer alive, and only while at least one
 * subscriber is mounted. A failed poll leaves the last-success stamp
 * untouched, so the derived age keeps growing and the AppBar dot
 * degrades fresh → aging → stale on its own (the honest reading: the
 * data is only as new as the last poll that actually succeeded).
 */

import { useSyncExternalStore } from "react";

/** Green below this age, amber up to STALE_MS, red beyond. */
export const FRESH_LIMIT_S = 60;
export const STALE_LIMIT_S = 300;

const POLL_INTERVAL_MS = 30_000;
const TICK_INTERVAL_MS = 10_000;

export type FreshnessStatus = "unknown" | "fresh" | "aging" | "stale";

export interface Freshness {
  /** Epoch ms of the last successful /health poll; null before the first. */
  lastSuccessMs: number | null;
  /** Whole seconds since lastSuccessMs; null before the first success. */
  secondsAgo: number | null;
  /** unknown → no successful poll yet; fresh <60s; aging 60–300s; stale >300s. */
  status: FreshnessStatus;
}

interface Snapshot {
  lastSuccessMs: number | null;
  nowMs: number;
}

let snapshot: Snapshot = { lastSuccessMs: null, nowMs: Date.now() };
const listeners = new Set<() => void>();
let pollTimer: ReturnType<typeof setInterval> | null = null;
let tickTimer: ReturnType<typeof setInterval> | null = null;
let pollInFlight = false;

function emit(): void {
  for (const listener of listeners) listener();
}

async function poll(): Promise<void> {
  if (pollInFlight) return;
  pollInFlight = true;
  try {
    const res = await fetch("/health", { cache: "no-store" });
    if (res.ok) {
      snapshot = { lastSuccessMs: Date.now(), nowMs: snapshot.nowMs };
      emit();
    }
  } catch {
    // Failed poll: keep the old stamp. Age grows; the dot degrades.
  } finally {
    pollInFlight = false;
  }
}

function startTimers(): void {
  if (pollTimer === null) {
    void poll();
    pollTimer = setInterval(() => void poll(), POLL_INTERVAL_MS);
  }
  if (tickTimer === null) {
    tickTimer = setInterval(() => {
      snapshot = { lastSuccessMs: snapshot.lastSuccessMs, nowMs: Date.now() };
      emit();
    }, TICK_INTERVAL_MS);
  }
}

function stopTimers(): void {
  if (listeners.size > 0) return;
  if (pollTimer !== null) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
  if (tickTimer !== null) {
    clearInterval(tickTimer);
    tickTimer = null;
  }
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  startTimers();
  return () => {
    listeners.delete(listener);
    stopTimers();
  };
}

function getSnapshot(): Snapshot {
  return snapshot;
}

function deriveStatus(secondsAgo: number | null): FreshnessStatus {
  if (secondsAgo === null) return "unknown";
  if (secondsAgo < FRESH_LIMIT_S) return "fresh";
  if (secondsAgo <= STALE_LIMIT_S) return "aging";
  return "stale";
}

export function useFreshness(): Freshness {
  const current = useSyncExternalStore(subscribe, getSnapshot);
  const secondsAgo =
    current.lastSuccessMs === null
      ? null
      : Math.max(0, Math.floor((current.nowMs - current.lastSuccessMs) / 1000));
  return {
    lastSuccessMs: current.lastSuccessMs,
    secondsAgo,
    status: deriveStatus(secondsAgo),
  };
}
