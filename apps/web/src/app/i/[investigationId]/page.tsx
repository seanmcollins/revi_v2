import Workspace from "@/components/workspace/Workspace";

/**
 * `/i/{investigation_id}` — a link to one answered turn.
 *
 * It opens the SESSION that turn belongs to (the investigation payload
 * publishes `session_id` as a required field) rather than rendering the
 * turn on its own, because a turn read outside its conversation has lost
 * the filters, the cohort and the referents that made its numbers mean
 * what they mean. The workspace then rebuilds the thread exactly as the
 * session permalink does, and the linked turn is in it.
 */
export default async function InvestigationPermalinkPage({
  params,
}: {
  params: Promise<{ investigationId: string }>;
}) {
  const { investigationId } = await params;
  return <Workspace initialInvestigationId={investigationId} />;
}
