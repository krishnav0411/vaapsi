/**
 * CountUp: with prefers-reduced-motion the final value renders
 * immediately (no animation); with motion allowed it settles on the
 * exact final value. Formatting is delegated to the format helpers.
 */

import { render, screen } from "@testing-library/react";
import { waitFor } from "@testing-library/react";
import { describe, expect, it, vi, afterEach } from "vitest";

import { CountUp } from "@/components/CountUp";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("CountUp", () => {
  it("renders the final value immediately under prefers-reduced-motion", () => {
    vi.spyOn(window, "matchMedia").mockReturnValue({
      matches: true,
      media: "(prefers-reduced-motion: reduce)",
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    } as unknown as MediaQueryList);
    render(<CountUp value={1234567} />);
    expect(screen.getByText("12,34,567")).toBeInTheDocument();
  });

  it("animates and settles on the exact final value", async () => {
    render(<CountUp value={999} durationMs={20} />);
    await waitFor(
      () => {
        expect(screen.getByText("999")).toBeInTheDocument();
      },
      { timeout: 2000 },
    );
  });

  it("uses the provided formatter (paise → ₹)", () => {
    vi.spyOn(window, "matchMedia").mockReturnValue({
      matches: true,
      media: "(prefers-reduced-motion: reduce)",
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    } as unknown as MediaQueryList);
    render(<CountUp value={49900} format={(n) => `₹${(n / 100).toLocaleString("en-IN")}`} />);
    expect(screen.getByText("₹499")).toBeInTheDocument();
  });
});
