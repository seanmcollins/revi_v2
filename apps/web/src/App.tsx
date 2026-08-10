import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { MonitorsSurface } from "@/components/monitors/MonitorsSurface";
import { HomeRoute } from "@/routes/HomeRoute";
import { NotFound } from "@/routes/NotFound";
import { RootLayout } from "@/routes/RootLayout";
import { WorkspaceRoute } from "@/routes/WorkspaceRoute";

/**
 * The route table — four surfaces, one legacy address, a floor under
 * everything else, and the layout that carries the providers.
 *
 * `/` IS HOME, AND HOME IS NOT THE WORKSPACE. It was: the workspace
 * rendered an empty composer there and rewrote its own address to
 * `/s/{id}` the moment the first turn minted a session. Home replaced the
 * empty state only — a conversation still lives at `/s/{id}`, a permalink
 * still opens there, and "New chat" still comes back here.
 *
 * TWO OF THE FOUR STILL RENDER THE SAME ELEMENT TYPE, ON PURPOSE.
 *
 * `/s/:sessionId` and `/i/:investigationId` are both the workspace, and the
 * workspace REWRITES ITS OWN ADDRESS: `/i/{iid}` resolves to the session
 * that turn belongs to and moves the bar to `/s/{sid}`. Under Next that was
 * a raw `history.replaceState` — no route transition, no remount, the same
 * component instance running throughout. Here it is a router navigation,
 * and a router navigation swaps the matched route's element. Naming those
 * two with two different components would turn that rewrite into an unmount
 * and a remount of the whole workspace — a new driver, a re-torn-down
 * health poll, a thread rebuilt underneath the analyst mid-conversation.
 *
 * With one component type at that position React reconciles instead of
 * remounting, and the rewrite is as invisible as `replaceState` was. The
 * segment is read inside `WorkspaceRoute`, which pins it at mount for the
 * same reason: under Next the page's `params` did not change when the URL
 * was rewritten, and they must not here either.
 *
 * `/` → `/s/{id}` IS now a real mount, and that is correct rather than
 * tolerated: Home is a different surface from the workspace, the same way
 * Monitors is, and the turn that causes the move is already streaming in
 * the store when the workspace arrives. The workspace's own "open this
 * session" effect no-ops on a session that is streaming or already on
 * screen, so nothing is re-joined — see `store.switchSession`.
 *
 * AND NOTHING FALLS THROUGH ANY MORE. The table used to be those four
 * paths and nothing else, so every other address resolved to no route at
 * all: the layout mounted, `Outlet` rendered null, and the app drew a blank
 * page. The owner hit it on `/rounds` — what Monitors was called before the
 * rename, still in browser autocomplete — and read the blank page as the
 * product being broken. Both halves of that are fixed below: the one
 * address we know about is a redirect to where it went, and everything else
 * lands on a page that says so.
 */
export function AppRoutes() {
  return (
    <Routes>
      <Route element={<RootLayout />}>
        <Route path="/" element={<HomeRoute />} />
        <Route path="/s/:sessionId" element={<WorkspaceRoute />} />
        <Route path="/i/:investigationId" element={<WorkspaceRoute />} />
        {/* Monitors is a different surface, so it is a different element
            and a real mount — which is what its arrival announcement and
            focus move depend on. */}
        <Route path="/monitors" element={<MonitorsSurface />} />
        {/* THE OLD NAME FOR THAT SURFACE. `/rounds` shipped, so it is in
            bookmarks and in the address bar's autocomplete, and a renamed
            page that answers its old address with nothing teaches people
            the app is unreliable. `replace` so the redirect leaves no entry
            of its own: Back from Monitors goes to wherever the reader came
            from, rather than to `/rounds` and straight forward again. */}
        <Route path="/rounds" element={<Navigate to="/monitors" replace />} />
        {/* The floor. Inside the layout group on purpose — a not-found
            address is still somewhere in this app, and it should arrive
            with the app's providers and the app's title rather than as a
            bare page hanging off the router. */}
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  );
}

/**
 * Split from the table above so the table can be mounted under a
 * `MemoryRouter` — `routing.test.tsx` asserts the no-remount property that
 * the whole permalink lifecycle rests on, and it cannot do that through a
 * `BrowserRouter` this component owns.
 */
export function App() {
  return (
    <BrowserRouter>
      <AppRoutes />
    </BrowserRouter>
  );
}
