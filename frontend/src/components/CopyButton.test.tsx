/**
 * CopyButton: writes the exact value to the clipboard, flips to
 * "Copied" feedback, and carries an accessible name. The clipboard stub
 * is re-applied per test (user-event swaps navigator.clipboard for its
 * own non-mock stub, so clicks here use fireEvent to keep the spy).
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CopyButton } from "@/components/CopyButton";

const writeText = vi.fn<(data: string) => Promise<void>>();

beforeEach(() => {
  writeText.mockClear();
  writeText.mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText },
  });
});

describe("CopyButton", () => {
  it("writes the exact value to the clipboard", async () => {
    render(<CopyButton value="ep_abc123" label="Copy episode id" />);
    fireEvent.click(screen.getByRole("button", { name: "Copy episode id" }));
    await waitFor(() => {
      expect(writeText).toHaveBeenCalledWith("ep_abc123");
    });
  });

  it("shows Copied feedback after a successful write", async () => {
    render(<CopyButton value="ep_abc123" />);
    fireEvent.click(screen.getByRole("button"));
    expect(await screen.findByText("Copied")).toBeInTheDocument();
  });

  it("has an aria-label", () => {
    render(<CopyButton value="row_hash_64" />);
    expect(screen.getByLabelText("Copy row_hash_64")).toBeInTheDocument();
  });
});
