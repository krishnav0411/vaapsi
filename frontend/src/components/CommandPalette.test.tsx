/**
 * CommandPalette behavior tests: ctrl+k opens, query filters, Enter
 * navigates, Escape closes, Tab is trapped inside the dialog, and the
 * context group leads on an /episodes/:id route. The api module is
 * mocked at its boundary — no network.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { describe, expect, it, vi, beforeEach } from "vitest";

import { CommandPalette } from "@/components/CommandPalette";
import { getEpisodeDetail, getEpisodes } from "@/lib/api";
import { makeEpisodeDetail } from "@/test/fixtures";

vi.mock("@/lib/api", () => ({
  getEpisodes: vi.fn().mockResolvedValue([]),
  getEpisodeDetail: vi.fn().mockResolvedValue(null),
}));

const mockedGetEpisodes = vi.mocked(getEpisodes);
const mockedGetEpisodeDetail = vi.mocked(getEpisodeDetail);

function renderPalette({
  route = "/",
  open = false,
}: { route?: string; open?: boolean } = {}) {
  const onOpenChange = vi.fn();
  const view = render(
    <MemoryRouter initialEntries={[route]}>
      <div data-testid="location-probe-path">
        <PathProbe />
      </div>
      <Routes>
        <Route path="/episodes" element={<div>episodes page</div>} />
        <Route path="/episodes/:id" element={<div>episode detail page</div>} />
        <Route path="*" element={<div>other page</div>} />
      </Routes>
      <CommandPalette open={open} onOpenChange={onOpenChange} />
    </MemoryRouter>,
  );
  return { ...view, onOpenChange };
}

function PathProbe() {
  const location = useLocation();
  return <span data-testid="pathname">{location.pathname}</span>;
}

beforeEach(() => {
  mockedGetEpisodes.mockResolvedValue([]);
  mockedGetEpisodeDetail.mockResolvedValue(makeEpisodeDetail());
});

describe("CommandPalette", () => {
  it("opens on ctrl+k keydown", () => {
    const { onOpenChange } = renderPalette({ open: false });
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    expect(onOpenChange).toHaveBeenCalledWith(true);
  });

  it("filters results by query", async () => {
    const user = userEvent.setup();
    renderPalette({ open: true });
    const input = await screen.findByPlaceholderText("Search commands and episodes…");
    await user.type(input, "ledger");
    expect(screen.getByText("Go to Ledger")).toBeInTheDocument();
    expect(screen.queryByText("Go to Overview")).not.toBeInTheDocument();
  });

  it("navigates to /episodes on Enter", async () => {
    const user = userEvent.setup();
    renderPalette({ open: true });
    const input = await screen.findByPlaceholderText("Search commands and episodes…");
    await user.type(input, "Go to Episodes");
    await user.keyboard("{Enter}");
    await waitFor(() => {
      expect(screen.getByTestId("pathname").textContent).toBe("/episodes");
    });
  });

  it("closes on Escape", async () => {
    const user = userEvent.setup();
    const { onOpenChange } = renderPalette({ open: true });
    const input = await screen.findByPlaceholderText("Search commands and episodes…");
    await user.type(input, "{Escape}");
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("traps focus inside the dialog on Tab", async () => {
    const user = userEvent.setup();
    renderPalette({ open: true });
    const dialog = screen.getByRole("dialog");
    await waitFor(() => {
      expect(screen.getByPlaceholderText("Search commands and episodes…")).toHaveFocus();
    });
    await user.tab();
    const active = document.activeElement as HTMLElement;
    expect(dialog.contains(active)).toBe(true);
    expect(active.tagName).toBe("BUTTON");
  });

  it("shows the context group on an /episodes/:id route", async () => {
    mockedGetEpisodeDetail.mockResolvedValue(
      makeEpisodeDetail({
        timeline: [
          {
            seq: 7,
            ts_utc: "2026-08-20T10:00:00Z",
            subscription_id: "sub_ctx",
            trigger_event: "subscription.halted",
            policy_eval: null,
            score: null,
            human_gate: 0,
            rzp_call: null,
            outcome: "EPISODE_CREATED",
            recovered_paise: 0,
            mode: "NORMAL",
          },
        ],
      }),
    );
    renderPalette({ route: "/episodes/ep_ctx", open: true });
    expect(await screen.findByText("Episode ep_ctx")).toBeInTheDocument();
    expect(screen.getByText("Copy episode id")).toBeInTheDocument();
    expect(screen.getByText("Go to its first ledger row")).toBeInTheDocument();
  });
});
