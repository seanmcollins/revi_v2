import { useState } from "react";
import { useParams } from "react-router-dom";

import { ResearchSurface } from "@/components/research/ResearchSurface";

/**
 * `/r/{run_id}` — one deep-research run.
 *
 * The segment is pinned at mount for the same reason `WorkspaceRoute` pins
 * its own: the surface owns a subscription keyed on the id (the run's GET,
 * then its progress stream), and a live `useParams` read would let a
 * re-render swap the id underneath an open stream. A real navigation to
 * another run is a real mount and pins the id it arrived with.
 */
export function ResearchRoute() {
  const params = useParams<{ runId?: string }>();
  const [pinned] = useState(() => params.runId ?? "");
  return <ResearchSurface runId={pinned} />;
}
