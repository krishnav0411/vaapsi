/**
 * LedgerPage against a mocked api module: rows render, the chain-valid
 * chip comes from the mock verifier, the tamper-demo copy ships, and
 * clicking a row expands the full detail with the 64-char hash visible.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";
import { MemoryRouter } from "react-router-dom";

import { LedgerPage } from "@/pages/LedgerPage";
import {
  makeLedgerListResponse,
  makeLedgerRowDetail,
  makeLedgerVerifyResponse,
} from "@/test/fixtures";

vi.mock("@/lib/api", () => ({
  useApi: vi.fn(),
  getLedgerRow: vi.fn(),
  verifyLedger: vi.fn().mockResolvedValue({ valid: true, rows: 2, broken_seq: null, detail: "chain ok" }),
  runTamperDemo: vi.fn(),
}));

import * as api from "@/lib/api";

const mockedUseApi = api.useApi as unknown as Mock;
const mockedGetLedgerRow = api.getLedgerRow as unknown as Mock;

function mockApi() {
  mockedUseApi.mockImplementation((path: string) => {
    if (path.startsWith("/api/ledger?")) {
      return {
        data: makeLedgerListResponse(),
        error: null,
        loading: false,
        refetch: vi.fn(),
      };
    }
    if (path === "/api/ledger/verify") {
      return {
        data: makeLedgerVerifyResponse(),
        error: null,
        loading: false,
        refetch: vi.fn(),
      };
    }
    return { data: null, error: null, loading: true, refetch: vi.fn() };
  });
}

beforeEach(() => {
  mockApi();
  mockedGetLedgerRow.mockReset();
});

describe("LedgerPage", () => {
  it("renders the mock ledger rows", async () => {
    render(
      <MemoryRouter>
        <LedgerPage />
      </MemoryRouter>,
    );
    expect(await screen.findByText("sub_01")).toBeInTheDocument();
    expect(screen.getByText("sub_02")).toBeInTheDocument();
    expect(screen.getByText("seq 1–2 of 2")).toBeInTheDocument();
  });

  it("shows the chain-valid chip from the mock verify", async () => {
    render(
      <MemoryRouter>
        <LedgerPage />
      </MemoryRouter>,
    );
    expect(await screen.findByText("chain valid · 2 rows")).toBeInTheDocument();
  });

  it("shows the tamper-demo card copy", () => {
    render(
      <MemoryRouter>
        <LedgerPage />
      </MemoryRouter>,
    );
    expect(screen.getByText("Prove it: tamper demo")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /run tamper demo/i })).toBeInTheDocument();
  });

  it("expands the row detail with the full hash on click", async () => {
    const user = userEvent.setup();
    const detail = makeLedgerRowDetail();
    mockedGetLedgerRow.mockResolvedValue(detail);
    render(
      <MemoryRouter>
        <LedgerPage />
      </MemoryRouter>,
    );
    await screen.findByText("sub_01");
    await user.click(screen.getByText("sub_01"));
    expect(await screen.findByText("Row 1 · full detail")).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByText("b".repeat(64))).toBeInTheDocument();
    });
    expect(screen.getByText(/canonical_json/)).toBeInTheDocument();
  });
});
