/**
 * WATCHING A RUN, RESOLVING WHAT ONE WOULD DO, AND STARTING ONE.
 *
 * Deep research is the one thing in this product that takes about a minute
 * and keeps going whether or not anybody is looking at it. That shape is
 * what these hooks are for, and it is why none of them lives on the
 * session store: a run is not a conversation, it has no thread, no
 * referents and no pending refinements, and giving it a slice of the store
 * would put a background job inside the object that models "what the
 * analyst is reading right now".
 *
 * THE DRY RUN IS HERE FOR THE SAME REASON. `plan_only` resolves what a run
 * would look at without starting one, and it is a read about a run rather
 * than a fact about the conversation.
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
 * (`research_complete`, `research_cancelled`, `error`) or because the
 * server answered a request with it — the run's own GET, or the stop's own
 * response — and a stream that drops mid-run re-reads the run rather than
 * guessing.
 *
 * AND THERE IS ONE WRITE. `POST .../cancel` stops a run that is still
 * going. It belongs here rather than on a surface because a run is the one
 * thing in this product that spends real work while nobody watches: the
 * hook that owns the watching is the one that can offer to end it.
 */

import { useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import {
  ApiRequestError,
  cancelDeepResearch,
  fetchDeepResearchRun,
  fetchDeepResearchRuns,
  previewDeepResearch,
  startDeepResearch,
  streamDeepResearch,
} from "@/lib/apiDriver";
import { announce } from "@/lib/announce";
import {
  applyResearchFrame,
  hasFailed,
  initialWatchState,
  isRunning,
  researchPhaseFor,
  wasStopped,
  type ResearchPreview,
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
  /**
   * Stop the run. The server ends it between two readings and answers with
   * the run it stopped, which is what this puts on the surface — no
   * inference from a stream that went quiet, exactly as nothing here
   * infers completion.
   */
  stop: () => void;
  /** The stop is in flight — the control says so rather than repeating. */
  stopping: boolean;
  /**
   * Why the stop was refused, kept apart from `error` on purpose.
   *
   * `error` means the RUN could not be read and the surface has nothing to
   * draw. A stop that did not land leaves a perfectly readable run still
   * going, and folding the two together would replace it with an error
   * page over a run that is working.
   */
  stopError: string | null;
}

export function useDeepResearchRun(runId: string, enabled = true): ResearchRunView {
  const [state, setState] = useState<ResearchWatchState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [stopping, setStopping] = useState(false);
  const [stopError, setStopError] = useState<string | null>(null);
  /**
   * Single-flight, for the reason every write on this surface is: a second
   * press while the first is in flight is a second request about a run
   * that is already ending.
   */
  const stopInFlight = useRef(false);
  /**
   * The phase last spoken aloud.
   *
   * A run emits a progress frame per angle — eight of them inside a
   * second — and a live region that announced every one of those would be
   * a screen reader reading a counter. The reader is told when the WORK
   * CHANGES: reading, measuring, chasing, writing, done.
   *
   * Keyed on the ROW rather than the wire phase, because `orient`,
   * `consult` and `plan` are one row and announcing its sentence three
   * times is the counter this ref exists to prevent.
   */
  const spoken = useRef<string | null>(null);

  useEffect(() => {
    if (!enabled || runId === "") return;
    let cancelled = false;
    const controller = new AbortController();

    const speak = (next: ResearchWatchState): void => {
      const row = researchPhaseFor(next.run.progress.phase);
      const key = isRunning(next.run.status) ? row.id : next.run.status;
      if (spoken.current === key) return;
      spoken.current = key;
      if (next.run.status === "complete") {
        announce("The deep research report is ready.");
        return;
      }
      if (wasStopped(next.run.status)) {
        // Said once, in the server's own words, and never in the register
        // reserved for something going wrong.
        announce(next.run.error ?? "This run was stopped. Nothing was published.");
        return;
      }
      if (hasFailed(next.run.status)) {
        announce(next.run.error ?? "This run stopped before it could finish.");
        return;
      }
      announce(`Deep research: ${row.label.toLowerCase()}.`);
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

  /**
   * STOP IT — and say so from the server's answer, not from the silence.
   *
   * The POST comes back with the run in the state it was left in, so the
   * surface changes because the platform said it changed. The stream then
   * closes of its own accord and the effect re-reads the run, which agrees
   * — two honest accounts of one fact rather than a guess and a
   * correction.
   *
   * A stop that is refused leaves the run exactly as it was and puts the
   * server's own sentence where the reader is looking. It does not clear
   * the run: nothing about a failed stop makes the run unreadable.
   */
  const stop = useCallback(() => {
    if (runId === "" || stopInFlight.current) return;
    stopInFlight.current = true;
    setStopping(true);
    setStopError(null);
    void (async () => {
      try {
        const stopped = await cancelDeepResearch(runId, { onDrift });
        setState((prev) =>
          prev === null ? initialWatchState(stopped) : { ...prev, run: stopped },
        );
        announce("This run was stopped. Nothing was published.");
      } catch (caught) {
        setStopError(
          caught instanceof ApiRequestError
            ? caught.message
            : caught instanceof Error
              ? caught.message
              : "This run could not be stopped.",
        );
      } finally {
        stopInFlight.current = false;
        setStopping(false);
      }
    })();
  }, [runId]);

  return { state, loading, error, stop, stopping, stopError };
}

/* ------------------------------------------------------------------ */
/* Resolving what one WOULD do                                         */
/* ------------------------------------------------------------------ */

/**
 * What the selector's values are joined on to make an effect dependency.
 *
 * A character that cannot occur in a payer name. "Atlas Commercial" has a
 * space in it and "registration and eligibility" has three, so any
 * printable separator would split one value into two on the way back and
 * post a population nobody chose.
 */
const SEPARATOR = "\u0000";

/** The server's own sentence, or the plainest true thing about a drop. */
function refusalOf(caught: unknown): string {
  if (caught instanceof ApiRequestError) return caught.message;
  if (caught instanceof Error) return caught.message;
  return "Revi could not work out what this would look at.";
}

export interface ResearchPreviewView {
  /** What the run would do, once the server has said. */
  preview: ResearchPreview | null;
  /** The dry run is in flight — the control says so rather than repeating. */
  pending: boolean;
  /** The server's own refusal, when it refused. */
  error: string | null;
}

export interface ResearchPreviewRequest extends ResearchPreviewView {
  /**
   * Resolve what a run over this population, for this question, would do.
   *
   * Answers with the preview so the caller can act ON THE ANSWER —
   * opening a card, in the composer's case — rather than watching state
   * for it to appear. `null` means it did not resolve, and `error` says
   * why in the server's own words.
   */
  request: (population: ResearchSelector, question: string) => Promise<ResearchPreview | null>;
  /** Forget it — the reader closed the card or retyped the question. */
  reset: () => void;
}

/**
 * THE DRY RUN, ON A PRESS.
 *
 * For the composer, where the question is whatever is in the box at the
 * moment somebody asks for deep research. Nothing is started: this is one
 * `plan_only` POST, and what comes back is what the confirmation card
 * renders.
 *
 * Single-flight, for the same reason `useLaunchDeepResearch` is: a second
 * press while the first is in flight would resolve the same orientation
 * twice, and the orientation is real work against real data.
 */
export function useResearchPreview(): ResearchPreviewRequest {
  const [preview, setPreview] = useState<ResearchPreview | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inFlight = useRef(false);
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const request = useCallback(
    async (population: ResearchSelector, question: string): Promise<ResearchPreview | null> => {
      if (inFlight.current) return null;
      inFlight.current = true;
      setPending(true);
      setError(null);
      setPreview(null);
      try {
        const resolved = await previewDeepResearch(population, {
          onDrift,
          ...(question !== "" ? { question } : {}),
        });
        if (!alive.current) return null;
        setPreview(resolved);
        return resolved;
      } catch (caught) {
        if (alive.current) setError(refusalOf(caught));
        return null;
      } finally {
        inFlight.current = false;
        if (alive.current) setPending(false);
      }
    },
    [],
  );

  const reset = useCallback(() => {
    setPreview(null);
    setError(null);
  }, []);

  return { preview, pending, error, request, reset };
}

/**
 * THE DRY RUN, FOR A QUESTION THAT WAS ALREADY ASKED.
 *
 * For the answer path: "run deep research on X" comes back as an ordinary
 * answer carrying an offer, and the card under it resolves the same
 * preview the composer's control does so that both routes describe the
 * same run in the same words.
 *
 * The selector is taken apart into its three fields and rebuilt inside
 * the effect. That is not ceremony: it is what lets the effect depend on
 * the population's VALUE rather than on the identity of an object a
 * parent re-creates on every render, which would re-POST the dry run on
 * every render. A selector is those three fields and nothing else, so the
 * rebuild is byte-for-byte what arrived.
 *
 * ONE PIECE OF STATE, CARRYING THE QUESTION IT ANSWERS. "In flight" is
 * then a comparison rather than a flag — the question being asked is not
 * the question that was answered — which is both fewer moving parts and
 * the only version that cannot show a stale preview under a new question
 * for one frame.
 */
interface ResolvedPreview {
  /** The question this result is the answer to. */
  question: string;
  preview: ResearchPreview | null;
  error: string | null;
}

export function useResearchPreviewFor(
  population: ResearchSelector,
  question: string,
  enabled: boolean,
): ResearchPreviewView {
  const [resolved, setResolved] = useState<ResolvedPreview>({
    question: "",
    preview: null,
    error: null,
  });

  const kind = population.kind;
  const label = population.label;
  const values = (population.values ?? []).join(SEPARATOR);

  useEffect(() => {
    if (!enabled || question === "") return;
    let cancelled = false;
    const controller = new AbortController();
    void (async () => {
      try {
        const preview = await previewDeepResearch(
          { kind, label, values: values === "" ? [] : values.split(SEPARATOR) },
          { question, onDrift, signal: controller.signal },
        );
        if (!cancelled) setResolved({ question, preview, error: null });
      } catch (caught) {
        if (cancelled || controller.signal.aborted) return;
        setResolved({ question, preview: null, error: refusalOf(caught) });
      }
    })();

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [enabled, question, kind, label, values]);

  const settled = resolved.question === question;
  return {
    preview: settled ? resolved.preview : null,
    pending: enabled && question !== "" && !settled,
    error: settled ? resolved.error : null,
  };
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
