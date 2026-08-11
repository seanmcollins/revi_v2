/**
 * WATCHING A RUN, AND STARTING ONE.
 *
 * Deep research is the one thing in this product that takes about a minute
 * and keeps going whether or not anybody is looking at it. That shape is
 * what these two hooks are for, and it is why neither of them lives on the
 * session store: a run is not a conversation, it has no thread, no
 * referents and no pending refinements, and giving it a slice of the store
 * would put a background job inside the object that models "what the
 * analyst is reading right now".
 *
 * THE READ IS TWO REQUESTS AND THEY DO NOT COMPETE.
 *
 *   `GET /v1/deep-research/{id}` answers immediately with wherever the run
 *     has got to. It is the whole surface for a FINISHED run, which is what
 *     a permalink opened tomorrow morning is.
 *   `GET .../stream` is opened only while there is something left to
 *     watch. The server catches a late watcher up with the frames already
 *     emitted, so attaching thirty seconds in misses nothing — and a run
 *     that finished before the stream opened never opens one at all.
 *
 * NOTHING HERE INFERS COMPLETION. A closed stream is not a finished run: it
 * is a closed stream. The status only ever changes because a frame said so
 * (`research_complete`, `error`) or because the run's own GET said so, and
 * a stream that drops mid-run re-reads the run rather than guessing.
 */

import { useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import {
  ApiRequestError,
  fetchDeepResearchRun,
  fetchDeepResearchRuns,
  startDeepResearch,
  streamDeepResearch,
} from "@/lib/apiDriver";
import { announce } from "@/lib/announce";
import {
  applyResearchFrame,
  initialWatchState,
  isRunning,
  RESEARCH_PHASES,
  type ResearchSelector,
  type ResearchSummary,
  type ResearchWatchState,
} from "@/lib/deepResearch";
import { researchPath } from "@/lib/links";
import { useSessionStore } from "@/lib/store";

const onDrift = (paths: string[]): void =>
  useSessionStore.getState().reportContractDrift(paths);

/* ------------------------------------------------------------------ */
/* Watching one run                                                    */
/* ------------------------------------------------------------------ */

export interface ResearchRunView {
  state: ResearchWatchState | null;
  /** The first read has not answered yet — there is nothing to render. */
  loading: boolean;
  /** Why the run could not be read, in the server's own words. */
  error: string | null;
}

export function useDeepResearchRun(runId: string, enabled = true): ResearchRunView {
  const [state, setState] = useState<ResearchWatchState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  /**
   * The phase last spoken aloud.
   *
   * A run emits a progress frame per angle — eight of them inside a
   * second — and a live region that announced every one of those would be
   * a screen reader reading a counter. The reader is told when the WORK
   * CHANGES: reading, measuring, writing, done.
   */
  const spoken = useRef<string | null>(null);

  useEffect(() => {
    if (!enabled || runId === "") return;
    let cancelled = false;
    const controller = new AbortController();

    const speak = (next: ResearchWatchState): void => {
      const key = isRunning(next.run.status) ? next.run.progress.phase : next.run.status;
      if (spoken.current === key) return;
      spoken.current = key;
      if (next.run.status === "complete") {
        announce("The deep research report is ready.");
        return;
      }
      if (next.run.status === "failed" || next.run.status === "interrupted") {
        announce(next.run.error ?? "This run stopped before it could finish.");
        return;
      }
      const phase = RESEARCH_PHASES.find((p) => p.id === next.run.progress.phase);
      if (phase) announce(`Deep research: ${phase.label.toLowerCase()}.`);
    };

    const apply = (updater: (prev: ResearchWatchState) => ResearchWatchState): void => {
      if (cancelled) return;
      setState((prev) => {
        if (prev === null) return prev;
        const next = updater(prev);
        speak(next);
        return next;
      });
    };

    void (async () => {
      try {
        const run = await fetchDeepResearchRun(runId, { onDrift, signal: controller.signal });
        if (cancelled) return;
        const opening = initialWatchState(run);
        setState(opening);
        setLoading(false);
        speak(opening);
        if (!isRunning(run.status)) return;
        // Only a run with something left to do opens a stream.
        await streamDeepResearch(
          runId,
          (frame) => apply((prev) => applyResearchFrame(prev, frame)),
          { signal: controller.signal },
        );
        if (cancelled) return;
        // THE STREAM ENDED. That is not a verdict — re-read the run and
        // let the server say what happened. A watcher that concluded
        // "finished" here would publish a report nobody composed.
        const settled = await fetchDeepResearchRun(runId, {
          onDrift,
          signal: controller.signal,
        });
        if (cancelled) return;
        setState((prev) =>
          prev === null
            ? initialWatchState(settled)
            : { ...prev, run: settled, ...(settled.report ? { plan: settled.report.plan } : {}) },
        );
      } catch (caught) {
        if (cancelled || controller.signal.aborted) return;
        setLoading(false);
        setError(
          caught instanceof ApiRequestError
            ? caught.message
            : caught instanceof Error
              ? caught.message
              : "This run could not be read.",
        );
      }
    })();

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [runId, enabled]);

  return { state, loading, error };
}

/* ------------------------------------------------------------------ */
/* Starting one                                                        */
/* ------------------------------------------------------------------ */

export interface LaunchResearch {
  /** Start a run over this population and go to its surface. */
  launch: (population: ResearchSelector, question?: string) => void;
  /** A launch is in flight — the control says so rather than repeating. */
  pending: boolean;
  /** The server's own refusal, when it refused. */
  error: string | null;
}

/**
 * LAUNCH, THEN GO WHERE THE REPORT WILL BE.
 *
 * The same discipline `useAsk` follows for a question, for the same reason:
 * the surfaces that offer a run — a lead card, an answer, Home's
 * still-catchable figure — do not render one, so a launch that stayed put
 * would leave the reader on a page while their report was written
 * somewhere they cannot see. The POST returns the run's id immediately, so
 * unlike a turn there is nothing to wait for: the address is knowable at
 * the moment the server accepts.
 *
 * Single-flight per hook instance. A second click while the first POST is
 * in flight would start a second real run — a second model call, a second
 * minute — over the same population.
 */
export function useLaunchDeepResearch(): LaunchResearch {
  const navigate = useNavigate();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inFlight = useRef(false);

  const launch = useCallback(
    (population: ResearchSelector, question?: string) => {
      if (inFlight.current) return;
      inFlight.current = true;
      setPending(true);
      setError(null);
      void (async () => {
        try {
          const run = await startDeepResearch(population, {
            onDrift,
            ...(question ? { question } : {}),
          });
          navigate(researchPath(run.id));
        } catch (caught) {
          setError(
            caught instanceof ApiRequestError
              ? caught.message
              : caught instanceof Error
                ? caught.message
                : "This run could not be started.",
          );
        } finally {
          inFlight.current = false;
          setPending(false);
        }
      })();
    },
    [navigate],
  );

  return { launch, pending, error };
}

/* ------------------------------------------------------------------ */
/* The tenant's runs                                                   */
/* ------------------------------------------------------------------ */

/**
 * `GET /v1/deep-research` — every run this tenant has, newest first.
 *
 * The session rail reads it for one purpose: a run persists as an
 * investigation inside its own session, so the rail already lists it as a
 * session row. Without this list that row opens the report as an ordinary
 * restored answer; with it, the row knows it is a run and opens the run's
 * own surface. The list is the only thing on the wire that maps a session
 * back to the run inside it.
 */
export function useDeepResearchRuns(enabled: boolean) {
  return useQuery<ResearchSummary[]>({
    queryKey: ["deep-research", "runs"],
    queryFn: () => fetchDeepResearchRuns({ onDrift }),
    enabled,
    staleTime: 60_000,
    retry: 1,
  });
}
