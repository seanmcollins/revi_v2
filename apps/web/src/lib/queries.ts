/**
 * TanStack Query hooks for the GET side of the API (portfolio, lineage).
 * Enabled only in api mode — mock mode keeps reading local fixtures, so
 * the mock experience is untouched. Contract drift found while parsing
 * feeds the same visible banner as the streaming driver.
 */

import { useQuery } from "@tanstack/react-query";

import {
  fetchPortfolioLatest,
  fetchSessionLineage,
  type PortfolioFetchResult,
} from "@/lib/apiDriver";
import { useSessionStore } from "@/lib/store";
import type { SessionLineageData } from "@/lib/types";

const onDrift = (paths: string[]): void =>
  useSessionStore.getState().reportContractDrift(paths);

export function usePortfolioQuery(enabled: boolean) {
  return useQuery<PortfolioFetchResult>({
    queryKey: ["portfolio", "latest"],
    queryFn: () => fetchPortfolioLatest({ onDrift }),
    enabled,
    staleTime: 5 * 60_000,
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
