import { BrowserRouter, Route, Routes } from "react-router-dom";

import { MonitorsSurface } from "@/components/monitors/MonitorsSurface";
import { RootLayout } from "@/routes/RootLayout";
import { WorkspaceRoute } from "@/routes/WorkspaceRoute";

/**
 * The route table — four routes and the layout that carries the providers.
 *
 * THREE OF THE FOUR RENDER THE SAME ELEMENT TYPE, ON PURPOSE.
 *
 * `/`, `/s/:sessionId` and `/i/:investigationId` are all the workspace, and
 * the workspace REWRITES ITS OWN ADDRESS: a session minted by the first
 * turn moves the bar from `/` to `/s/{id}`, and "New chat" moves it back.
 * Under Next that was a raw `history.replaceState` — no route transition,
 * no remount, the same component instance running throughout. Here it is a
 * router navigation, and a router navigation swaps the matched route's
 * element. Naming three routes with three different components would
 * therefore turn each of those address rewrites into an unmount and a
 * remount of the whole workspace — a new driver, a re-torn-down health
 * poll, a thread rebuilt underneath the analyst mid-conversation.
 *
 * With one component type at that position React reconciles instead of
 * remounting, and the rewrite is as invisible as `replaceState` was. The
 * segment is read inside `WorkspaceRoute`, which pins it at mount for the
 * same reason: under Next the page's `params` did not change when the URL
 * was rewritten, and they must not here either.
 */
export function AppRoutes() {
  return (
    <Routes>
      <Route element={<RootLayout />}>
        <Route path="/" element={<WorkspaceRoute />} />
        <Route path="/s/:sessionId" element={<WorkspaceRoute />} />
        <Route path="/i/:investigationId" element={<WorkspaceRoute />} />
        {/* Monitors is a different surface, so it is a different element
            and a real mount — which is what the arrival announcement and
            the focus move depend on. */}
        <Route path="/monitors" element={<MonitorsSurface />} />
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
