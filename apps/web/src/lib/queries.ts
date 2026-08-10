/**
 * TanStack Query hooks for the GET side of the API (portfolio, lineage).
 * Enabled only in api mode — mock mode keeps reading local fixtures, so
 * the mock experience is untouched. Contract drift found while parsing
 * feeds the same visible banner as the streaming driver.
 */

import { useQuery } from "@tanstack/react-query";

import {
  fetchPortfolioLatest,
  fetchRounds,
  fetchRoundsBrief,
  fetchSessionLineage,
} from "@/lib/apiDriver";
import type { PortfolioSnapshotData } from "@/lib/contract";
import type { BriefData, RoundsData } from "@/lib/rounds";
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
 * `GET /v1/rounds/brief` — what changed at this load.
 *
 * `staleTime: Infinity` and no interval, on purpose. A brief is a
 * statement about ONE data load: it does not change until a new load
 * lands, and a surface that silently re-fetched it would be a live
 * dashboard, which is the thing Rounds is positioned against. New loads
 * arrive through the watermark, and the watermark is in the query key.
 *
 * `retry: false`. This route EVALUATES — it re-runs every watch and
 * verifies every claimed resolution — so a failed call has usually done
 * real work before failing, and three silent retries would triple it while
 * the analyst reads a spinner.
 */
export function useBriefQuery(enabled: boolean, watermarkId: string) {
  return useQuery<BriefData>({
    queryKey: ["rounds", "brief", watermarkId],
    queryFn: () => fetchRoundsBrief({ onDrift }),
    enabled,
    staleTime: Infinity,
    retry: false,
  });
}

/*
 * `GET /v1/rounds/pins` is deliberately NOT a query hook.
 *
 * It lives on the store (`loadWatches`), because the component that needs
 * it most is `WatchThis` — a leaf that renders inside a chart's action row,
 * on a finding, and on a worklist header. A `useQuery` there would make
 * every one of those call sites, and every test that mounts an answer,
 * require a `QueryClientProvider` they have no other reason to have. The
 * store is already the seam every one of them reads.
 */

/** `GET /v1/rounds` — the watches, evaluated. Same discipline as the brief. */
export function useRoundsQuery(enabled: boolean, watermarkId: string) {
  return useQuery<RoundsData>({
    queryKey: ["rounds", "tiles", watermarkId],
    queryFn: () => fetchRounds({ onDrift }),
    enabled,
    staleTime: Infinity,
    retry: false,
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
