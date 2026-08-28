/**
 * ApprovalsPage: calm empty state on an empty queue, a pending approval
 * with its ₹ amount, approve calls the decide endpoint with the right
 * args, and a reject without a typed reason shows the inline error
 * without ever hitting the endpoint. api mocked at the boundary.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import { ApprovalsPage } from "@/pages/ApprovalsPage";
import { makeApproval, makeApprovalDetail } from "@/test/fixtures";

vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
  postDecide: vi.fn().mockResolvedValue(undefined),
  useApi: vi.fn(),
}));

import * as api from "@/lib/api";

const mockedUseApi = api.useApi as unknown as Mock;
const mockedPostDecide = api.postDecide as unknown as Mock;

beforeEach(() => {
  mockedPostDecide.mockClear();
  mockedPostDecide.mockResolvedValue(undefined);
});

function mockPending(approvals: ReturnType<typeof makeApproval>[]) {
  mockedUseApi.mockImplementation((path: string) => {
    if (path === "/api/approvals/pending") {
      return { data: { approvals }, error: null, loading: false, refetch: vi.fn() };
    }
    if (path === "/api/approvals/ap_1/detail") {
      return {
        data: makeApprovalDetail(),
        error: null,
        loading: false,
        refetch: vi.fn(),
      };
    }
    return { data: null, error: null, loading: true, refetch: vi.fn() };
  });
}

describe("ApprovalsPage", () => {
  it("shows the calm empty state on an empty mock", () => {
    mockPending([]);
    render(<ApprovalsPage />);
    expect(screen.getByText("Nothing awaiting judgment")).toBeInTheDocument();
    expect(screen.getByText(/Nothing is gated right now/)).toBeInTheDocument();
  });

  it("renders a pending approval with its ₹ amount", () => {
    mockPending([makeApproval()]);
    render(<ApprovalsPage />);
    expect(screen.getByText("₹499")).toBeInTheDocument();
    expect(screen.getByText("ep_01")).toBeInTheDocument();
    expect(screen.getByText("Gate reason:")).toBeInTheDocument();
  });

  it("approve calls the decide endpoint with decision=approve", async () => {
    const user = userEvent.setup();
    mockPending([makeApproval()]);
    render(<ApprovalsPage />);
    await user.click(screen.getByRole("button", { name: /Review →/i }));
    const approveButton = await screen.findByRole("button", { name: "Approve" });
    await user.click(approveButton);
    await waitFor(() => {
      expect(mockedPostDecide).toHaveBeenCalledWith("ap_1", "approve", "");
    });
    expect(
      await screen.findByText("Approved ap_1 — recorded in the ledger."),
    ).toBeInTheDocument();
  });

  it("reject without a reason shows the error and never calls decide", async () => {
    const user = userEvent.setup();
    mockPending([makeApproval()]);
    render(<ApprovalsPage />);
    await user.click(screen.getByRole("button", { name: /Review →/i }));
    const rejectButton = await screen.findByRole("button", { name: "Reject" });
    await user.click(rejectButton);
    expect(
      await screen.findByText(/A typed reason is required to reject/),
    ).toBeInTheDocument();
    expect(mockedPostDecide).not.toHaveBeenCalled();
  });
});
