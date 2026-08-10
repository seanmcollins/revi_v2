import { useEffect } from "react";
import { Outlet } from "react-router-dom";

import { QueryProvider } from "@/components/providers/QueryProvider";
import { TooltipProvider } from "@/components/ui/tooltip";

/**
 * What the Next root layout was: the two providers every route needs, and
 * the document title.
 *
 * The title is one string for the whole app — Next declared it once in
 * `metadata` and no route overrode it — so `index.html` already carries it
 * and this only re-asserts it. That is not redundant: a router keeps the
 * document across navigations, so anything that ever sets `document.title`
 * per route would leave the last route's title behind on the next one.
 * Asserting it from the layout makes the invariant ("the title is the
 * app's, not the route's") hold by construction.
 *
 * `delayDuration={200}` is the tooltip delay the layout set. It is a real
 * parity value: at 0 the header's icon tooltips fire on pointer transit.
 */
export const DOCUMENT_TITLE = "Revi — RCM Investigations";

export function RootLayout() {
  useEffect(() => {
    document.title = DOCUMENT_TITLE;
  }, []);

  return (
    <QueryProvider>
      <TooltipProvider delayDuration={200}>
        <Outlet />
      </TooltipProvider>
    </QueryProvider>
  );
}
