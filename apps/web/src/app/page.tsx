import Workspace from "@/components/workspace/Workspace";

/**
 * The workspace, in whatever session this browser is already in.
 *
 * The component lives outside `app/` because two routes mount it: this one
 * and `/s/{session_id}`, the permalink. Once a session exists server-side
 * the workspace rewrites the address bar to its own `/s/…` path, so a
 * thread opened here is linkable from the moment it has something to link
 * to.
 */
export default function Page() {
  return <Workspace />;
}
