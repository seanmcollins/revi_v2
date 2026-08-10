import { useState } from "react";
import { useParams } from "react-router-dom";

import Workspace from "@/components/workspace/Workspace";

/**
 * The workspace, in whatever session this browser is already in — and the
 * three routes that mount it.
 *
 *   `/`                     whatever session this browser is in
 *   `/s/{session_id}`       that one — the permalink the archive dialog
 *                           promises in writing
 *   `/i/{investigation_id}` one answered turn, resolved to the SESSION it
 *                           belongs to and opened there, because a turn
 *                           read outside its conversation has lost the
 *                           filters, the cohort and the referents that made
 *                           its numbers mean what they mean
 *
 * THE SEGMENT IS PINNED AT MOUNT.
 *
 * Under Next these were three page components and the segment arrived as
 * `params`; a `history.replaceState` that rewrote the address bar did not
 * re-run them, so the props a mounted workspace held never changed
 * underneath it. Here the same rewrite is a router navigation, `useParams`
 * is live, and an un-pinned read would hand the workspace an
 * `initialSessionId` for the session it is ALREADY IN the moment it
 * published its own permalink — asking the store to re-join and rebuild a
 * thread mid-conversation. `useState`'s initializer runs once per mount,
 * which is exactly the lifetime `params` had.
 *
 * A real navigation (Monitors → back, a pasted link) is a real mount, and
 * pins the segment it arrived with.
 */
export function WorkspaceRoute() {
  const params = useParams<{ sessionId?: string; investigationId?: string }>();
  const [pinned] = useState(() => ({
    sessionId: params.sessionId,
    investigationId: params.investigationId,
  }));

  return (
    <Workspace
      initialSessionId={pinned.sessionId}
      initialInvestigationId={pinned.investigationId}
    />
  );
}
