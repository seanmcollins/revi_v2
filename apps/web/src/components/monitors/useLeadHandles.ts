/**
 * A BRIEF ROW'S DESTINATION, and the state the lead behind it is in.
 *
 * A brief entry carries a sentence and a dollar figure; three of four live
 * rows carry no investigation of their own. What makes them work rather
 * than notify is the LEAD behind them — the worklist card with the typed
 * drill spec, and whatever this browser has since changed about its status.
 * Joining those two is identical on Monitors and on Home, so it is written
 * once here rather than transcribed into the second surface that renders a
 * brief.
 */

import { useCallback, useMemo } from "react";

import type { BriefLeadHandle } from "@/components/monitors/BriefEntryRow";
import type { LeadRow } from "@/components/monitors/LeadLifecycle";
import type { PortfolioItem } from "@/lib/mock/portfolio";
import { useSessionStore } from "@/lib/store";
import { useAsk } from "@/lib/useAsk";

/**
 * How a lead is opened: the platform re-derives this drill every load to
 * verify claimed resolutions, and the worklist card carries the typed spec
 * that runs it. Submitting it opens a real investigation, and the
 * navigation follows the turn into the session it mints.
 */
export function useOpenLead(): (item: PortfolioItem) => (() => void) | undefined {
  const ask = useAsk();
  return useCallback(
    (item: PortfolioItem) => {
      if (!item.drillable || !item.drillSpec) return undefined;
      const spec = item.drillSpec;
      return () => ask({ spec, anomalyRef: item.referent });
    },
    [ask],
  );
}

/**
 * THE LEADS SOMEBODY IS ACTUALLY WORKING, for the lifecycle zone.
 *
 * The same join as `useLeadHandles` asked a different question: not "how do
 * I open this lead" but "where does it stand". It reads the load's own
 * worklist snapshot — which publishes `lead_status` and the platform's own
 * sentence on every card — merged with whatever this browser has changed
 * since, so a status set thirty seconds ago is not waiting for the next
 * data load to appear.
 *
 * Untouched leads are excluded: they are the worklist, they are thirty of
 * thirty-three, and a lifecycle view that listed them would be the worklist
 * with extra steps.
 *
 * It lived inside the retired Monitors surface. It is here because the zone
 * it feeds moved to Home with it — the standing question "what did we claim
 * we fixed, and did it stick" outlived the page it was written on.
 */
export function useLeadRows(items: readonly PortfolioItem[] | undefined): LeadRow[] {
  const leadStates = useSessionStore((s) => s.leadStates);
  const openLead = useOpenLead();
  return useMemo(() => {
    const rows: LeadRow[] = [];
    for (const item of items ?? []) {
      const liveState = leadStates[item.referent];
      const status = liveState?.status ?? item.leadStatus ?? "open";
      if (status === "open") continue;
      const open = openLead(item);
      rows.push({
        anomalyId: item.referent,
        title: item.title,
        status,
        note: liveState?.verificationNote || liveState?.note || item.leadStatusNote || "",
        ...(item.impactCents !== undefined ? { impactCents: item.impactCents } : {}),
        ...(open ? { open } : {}),
        ...(liveState ? { live: liveState } : {}),
      });
    }
    // Anything this browser changed on a lead the snapshot does not carry
    // still belongs here — a status set on a card that has since left the
    // feed is exactly the kind of work that goes missing.
    for (const [anomalyId, state] of Object.entries(leadStates)) {
      if (state.status === "open") continue;
      if (rows.some((row) => row.anomalyId === anomalyId)) continue;
      rows.push({
        anomalyId,
        title: "No longer on this load's worklist",
        status: state.status,
        note: state.verificationNote || state.note,
        live: state,
      });
    }
    return rows;
  }, [items, leadStates, openLead]);
}

/** Every lead on this load's worklist, by anomaly id. */
export function useLeadHandles(
  items: readonly PortfolioItem[] | undefined,
): ReadonlyMap<string, BriefLeadHandle> {
  const leadStates = useSessionStore((s) => s.leadStates);
  const openLead = useOpenLead();
  return useMemo(() => {
    const handles = new Map<string, BriefLeadHandle>();
    for (const item of items ?? []) {
      // What this browser changed a minute ago beats the snapshot, which
      // was composed when the load landed — and it is the only record that
      // carries what the platform MEASURED about the claim.
      const liveState = leadStates[item.referent];
      const open = openLead(item);
      handles.set(item.referent, {
        ...(liveState?.status ?? item.leadStatus
          ? { status: liveState?.status ?? item.leadStatus! }
          : {}),
        ...(liveState?.verificationNote || liveState?.note || item.leadStatusNote
          ? { note: liveState?.verificationNote || liveState?.note || item.leadStatusNote! }
          : {}),
        ...(open ? { open } : {}),
        ...(!open && item.drillUnavailableReason
          ? { unavailableReason: item.drillUnavailableReason }
          : {}),
      });
    }
    return handles;
  }, [items, leadStates, openLead]);
}
