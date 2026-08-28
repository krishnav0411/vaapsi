/**
 * The Blade activity table shared by Overview (recent activity) and the
 * Episodes index. Every cell is real server data — no placeholders
 * dressed as content. The Amount column renders the per-episode
 * recovered total /api/episodes joins (SUM over the episode's ledger
 * window): 0 renders an honest "—" titled "no recovery yet", never a
 * fake ₹0. Column headers sort only when the caller passes a sort
 * contract (the Episodes index does; Overview's recent-activity list
 * stays server-ordered).
 */

import { useNavigate } from "react-router-dom";

import { EpisodeStatePill, StatusPill } from "@/components/StatusPill";
import type { EpisodeRow } from "@/lib/api";
import { formatInr, timeAgo, truncateId } from "@/lib/format";
import { cn } from "@/lib/utils";

export type EpisodeSortKey = "episode" | "state" | "halted";

export interface EpisodeSort {
  key: EpisodeSortKey;
  dir: "asc" | "desc";
}

const columns: { label: string; align?: "right"; sortKey?: EpisodeSortKey }[] = [
  { label: "Episode", sortKey: "episode" },
  { label: "Subscription" },
  { label: "Cohort" },
  { label: "State", sortKey: "state" },
  { label: "Halted", sortKey: "halted" },
  { label: "Amount", align: "right" },
];

function AmountCell({ paise }: { paise: number }) {
  if (paise <= 0) {
    return (
      <span className="text-sm text-text-disabled" title="no recovery yet">
        —
      </span>
    );
  }
  return (
    <span
      className="tnum font-mono text-sm text-text-normal"
      title="recovered on this episode's ledger window"
    >
      {formatInr(paise)}
    </span>
  );
}

export function EpisodeTable({
  episodes,
  sort,
  onSort,
}: {
  episodes: EpisodeRow[];
  sort?: EpisodeSort;
  onSort?: (key: EpisodeSortKey) => void;
}) {
  const navigate = useNavigate();

  if (episodes.length === 0) {
    return (
      <p className="rounded-card border border-border-subtle bg-surface p-24 text-sm text-text-muted">
        No episodes yet — the audit chain starts with the first halt.
      </p>
    );
  }

  return (
    <div className="overflow-hidden rounded-card border border-border-subtle bg-surface shadow-low">
      <table className="w-full border-collapse text-left">
        <thead>
          <tr className="border-b border-border-subtle">
            {columns.map(({ label, align, sortKey }) => {
              const active =
                sort !== undefined && sortKey !== undefined && sort.key === sortKey;
              return (
                <th
                  key={label}
                  aria-sort={
                    active && sort !== undefined
                      ? sort.dir === "asc"
                        ? "ascending"
                        : "descending"
                      : undefined
                  }
                  className={cn(
                    "px-12 py-8 text-xs font-medium uppercase text-text-muted",
                    align === "right" && "text-right",
                  )}
                >
                  {sortKey !== undefined && onSort !== undefined ? (
                    <button
                      type="button"
                      onClick={() => onSort(sortKey)}
                      className={cn(
                        "inline-flex items-center gap-4 uppercase hover:text-text-normal",
                        active && "text-primary",
                      )}
                    >
                      {label}
                      <span aria-hidden className="text-xs">
                        {active && sort !== undefined ? (sort.dir === "asc" ? "▲" : "▼") : "↕"}
                      </span>
                    </button>
                  ) : (
                    label
                  )}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {episodes.map((episode) => (
            <tr
              key={episode.id}
              onClick={() => navigate(`/episodes/${encodeURIComponent(episode.id)}`)}
              className="cursor-pointer border-b border-border-subtle last:border-b-0 hover:bg-row-hover"
            >
              <td className="px-12 py-8 font-mono text-sm text-text-normal" title={episode.id}>
                {truncateId(episode.id)}
              </td>
              <td className="px-12 py-8 font-mono text-sm text-text-subtle" title={episode.subscription_id}>
                {episode.subscription_id}
              </td>
              <td className="px-12 py-8">
                {episode.cohort === null ? (
                  <span className="text-sm text-text-disabled">—</span>
                ) : (
                  <StatusPill tone="neutral">{episode.cohort}</StatusPill>
                )}
              </td>
              <td className="px-12 py-8">
                <span className="inline-flex items-center gap-8">
                  <EpisodeStatePill state={episode.state} />
                  {episode.attempt_count > 0 && (
                    <span className="tnum font-mono text-xs text-text-muted">
                      ×{episode.attempt_count}
                    </span>
                  )}
                </span>
              </td>
              <td className="px-12 py-8 text-sm text-text-muted" title={episode.halt_ts_utc}>
                {timeAgo(episode.halt_ts_utc)}
              </td>
              <td className="px-12 py-8 text-right">
                <AmountCell paise={episode.recovered_paise} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
