/**
 * TanStack Query hooks for the GET side of the API (portfolio, lineage).
 * Enabled only in api mode — mock mode keeps reading local fixtures, so
 * the mock experience is untouched. Contract drift found while parsing
 * feeds the same visible banner as the streaming driver.
 */

import { useQuery } from "@tanstack/react-query";

import {
  fetchInvestigation,
  fetchPortfolioLatest,
  fetchMonitors,
  fetchMonitorsBrief,
  fetchSessionLineage,
} from "@/lib/apiDriver";
import type { PortfolioSnapshotData, TurnResponseParse } from "@/lib/contract";
import type { BriefData, MonitorsData } from "@/lib/monitors";
import { useSessionStore } from "@/lib/store";
import type { SessionLineageData } from "@/lib/types";

const onDrift = (paths: string[]): void =>
  useSessionStore.getState().reportContractDrift(paths);

export function usePortfolioQuery(enabled: boolean) {
  return useQuery<PortfolioSnapshotData>({
    queryKey: ["portfolio", "latest"],
    queryFn: () => fetchPortfolioLatest({ onDrift }),
    enabled,
    staleTime: 5 * 60_000,
    retry: 1,
  });
}

/**
 * `GET /v1/monitors/brief` — what changed at this load.
 *
 * `staleTime: Infinity` and no interval, on purpose. A brief is a
 * statement about ONE data load: it does not change until a new load
 * lands, and a surface that silently re-fetched it would be a live
 * dashboard, which is the thing Monitors is positioned against. New loads
 * arrive through the watermark, and the watermark is in the query key.
 *
 * `retry: false`. This route EVALUATES — it re-runs every monitor and
 * verifies every claimed resolution — so a failed call has usually done
 * real work before failing, and three silent retries would triple it while
 * the analyst reads a spinner.
 */
export function useBriefQuery(enabled: boolean, watermarkId: string) {
  return useQuery<BriefData>({
    queryKey: ["monitors", "brief", watermarkId],
    queryFn: () => fetchMonitorsBrief({ onDrift }),
    enabled,
    staleTime: Infinity,
    retry: false,
  });
}

/*
 * `GET /v1/monitors/pins` is deliberately NOT a query hook.
 *
 * It lives on the store (`loadMonitors`), because the component that needs
 * it most is `MonitorThis` — a leaf that renders inside a chart's action row,
 * on a finding, and on a worklist header. A `useQuery` there would make
 * every one of those call sites, and every test that mounts an answer,
 * require a `QueryClientProvider` they have no other reason to have. The
 * store is already the seam every one of them reads.
 */

/** `GET /v1/monitors` — the monitors, evaluated. Same discipline as the brief. */
export function useMonitorsQuery(enabled: boolean, watermarkId: string) {
  return useQuery<MonitorsData>({
    queryKey: ["monitors", "tiles", watermarkId],
    queryFn: () => fetchMonitors({ onDrift }),
    enabled,
    staleTime: Infinity,
    retry: false,
  });
}

/**
 * `GET /v1/investigations/{iid}` — one stored investigation, read for its
 * FIGURE rather than for its thread.
 *
 * Home draws the top-ranked thing on this load as a real chart, and a real
 * chart means the rows the investigation behind it actually published —
 * never a shape composed on the landing page from a summary. This is the
 * read that fetches them.
 *
 * `staleTime: Infinity`, no interval, `retry: 1`. A stored investigation is
 * immutable: it is a record of a question that was answered at a data load,
 * so re-reading it can only ever return the same bytes. The one retry is
 * for a dropped connection, not for a changing answer.
 *
 * No `pin`: the context header belongs to the SESSION this turn lives in,
 * and Home has no session. The parse is the same minus the header (see
 * `parseInvestigationResponse`), which is exactly right for a figure — a
 * header pinned to invented dates is the thing that must not happen.
 */
export function useInvestigationQuery(investigationId: string, enabled: boolean) {
  return useQuery<TurnResponseParse>({
    queryKey: ["investigation", investigationId],
    queryFn: () => fetchInvestigation(investigationId, { onDrift }),
    enabled: enabled && investigationId !== "",
    staleTime: Infinity,
    retry: 1,
  });
}

export function useSessionLineageQuery(
  sessionId: string,
  turnCount: number,
  enabled: boolean,
) {
  return useQuery<SessionLineageData>({
    // Keyed by turn count: every completed turn refetches the DAG.
    queryKey: ["lineage", sessionId, turnCount],
    queryFn: () => fetchSessionLineage(sessionId, { onDrift }),
    enabled,
    placeholderData: (previous) => previous,
    retry: 1,
  });
}
