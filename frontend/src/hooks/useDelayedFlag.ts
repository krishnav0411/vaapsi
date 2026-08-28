/**
 * True only after `active` has held for `delayMs` — the anti-flash gate
 * that keeps skeletons from appearing on sub-300ms loads. The timer is
 * cleared on unmount and whenever `active` flips, so a slow-then-fast
 * fetch never leaves a stray timeout behind.
 */

import { useEffect, useState } from "react";

export function useDelayedFlag(active: boolean, delayMs = 300): boolean {
  const [show, setShow] = useState(false);

  useEffect(() => {
    if (!active) {
      setShow(false);
      return;
    }
    const timer = setTimeout(() => setShow(true), delayMs);
    return () => clearTimeout(timer);
  }, [active, delayMs]);

  return show;
}
