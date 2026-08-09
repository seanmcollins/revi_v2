import Workspace from "@/components/workspace/Workspace";

/**
 * `/s/{session_id}` — the permalink.
 *
 * The one question every buyer asks in the first demo is whether an
 * analyst can send an investigation to the CFO or paste it into a ticket,
 * and until this route existed the answer was no: there was no per-session
 * URL anywhere in the app, while the archive dialog promised one in
 * writing. The store already did the work (`switchSession` re-joins the
 * session server-side and rebuilds its thread from the lineage plus each
 * turn's stored investigation); all this adds is that the id can arrive
 * from an address bar.
 *
 * A server component whose only job is to read the segment: `params` is a
 * promise in this version of Next, and the workspace itself is a client
 * component that owns the driver and the store.
 */
export default async function SessionPermalinkPage({
  params,
}: {
  params: Promise<{ sessionId: string }>;
}) {
  const { sessionId } = await params;
  return <Workspace initialSessionId={sessionId} />;
}
