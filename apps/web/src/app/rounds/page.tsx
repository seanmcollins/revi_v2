import { RoundsSurface } from "@/components/rounds/RoundsSurface";

/**
 * `/rounds` — the proactive surface, as a first-class place.
 *
 * A route rather than a panel, for two reasons that are the same reason.
 * It is where an analyst starts their day, so it needs an address they can
 * bookmark and a browser Back that returns to it. And the cold start
 * decides between this and the thread on every visit — a redirect needs
 * somewhere to redirect TO.
 */
export default function RoundsPage() {
  return <RoundsSurface />;
}
