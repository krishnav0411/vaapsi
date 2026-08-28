/**
 * DrillsPage: three drill cards render from the mock catalog; running a
 * drill enters the "running" state, then the result panel appears on
 * resolve. runDrill is mocked — no network.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import { DrillsPage } from "@/pages/DrillsPage";
import { makeDrill, makeDrillResult } from "@/test/fixtures";

vi.mock("@/lib/api", () => ({
  useApi: vi.fn(),
  runDrill: vi.fn(),
}));

import * as api from "@/lib/api";

const mockedUseApi = api.useApi as unknown as Mock;
const mockedRunDrill = api.runDrill as unknown as Mock;

beforeEach(() => {
  mockedUseApi.mockImplementation((path: string) => {
    if (path === "/api/drills") {
      return {
        data: {
          drills: [
            makeDrill(),
            makeDrill({ drill_id: "gateway_5xx", title: "Gateway 5xx storm" }),
            makeDrill({ drill_id: "llm_outage", title: "LLM outage" }),
          ],
        },
        error: null,
        loading: false,
        refetch: vi.fn(),
      };
    }
    return { data: null, error: null, loading: true, refetch: vi.fn() };
  });
  mockedRunDrill.mockReset();
});

describe("DrillsPage", () => {
  it("renders the three drill cards from the mock", () => {
    render(<DrillsPage />);
    expect(screen.getByText("Replay storm")).toBeInTheDocument();
    expect(screen.getByText("Gateway 5xx storm")).toBeInTheDocument();
    expect(screen.getByText("LLM outage")).toBeInTheDocument();
  });

  it("enters running state, then shows the result panel on resolve", async () => {
    const user = userEvent.setup();
    let resolveRun: (result: ReturnType<typeof makeDrillResult>) => void = () => {};
    mockedRunDrill.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveRun = resolve;
        }),
    );
    render(<DrillsPage />);
    const runButtons = screen.getAllByRole("button", { name: /run drill/i });
    expect(runButtons).toHaveLength(3);
    await user.click(runButtons[0]);
    expect(screen.getByText(/running… \(up to 30s\)/)).toBeInTheDocument();
    resolveRun(makeDrillResult());
    await waitFor(() => {
      expect(screen.getByText("42 webhooks replayed, chain stayed valid")).toBeInTheDocument();
    });
    expect(screen.getByText("passed")).toBeInTheDocument();
  });
});
