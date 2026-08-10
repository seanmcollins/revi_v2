/**
 * ASK, AND GO WHERE THE ANSWER WILL BE.
 *
 * A question can now be asked from three surfaces that do not render a
 * thread: Home, Monitors (a lead's drill), and the session rail's portfolio
 * cards. Every one of them used to call `store.submit()` and stay put — on
 * the old route table that was harmless, because `/` WAS the workspace and
 * the answer streamed in underneath. With Home at `/` it is not harmless:
 * the turn runs, the session is minted, and the analyst watches a landing
 * page while their answer arrives somewhere they cannot see.
 *
 * So this is the one place that knows what to do after a submission, and it
 * is deliberately a WRAPPER rather than a second submission path: the store
 * still owns single-flight, the pending-refinement queue, the settings
 * envelope and the session list refresh. All this adds is the navigation.
 *
 * THREE THINGS IT GETS RIGHT, each of which is a way this goes wrong:
 *
 *   IT WAITS FOR A SESSION TO EXIST. In api mode the session is minted by
 *     the first turn — `POST /v1/sessions` inside `ApiDriver.submit` — so
 *     the id is not knowable at the moment of the click. Navigating to
 *     `/s/undefined` or awaiting the whole 30-60s stream are both wrong;
 *     this arms and follows the store's own `sessionLive` flip, which is
 *     the exact instant the address becomes real.
 *   IT DOES NOT FOLLOW A SUBMISSION THAT WAS REFUSED. `submit()` returns
 *     early when a turn is already streaming or a session switch is in
 *     flight. `streamingTurnId` is set synchronously by an accepted
 *     submission, so a refused one arms nothing and the next unrelated
 *     session change does not yank the reader somewhere.
 *   IT NEVER PUSHES THE ADDRESS IT IS ALREADY AT. Asking a follow-up from
 *     the workspace at `/s/{id}` must not stack a second identical history
 *     entry that Back then has to walk through.
 */

import { useCallback, useEffect, useRef } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import type { TurnSubmission } from "@/lib/driver";
import { useSessionStore } from "@/lib/store";

/** The route a live session is read at. */
export function sessionPath(sessionId: string): string {
  return `/s/${encodeURIComponent(sessionId)}`;
}

export function useAsk(): (submission: TurnSubmission) => void {
  const navigate = useNavigate();
  // Read through a ref so the callbacks below never close over a stale
  // path: this hook's consumer may hold the returned function across many
  // renders, and the guard it needs is "where are we NOW". Written in an
  // effect rather than during render — a ref mutated mid-render is a value
  // React is entitled to throw away, and every reader here is an event
  // handler or an effect, both of which run after the commit that set it.
  const pathname = useLocation().pathname;
  const pathRef = useRef(pathname);
  useEffect(() => {
    pathRef.current = pathname;
  }, [pathname]);

  const sessionId = useSessionStore((s) => s.sessionId);
  const sessionLive = useSessionStore((s) => s.sessionLive);
  const following = useRef(false);

  const open = useCallback(
    (id: string) => {
      const path = sessionPath(id);
      if (pathRef.current !== path) navigate(path);
    },
    [navigate],
  );

  useEffect(() => {
    if (!following.current) return;
    if (!sessionLive || sessionId === "") return;
    following.current = false;
    open(sessionId);
  }, [sessionLive, sessionId, open]);

  return useCallback(
    (submission: TurnSubmission) => {
      void useSessionStore.getState().submit(submission);
      const after = useSessionStore.getState();
      // Refused (a turn already streaming, a switch in flight). The store
      // said no; there is nothing to follow.
      if (after.streamingTurnId === null) return;
      // Already in a session — the turn is a follow-up in it, and the
      // address is knowable immediately.
      if (after.sessionLive && after.sessionId !== "") {
        open(after.sessionId);
        return;
      }
      following.current = true;
    },
    [open],
  );
}
