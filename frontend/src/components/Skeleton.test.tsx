/**
 * Skeleton variants + the shared ErrorState contract: TableSkeleton
 * renders exactly N body rows, CardSkeleton announces itself, and
 * ErrorState is role="alert" with a retry button that fires its callback.
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { CardSkeleton, Skeleton, TableSkeleton } from "@/components/Skeleton";
import { ErrorState } from "@/components/ErrorState";

describe("TableSkeleton", () => {
  it("renders exactly N body rows across M columns", () => {
    const { container } = render(<TableSkeleton rows={4} cols={6} />);
    expect(container.querySelectorAll("tbody tr")).toHaveLength(4);
    expect(container.querySelectorAll("tbody tr")[0].querySelectorAll("td")).toHaveLength(6);
    expect(screen.getByRole("status", { name: "Loading table" })).toBeInTheDocument();
  });
});

describe("CardSkeleton", () => {
  it("renders a status block", () => {
    render(<CardSkeleton label="Loading metric card" />);
    expect(screen.getByRole("status", { name: "Loading metric card" })).toBeInTheDocument();
  });
});

describe("Skeleton", () => {
  it("is a bare pulsing block hidden from the tree", () => {
    const { container } = render(<Skeleton width={120} height={16} />);
    expect(container.firstElementChild).toHaveAttribute("aria-hidden");
    expect(container.firstElementChild).toHaveStyle({ width: "120px", height: "16px" });
  });
});

describe("ErrorState", () => {
  it("has role=alert, names the failure, and retry fires the callback", async () => {
    const user = userEvent.setup();
    const onRetry = vi.fn();
    render(
      <ErrorState
        title="Couldn't load the ledger"
        message="The ledger rows failed to load. The API may be restarting — retry."
        onRetry={onRetry}
      />,
    );
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.getByText(/The API may be restarting/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});
