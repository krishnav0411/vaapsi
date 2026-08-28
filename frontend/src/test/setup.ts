/**
 * Vitest setup: jest-dom matchers + the two DOM APIs jsdom lacks and
 * the suite relies on (matchMedia — read by ThemeToggle and CountUp's
 * reduced-motion checks — and a clipboard stub). Tests can override
 * matchMedia per-case with vi.spyOn.
 */

import "@testing-library/jest-dom/vitest";

import { afterEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";

function matchMediaStub(query: string): MediaQueryList {
  return {
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  } as MediaQueryList;
}

if (typeof window.matchMedia !== "function") {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: vi.fn().mockImplementation(matchMediaStub),
  });
}

Object.defineProperty(navigator, "clipboard", {
  configurable: true,
  value: {
    writeText: vi.fn().mockResolvedValue(undefined),
    readText: vi.fn().mockResolvedValue(""),
  },
});

// jsdom has no layout engine — scrollIntoView (palette active-row, etc.)
// becomes a no-op instead of a TypeError.
Element.prototype.scrollIntoView = vi.fn();

afterEach(() => {
  cleanup();
});
