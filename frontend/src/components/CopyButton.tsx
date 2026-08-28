/**
 * Shared copy-to-clipboard button: writes the exact text, shows "Copied"
 * for a beat, and degrades silently when the clipboard is unavailable
 * (permissions/insecure context) — the value is visible text anyway; the
 * button is a convenience, never the only path to the data.
 */

import { useState } from "react";

import { cn } from "@/lib/utils";

export function CopyButton({ value, label, className }: { value: string; label?: string; className?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(value);
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        } catch {
          // Clipboard unavailable — the value stays fully visible text.
        }
      }}
      title={label ?? `Copy ${value}`}
      className={cn(
        "shrink-0 rounded-button border border-border-normal px-8 py-2 text-xs font-medium text-text-subtle hover:border-border-hover hover:text-text-normal",
        className,
      )}
    >
      {copied ? "Copied" : "Copy"}
    </button>
  );
}
