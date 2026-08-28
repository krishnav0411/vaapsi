/**
 * D7.4 Episodes index — the "View all" target. /api/episodes is fetched
 * whole (the dataset is small) and filtered/sorted in memory: state
 * chips (multi-select, none active = all states), a cohort segmented
 * control, and sortable Episode/State/Halted headers — halted-time
 * descending is the default, because the operator's first question is
 * "what halted most recently". The count line and both empty states
 * (no data at all vs. filters excluding everything) tell the truth
 * about what is on screen.
 */

import { useMemo, useState } from "react";

import {
  EpisodeTable,
  type EpisodeSort,
  type EpisodeSortKey,
} from "@/components/EpisodeTable";
import {
  useApi,
  type Cohort,
  type EpisodeRow,
  type EpisodeState,
} from "@/lib/api";
import { cn } from "@/lib/utils";

const ALL_STATES: EpisodeState[] = [
  "NEW",
  "DIAGNOSED",
  "SCORED",
  "GATED",
  "SENT",
  "VERIFIED",
  "CLOSED",
  "VOIDED",
];

/** Lifecycle order — state sorting follows the pipeline, not the alphabet. */
const STATE_ORDER: Record<EpisodeState, number> = {
  NEW: 0,
  DIAGNOSED: 1,
  SCORED: 2,
  GATED: 3,
  SENT: 4,
  VERIFIED: 5,
  CLOSED: 6,
  VOIDED: 7,
};

const DEFAULT_DIR: Record<EpisodeSortKey, "asc" | "desc"> = {
  episode: "asc",
  state: "asc",
  halted: "desc",
};

function sortEpisodes(rows: EpisodeRow[], sort: EpisodeSort): EpisodeRow[] {
  const sorted = [...rows].sort((a, b) => {
    switch (sort.key) {
      case "episode":
        return a.id.localeCompare(b.id);
      case "state":
        return STATE_ORDER[a.state] - STATE_ORDER[b.state];
      case "halted":
        return Date.parse(a.halt_ts_utc) - Date.parse(b.halt_ts_utc);
    }
  });
  return sort.dir === "desc" ? sorted.reverse() : sorted;
}

function FilterLabel({ children }: { children: string }) {
  return (
    <span className="w-64 shrink-0 pt-4 text-xs font-medium uppercase text-text-muted">
      {children}
    </span>
  );
}

function StateChip({
  state,
  active,
  onToggle,
}: {
  state: EpisodeState;
  active: boolean;
  onToggle: (state: EpisodeState) => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={() => onToggle(state)}
      className={cn(
        "rounded-pill border px-12 py-2 text-xs font-medium",
        active
          ? "border-primary bg-primary-tint text-primary"
          : "border-border-normal bg-surface text-text-subtle hover:border-border-hover",
      )}
    >
      {state}
    </button>
  );
}

function CohortSegment({
  value,
  active,
  onSelect,
}: {
  value: string;
  active: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onSelect}
      className={cn(
        "rounded-button border px-12 py-4 text-xs font-medium",
        active
          ? "border-primary bg-primary-tint text-primary"
          : "border-border-normal bg-surface text-text-subtle hover:border-border-hover",
      )}
    >
      {value}
    </button>
  );
}

export function EpisodesPage() {
  const episodes = useApi<EpisodeRow[]>("/api/episodes");
  const [states, setStates] = useState<Set<EpisodeState>>(new Set<EpisodeState>());
  const [cohort, setCohort] = useState<Cohort | "">("");
  const [sort, setSort] = useState<EpisodeSort>({ key: "halted", dir: "desc" });

  const filtered = useMemo(() => {
    const data = episodes.data;
    if (data === null) return [];
    return data.filter(
      (episode) =>
        (states.size === 0 || states.has(episode.state)) &&
        (cohort === "" || episode.cohort === cohort),
    );
  }, [episodes.data, states, cohort]);

  const sorted = useMemo(() => sortEpisodes(filtered, sort), [filtered, sort]);

  function onSort(key: EpisodeSortKey) {
    setSort((prev) =>
      prev.key === key
        ? { key, dir: prev.dir === "asc" ? "desc" : "asc" }
        : { key, dir: DEFAULT_DIR[key] },
    );
  }

  function toggleState(state: EpisodeState) {
    setStates((prev) => {
      const next = new Set(prev);
      if (next.has(state)) {
        next.delete(state);
      } else {
        next.add(state);
      }
      return next;
    });
  }

  return (
    <div className="flex flex-col gap-16">
      <div className="flex flex-col gap-12 rounded-card border border-border-subtle bg-surface p-16 shadow-low">
        <div className="flex items-start gap-12">
          <FilterLabel>State</FilterLabel>
          <div className="flex flex-wrap gap-8">
            <button
              type="button"
              aria-pressed={states.size === 0}
              onClick={() => setStates(new Set<EpisodeState>())}
              className={cn(
                "rounded-pill border px-12 py-2 text-xs font-medium",
                states.size === 0
                  ? "border-primary bg-primary-tint text-primary"
                  : "border-border-normal bg-surface text-text-subtle hover:border-border-hover",
              )}
            >
              All
            </button>
            {ALL_STATES.map((state) => (
              <StateChip
                key={state}
                state={state}
                active={states.has(state)}
                onToggle={() => toggleState(state)}
              />
            ))}
          </div>
        </div>
        <div className="flex items-start gap-12">
          <FilterLabel>Cohort</FilterLabel>
          <div className="flex gap-8">
            <CohortSegment value="All" active={cohort === ""} onSelect={() => setCohort("")} />
            <CohortSegment
              value="TREATMENT"
              active={cohort === "TREATMENT"}
              onSelect={() => setCohort("TREATMENT")}
            />
            <CohortSegment
              value="CONTROL"
              active={cohort === "CONTROL"}
              onSelect={() => setCohort("CONTROL")}
            />
          </div>
        </div>
      </div>

      {episodes.error !== null ? (
        <div
          role="alert"
          className="rounded-card bg-negative-bg p-16 text-sm font-medium text-negative-text"
        >
          {episodes.error}
        </div>
      ) : episodes.data === null ? (
        <p className="text-sm text-text-muted">Loading episodes…</p>
      ) : (
        <>
          <p className="tnum text-sm text-text-muted">
            {sorted.length} {sorted.length === 1 ? "episode" : "episodes"}
            {sorted.length !== episodes.data.length && (
              <span> of {episodes.data.length} total</span>
            )}
          </p>
          {episodes.data.length > 0 && sorted.length === 0 ? (
            <p className="rounded-card border border-border-subtle bg-surface p-24 text-sm text-text-muted">
              No episodes match the current filters — clear a chip or switch cohort to widen
              the view.
            </p>
          ) : (
            <EpisodeTable episodes={sorted} sort={sort} onSort={onSort} />
          )}
        </>
      )}
    </div>
  );
}
