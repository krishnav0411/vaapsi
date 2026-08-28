/**
 * The ⌘K command palette — plain React, zero new deps. Global Cmd/Ctrl+K
 * (ignored while typing in inputs/textareas) opens a Linear-style overlay:
 * a centered ~560px panel over a rgba dim, fuzzy-filtering nav commands,
 * the first ~20 episodes (loaded on open) and the theme toggle. Arrow
 * keys move, Enter runs, Escape closes, Tab is trapped inside the dialog,
 * body scroll locks while open. CONTEXT-AWARE: on /episodes/:id the
 * episode's own actions (copy ids, jump to its first ledger row) lead the
 * list. Every color comes from the index.css tokens — both themes are law.
 */

import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";
import { useLocation, useNavigate } from "react-router-dom";
import {
  BarChart3,
  BookOpen,
  ClipboardCheck,
  Copy,
  Home,
  List,
  Moon,
  Repeat,
  Sun,
  type LucideIcon,
} from "lucide-react";

import {
  getEpisodeDetail,
  getEpisodes,
  type EpisodeDetailResponse,
  type EpisodeRow,
} from "@/lib/api";
import { THEME_TOGGLE_EVENT } from "@/components/ThemeToggle";
import { cn } from "@/lib/utils";

const EPISODES_IN_PALETTE = 20;

interface PaletteItem {
  id: string;
  group: string;
  label: string;
  sub?: string;
  icon?: LucideIcon;
  /** Extra fuzzy haystack beyond label + sub (e.g. a subscription id). */
  match?: string;
  run: () => void;
}

/** Subsequence fuzzy score (null = no match); consecutive + prefix wins. */
function fuzzyScore(query: string, haystack: string): number | null {
  const q = query.toLowerCase();
  const h = haystack.toLowerCase();
  if (q.length === 0) return 0;
  let score = 0;
  let from = 0;
  let prev = -2;
  for (const ch of q) {
    const idx = h.indexOf(ch, from);
    if (idx === -1) return null;
    score += idx === prev + 1 ? 3 : 1;
    if (idx === 0) score += 2;
    prev = idx;
    from = idx + 1;
  }
  return score;
}

async function copyText(value: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(value);
  } catch {
    // Clipboard unavailable — closing still shows the value was there.
  }
}

function Kbd({ children }: { children: string }) {
  return (
    <kbd className="rounded-button border border-border-subtle bg-canvas px-4 py-1 font-mono text-[10px] text-text-muted">
      {children}
    </kbd>
  );
}

export function CommandPalette({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const [query, setQuery] = useState("");
  const [index, setIndex] = useState(0);
  const [episodes, setEpisodes] = useState<EpisodeRow[]>([]);
  const [ctx, setCtx] = useState<EpisodeDetailResponse | null>(null);
  const [dark, setDark] = useState(false);

  const inputRef = useRef<HTMLInputElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const activeRef = useRef<HTMLButtonElement>(null);

  const ctxMatch = pathname.match(/^\/episodes\/([^/]+)$/);
  const ctxId = ctxMatch !== null ? decodeURIComponent(ctxMatch[1]) : null;

  // Global ⌘K / Ctrl+K — ignored while typing in inputs/textareas.
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        const target = event.target as HTMLElement | null;
        const tag = target?.tagName;
        if (tag === "INPUT" || tag === "TEXTAREA" || target?.isContentEditable) return;
        event.preventDefault();
        onOpenChange(!open);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onOpenChange]);

  // On open: lock scroll, focus the input, load episodes + the context episode.
  useEffect(() => {
    if (!open) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    setQuery("");
    setIndex(0);
    setDark(document.documentElement.classList.contains("dark"));

    const controller = new AbortController();
    getEpisodes({}, controller.signal)
      .then((rows) => setEpisodes(rows.slice(0, EPISODES_IN_PALETTE)))
      .catch(() => setEpisodes([]));
    if (ctxId !== null) {
      getEpisodeDetail(ctxId, controller.signal)
        .then(setCtx)
        .catch(() => setCtx(null));
    } else {
      setCtx(null);
    }

    const raf = requestAnimationFrame(() => inputRef.current?.focus());
    return () => {
      document.body.style.overflow = previousOverflow;
      controller.abort();
      cancelAnimationFrame(raf);
    };
  }, [open, ctxId]);

  const run = (item: PaletteItem) => {
    onOpenChange(false);
    item.run();
  };

  const groups = useMemo<Array<{ title: string; items: PaletteItem[] }>>(() => {
    const ctxItems: PaletteItem[] =
      ctxId !== null
        ? [
            {
              id: `ctx-copy-episode`,
              group: `Episode ${ctxId}`,
              label: "Copy episode id",
              icon: Copy,
              run: () => void copyText(ctxId),
            },
            ...(ctx !== null
              ? [
                  {
                    id: "ctx-copy-subscription",
                    group: `Episode ${ctxId}`,
                    label: "Copy subscription id",
                    icon: Copy,
                    run: () => void copyText(ctx.episode.subscription_id),
                  },
                  ...(ctx.timeline.length > 0
                    ? [
                        {
                          id: "ctx-first-ledger-row",
                          group: `Episode ${ctxId}`,
                          label: "Go to its first ledger row",
                          sub: `seq ${ctx.timeline[0].seq} · ${ctx.timeline[0].trigger_event}`,
                          icon: BookOpen,
                          run: () => navigate(`/ledger?seq=${ctx.timeline[0].seq}`),
                        },
                      ]
                    : []),
                ]
              : []),
          ]
        : [];

    const navItems: PaletteItem[] = (
      [
        { to: "/", label: "Go to Overview", icon: Home },
        { to: "/episodes", label: "Go to Episodes", icon: List },
        { to: "/metrics", label: "Go to Metrics", icon: BarChart3 },
        { to: "/ledger", label: "Go to Ledger", icon: BookOpen },
        { to: "/drills", label: "Go to Drills", icon: Repeat },
        { to: "/approvals", label: "Go to Approvals", icon: ClipboardCheck },
      ] as const
    ).map(({ to, label, icon }) => ({
      id: `nav-${to}`,
      group: "Commands",
      label,
      icon,
      run: () => navigate(to),
    }));

    const themeItems: PaletteItem[] = [
      {
        id: "theme-toggle",
        group: "Commands",
        label: dark ? "Switch to light theme" : "Switch to dark theme",
        icon: dark ? Sun : Moon,
        run: () => window.dispatchEvent(new CustomEvent(THEME_TOGGLE_EVENT)),
      },
    ];

    const episodeItems: PaletteItem[] = episodes.map((episode) => ({
      id: `episode-${episode.id}`,
      group: "Episodes",
      label: episode.id,
      sub: episode.subscription_id,
      icon: List,
      match: `${episode.subscription_id} ${episode.state}`,
      run: () => navigate(`/episodes/${encodeURIComponent(episode.id)}`),
    }));

    const all = [...ctxItems, ...navItems, ...themeItems, ...episodeItems];
    const scored = all
      .map((item) => ({
        item,
        score: fuzzyScore(
          query,
          `${item.label} ${item.sub ?? ""} ${item.match ?? ""} ${item.group}`,
        ),
      }))
      .filter((entry): entry is { item: PaletteItem; score: number } => entry.score !== null)
      .sort((a, b) => b.score - a.score);

    const ordered: PaletteItem[] = [];
    const seenGroups: string[] = [];
    for (const { item } of scored) {
      if (!seenGroups.includes(item.group)) {
        seenGroups.push(item.group);
        const groupItems = scored.filter((s) => s.item.group === item.group).map((s) => s.item);
        ordered.push(...groupItems);
      }
    }
    const byGroup = new Map<string, PaletteItem[]>();
    for (const item of ordered) {
      const list = byGroup.get(item.group) ?? [];
      list.push(item);
      byGroup.set(item.group, list);
    }
    return Array.from(byGroup, ([title, items]) => ({ title, items }));
  }, [ctx, ctxId, dark, episodes, navigate, query]);

  const flat = useMemo(() => groups.flatMap((group) => group.items), [groups]);

  useEffect(() => {
    setIndex((current) => (current < flat.length ? current : 0));
  }, [flat.length]);

  useEffect(() => {
    activeRef.current?.scrollIntoView({ block: "nearest" });
  }, [index]);

  if (!open) return null;

  function onKeyDown(event: ReactKeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      onOpenChange(false);
      return;
    }
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setIndex((current) => (flat.length === 0 ? 0 : (current + 1) % flat.length));
      return;
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      setIndex((current) =>
        flat.length === 0 ? 0 : (current - 1 + flat.length) % flat.length,
      );
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      const item = flat[index];
      if (item !== undefined) run(item);
      return;
    }
    if (event.key === "Tab") {
      event.preventDefault();
      const focusables = Array.from(
        panelRef.current?.querySelectorAll<HTMLElement>("input, button") ?? [],
      );
      if (focusables.length === 0) return;
      const current = focusables.indexOf(document.activeElement as HTMLElement);
      const next = event.shiftKey
        ? current <= 0
          ? focusables.length - 1
          : current - 1
        : current === -1 || current === focusables.length - 1
          ? 0
          : current + 1;
      focusables[next].focus();
    }
  }

  let cursor = -1;

  return (
    <div
      className="fixed inset-0 z-50 overflow-y-auto"
      style={{ backgroundColor: "rgba(0, 0, 0, 0.5)" }}
      onClick={() => onOpenChange(false)}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
        onKeyDown={onKeyDown}
        onClick={(event) => event.stopPropagation()}
        className="mx-auto mt-[12vh] w-[560px] max-w-[calc(100vw-32px)] overflow-hidden rounded-card border border-border-subtle bg-surface shadow-high"
      >
        <div className="border-b border-border-subtle">
          <input
            ref={inputRef}
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              setIndex(0);
            }}
            placeholder="Search commands and episodes…"
            aria-label="Search commands and episodes"
            className="h-control-md w-full bg-surface px-16 text-sm text-text-normal outline-none placeholder:text-text-disabled"
          />
        </div>
        <div className="max-h-[50vh] overflow-y-auto p-8">
          {flat.length === 0 ? (
            <p className="px-8 py-16 text-sm text-text-muted">
              No matches for “{query}”.
            </p>
          ) : (
            groups.map((group) => (
              <div key={group.title} className="flex flex-col gap-2 py-8">
                <p className="px-8 text-xs font-medium uppercase text-text-muted">
                  {group.title}
                </p>
                {group.items.map((item) => {
                  cursor += 1;
                  const active = cursor === index;
                  const itemIndex = cursor;
                  const Icon = item.icon;
                  return (
                    <button
                      key={item.id}
                      ref={active ? activeRef : undefined}
                      type="button"
                      onMouseEnter={() => setIndex(itemIndex)}
                      onClick={() => run(item)}
                      className={cn(
                        "flex w-full items-center gap-12 rounded-button px-8 py-8 text-left",
                        active ? "bg-primary-tint" : "hover:bg-row-hover",
                      )}
                    >
                      {Icon !== undefined && (
                        <Icon
                          className={cn(
                            "h-16 w-16 shrink-0",
                            active ? "text-primary" : "text-text-muted",
                          )}
                          aria-hidden
                        />
                      )}
                      <span className="min-w-0 flex-1">
                        <span
                          className={cn(
                            "block truncate text-sm font-medium",
                            active ? "text-primary" : "text-text-normal",
                          )}
                        >
                          {item.label}
                        </span>
                        {item.sub !== undefined && (
                          <span className="block truncate font-mono text-xs text-text-muted">
                            {item.sub}
                          </span>
                        )}
                      </span>
                      {active && <Kbd>↵</Kbd>}
                    </button>
                  );
                })}
              </div>
            ))
          )}
        </div>
        <footer className="flex items-center gap-16 border-t border-border-subtle bg-canvas px-16 py-8">
          <span className="flex items-center gap-4 text-xs text-text-muted">
            <Kbd>↑</Kbd>
            <Kbd>↓</Kbd>
            navigate
          </span>
          <span className="flex items-center gap-4 text-xs text-text-muted">
            <Kbd>↵</Kbd>
            run
          </span>
          <span className="flex items-center gap-4 text-xs text-text-muted">
            <Kbd>esc</Kbd>
            close
          </span>
        </footer>
      </div>
    </div>
  );
}
