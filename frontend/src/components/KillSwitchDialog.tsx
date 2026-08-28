/**
 * The kill switch ritual over JSON — never auto-fires. The dialog
 * requires the exact typed text KILL; the confirm button stays disabled
 * until then. On 200 the dialog closes and onKilled() refetches
 * /api/mode so every banner and badge flips to KILLED. On 400 the
 * server's detail renders inline and the dialog stays open.
 */

import { useState, type MouseEvent } from "react";
import { Ban } from "lucide-react";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { ApiError, postKill } from "@/lib/api";

export function KillSwitchDialog({ onKilled }: { onKilled: () => void }) {
  const [open, setOpen] = useState(false);
  const [confirmText, setConfirmText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const armed = confirmText.trim() === "KILL" && !submitting;

  function handleOpenChange(next: boolean) {
    setOpen(next);
    if (!next) {
      setConfirmText("");
      setError(null);
      setSubmitting(false);
    }
  }

  async function fire(event: MouseEvent<HTMLButtonElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await postKill(confirmText.trim());
      handleOpenChange(false);
      onKilled();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "kill switch request failed");
      setSubmitting(false);
    }
  }

  return (
    <AlertDialog open={open} onOpenChange={handleOpenChange}>
      <AlertDialogTrigger asChild>
        <button
          type="button"
          className="flex h-control-sm w-full items-center justify-center gap-8 rounded-button border border-negative-text bg-surface px-12 text-sm font-medium text-negative-text hover:bg-negative-bg"
        >
          <Ban className="h-16 w-16" aria-hidden />
          Kill switch
        </button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Activate kill switch?</AlertDialogTitle>
          <AlertDialogDescription>
            Blocks all outbound actions and outreach approvals. This is ONE-WAY — the engine
            resumes NORMAL only after a process restart. Type KILL to confirm.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <input
          value={confirmText}
          onChange={(event) => setConfirmText(event.target.value)}
          placeholder='Type "KILL"'
          aria-label='Type KILL to confirm the kill switch'
          className="h-control-md w-full rounded-button border border-border-normal bg-surface px-12 text-sm text-text-normal outline-none placeholder:text-text-disabled focus:border-primary-border focus:ring-2 focus:ring-primary-border"
        />
        {error !== null && (
          <p role="alert" className="text-sm text-negative-text">
            {error}
          </p>
        )}
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction onClick={fire} disabled={!armed}>
            {submitting ? "Killing…" : "Activate kill switch"}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
