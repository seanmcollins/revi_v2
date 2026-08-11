import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { HomeRoute } from "@/routes/HomeRoute";
import { NotFound } from "@/routes/NotFound";
import { ResearchRoute } from "@/routes/ResearchRoute";
import { RootLayout } from "@/routes/RootLayout";
import { WorkspaceRoute } from "@/routes/WorkspaceRoute";

/**
 * The route table — three surfaces, two legacy addresses, a floor under
 * everything else, and the layout that carries the providers.
 *
 * IT WAS FOUR SURFACES. `/monitors` rendered a second landing page — this
 * load's brief, a grid of monitor tiles, the lead lifecycle — and Home
 * rendered the better version of the same three things. The owner's
 * decision was to keep one: "the /monitors view is pointless and the
 * current home view is just a superior version." So Home is the only
 * landing surface, the monitors are managed inside its digest (a tile
 * expands in place into everything the retired grid offered), and the two
 * addresses that named the retired page both land on Home.
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
        {/* A DEEP-RESEARCH RUN, at its own address. Its own element and a
            real mount, like Monitors: the run surface is not a
            conversation — it has no composer and no thread — and it owns a
            subscription keyed on the run id, so it must not reconcile
            across two different runs. The run also persists as an
            investigation inside a session of its own, so `/s/{id}` still
            resolves to the same work as the restored answer the server
            stored; this route is that work read as the composed report. */}
        <Route path="/r/:runId" element={<ResearchRoute />} />
        {/* THE TWO ADDRESSES OF THE RETIRED SURFACE. Both shipped, so both
            are in bookmarks and in the address bar's autocomplete, and a
            page that answers its old address with nothing teaches people
            the app is unreliable — the owner hit exactly that on `/rounds`
            (what Monitors was called before the rename) and read the blank
            page as the product being broken.

            They land on Home rather than on Home's monitors zone. What
            somebody bookmarked was a whole surface, and Home IS that
            surface now — arriving mid-page, past the brief this load
            produced, would hide the first thing they came for. The controls
            that mean "take me to my monitors" — the rail, ⌘K, the note
            under an answer that started one — carry `#home-monitors` and do
            move focus into the zone.

            `replace` so neither redirect leaves an entry of its own: Back
            from Home goes to wherever the reader came from, rather than to
            `/monitors` and straight forward again. */}
        <Route path="/monitors" element={<Navigate to="/" replace />} />
        <Route path="/rounds" element={<Navigate to="/" replace />} />
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
