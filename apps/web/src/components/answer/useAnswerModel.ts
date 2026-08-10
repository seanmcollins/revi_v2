"use client";

import { useMemo } from "react";

import { selectPrimaryChart, selectRenderableCharts } from "@/lib/contract";
import { caveatLines } from "@/lib/export";
import { chartWindowLabel } from "@/lib/format";
import { tidyProse } from "@/lib/prose";
import type { TurnRecord } from "@/lib/store";
import type { ChartSpec, EvidenceGrade, Finding, WarningEvent } from "@/lib/types";
import { foldComposedDisclosures, partitionWarnings, publicWarningBody } from "@/lib/warnings";

/**
 * Everything three layouts of one answer have to agree about.
 *
 * The default layout, the refined one and the calm one differ in what is
 * on the first screen — not in what the payload says. Every derivation
 * that could drift between them lives here once: which warnings are the
 * verdict, which are the "things to know", how many checks ran, which
 * chart is the primary one, what the prose is after the banners have
 * taken their sentences back.
 *
 * That is a correctness requirement, not a tidiness one. The integrity
 * line prints a count of the cautions the sheet lists; if the line and
 * the sheet each partitioned the warnings themselves, the count would be
 * a claim about a list nobody checked against it.
 */
export interface AnswerModel {
  streaming: boolean;
  /** Charts worth drawing, in wire order (see `selectRenderableCharts`). */
  charts: ChartSpec[];
  /** The one chart the calm layout draws, when the turn earns one. */
  primaryChart?: ChartSpec;
  /** Every other renderable chart — hosted in the Evidence rail. */
  secondaryCharts: ChartSpec[];
  /** The worklist's intro sentence, lifted out of the warning list. */
  worklistIntro?: WarningEvent;
  /** The turn's warnings minus the sentence the worklist block opens with. */
  warnings: WarningEvent[];
  /** The answer to the question that was asked. Never collapsed. */
  verdicts: WarningEvent[];
  /**
   * The verdict sentences exactly as a surface renders them.
   *
   * A premise turn publishes its verdict twice — as the warning and as
   * F1's statement — and a layout that leads with one and lists the other
   * prints the same clause on one screen. See `echoesVerdict`.
   */
  verdictBodies: string[];
  /** Everything else — the "N things to know". */
  thingsToKnow: WarningEvent[];
  windowLabel?: string;
  comparisonWindows?: { current?: string; prior?: string };
  copyable: boolean;
  emptyResult: boolean;
  csvCaveats: string[];
  prose: { text: string; folded: number };
  measuredFindings: Finding[];
  boundedFindings: Finding[];
  /** How many data checks this turn ran — the integrity line's third count. */
  checks: number;
  /**
   * The calm layout is showing the facts on the answer itself.
   *
   * True when there is no written analysis to be the answer — a restored
   * turn on a payload generation that did not persist the composed prose
   * is findings and nothing else. Deferring those to the rail would leave
   * a context line over an integrity line and call it an answer.
   *
   * It lives on the model rather than inside the body because the RAIL
   * has to know too: facts inline AND facts in the rail is the same rows
   * printed twice on one screen, which is the repetition this whole
   * change exists to remove.
   */
  factsInline: boolean;
  completionMessage: string;
}

/**
 * What the integrity line's dot MEANS. Never decoration.
 *
 * The dot used to be `bg-verified` unconditionally, with the verified
 * halo, under whatever clause happened to be true — so "Answered without
 * reading your data" shipped under a green verified dot. A mark that says
 * the same thing about every answer says nothing about any of them, and
 * this one was saying the opposite of the sentence beside it.
 */
export type IntegrityTone =
  /** Read the warehouse, under a governed measure, at a certified grade. */
  | "verified"
  /** Read the warehouse, but under no governed contract. */
  | "measured"
  /** Read the warehouse, and the answer's own grade qualifies it. */
  | "qualified"
  /** Read nothing — cache, or a turn that never had a probe to run. */
  | "unread";

export interface VerificationClause {
  /** The sentence the line leads with. */
  text: string;
  /** What the dot says, and what its accessible name says. */
  tone: IntegrityTone;
  /**
   * The answer-level grade, said in words, when it is not `direct`.
   *
   * `answerGrade` had exactly one renderer — `AnswerTrustRow`'s badge —
   * and the calm layout never mounts it, so a Proxy- or Uncertified-graded
   * answer presented identically to a Direct one on the default surface.
   * The grade is not a badge the layouts may choose to carry; it is the
   * one thing on the line that says how far the number may be taken.
   */
  gradeNote?: { grade: EvidenceGrade; text: string };
}

/**
 * How a non-`direct` answer grade is said on a line of prose, in the same
 * words the badge's tooltip uses — a grade is a claim about the evidence,
 * so the phrasing does not get to soften between surfaces.
 */
const GRADE_CLAUSES: Readonly<Record<EvidenceGrade, string | undefined>> = {
  direct: undefined,
  derived: "Derived — calculated from validated fields",
  proxy: "Indicative — computed from a stand-in measure",
  discovery: "Uncertified — fields nobody has certified for this purpose",
  unavailable: "No adequate measurement exists for this",
};

/**
 * How the answer describes what it did, in one honest clause.
 *
 * "Verified against your data" is the sentence the integrity line was
 * designed around, and it is only true of a turn that actually read the
 * warehouse under a governed contract. Three other things happen — a turn
 * that read data under no governed measure, a turn answered entirely from
 * this session's cache, and a turn (META, definitional) that never had a
 * probe to run — and each gets its own clause rather than borrowing a
 * verification it did not earn.
 *
 * And across all four, the ANSWER GRADE rides along: a turn that read the
 * warehouse under a governed measure whose evidence is a proxy is still
 * not "verified" in the sense a reader will take that word, so the grade
 * qualifies the clause rather than living in a badge the calm layout does
 * not render.
 */
export function verificationClause(answer: TurnRecord["answer"]): VerificationClause {
  const checks = answer.evidence?.probes.length ?? 0;
  const governed = (answer.metric?.metrics.length ?? 0) > 0 || answer.metric?.primary !== undefined;
  const grade = answer.answerGrade;
  const gradeText = grade ? GRADE_CLAUSES[grade] : undefined;
  const gradeNote = grade && gradeText ? { grade, text: gradeText } : undefined;
  // `derived` is a certified path — deterministic arithmetic over
  // validated fields — so it is stated without downgrading the dot.
  // `proxy`, `discovery` and `unavailable` change what the number may be
  // used for, and the mark says so.
  const qualified = grade !== undefined && grade !== "direct" && grade !== "derived";

  if (checks === 0) {
    return {
      text:
        (answer.evidence?.cacheHits ?? 0) > 0
          ? "Answered from checks already run in this session"
          : "Answered without reading your data",
      tone: "unread",
      ...(gradeNote ? { gradeNote } : {}),
    };
  }
  return {
    text: governed ? "Verified against your data" : "Computed from your data",
    tone: qualified ? "qualified" : governed ? "verified" : "measured",
    ...(gradeNote ? { gradeNote } : {}),
  };
}

export function useAnswerModel(turn: TurnRecord): AnswerModel {
  const a = turn.answer;
  const streaming = a.status === "streaming";

  // A comparison turn publishes the same measure twice (`main` and
  // `main__compare`, byte-identical rows) and single-row frames for
  // scalars. Both were being drawn: two identical charts stacked, and a
  // "trend" through one point. See `selectRenderableCharts`.
  const charts = useMemo(() => selectRenderableCharts(a.charts), [a.charts]);
  // The findings decide which figure leads when the engine named no `main`
  // frame — see `selectPrimaryChart`. Passed here rather than resolved
  // inside the selector so the one figure on screen and the facts behind
  // it come from the same payload this model already holds.
  const primaryChart = useMemo(() => selectPrimaryChart(charts, a.findings), [charts, a.findings]);
  const secondaryCharts = useMemo(
    () => charts.filter((chart) => chart.id !== primaryChart?.id),
    [charts, primaryChart],
  );

  /**
   * The worklist's intro line, lifted out of the turn's warnings.
   *
   * `WORKLIST_ATTACHED` is the sentence that says the ranked cards below
   * are the detection feed's work and NOT findings this turn computed.
   * Left in the general warning list it would sit above the findings and
   * far from the cards it is about. It is moved, not dropped: it opens
   * the worklist block and comes out of the list below, so the same
   * sentence is never printed twice.
   */
  const worklistIntro = a.worklist
    ? a.warnings.find((w) => w.code === "WORKLIST_ATTACHED")
    : undefined;
  const warnings = useMemo(
    () => (worklistIntro === undefined ? a.warnings : a.warnings.filter((w) => w !== worklistIntro)),
    [a.warnings, worklistIntro],
  );
  const { verdicts, rest: thingsToKnow } = useMemo(
    () => partitionWarnings(warnings),
    [warnings],
  );
  const verdictBodies = useMemo(
    () => verdicts.map((w) => publicWarningBody(w.code, w.message).text),
    [verdicts],
  );

  // A chart read on its own — screenshotted into a deck, scrolled past
  // the header — otherwise carries no period at all. On a SNAPSHOT
  // contract the period is a moment, not a range, and the payload's
  // window is not what was measured.
  const windowLabel = a.header
    ? a.header.asOf
      ? `as of ${a.header.asOf}`
      : chartWindowLabel(a.header.window)
    : undefined;
  const comparisonWindows =
    a.header && !a.header.asOf && a.header.comparison
      ? {
          current: chartWindowLabel(a.header.window),
          prior: a.header.comparison.label ?? chartWindowLabel(a.header.comparison.window),
        }
      : undefined;

  // Worth offering only once the turn has something to take away.
  const copyable =
    !streaming &&
    a.status === "complete" &&
    (a.findings.length > 0 || a.narrative.trim() !== "");

  const emptyResult =
    !streaming &&
    a.status === "complete" &&
    a.findings.length === 0 &&
    !a.definition &&
    !a.clarification &&
    !a.error &&
    a.narrative.trim() === "";

  // The same caveats the copied text prints, handed to every chart CSV on
  // this turn — one answer, one set of caveats, whichever button is used.
  const csvCaveats = useMemo(() => caveatLines(a.warnings), [a.warnings]);

  // The composer builds its mandatory disclosures into the prose verbatim
  // and this card independently renders the same `warnings_v2`. The
  // banner is kept as the structured surface and the prose defers to it —
  // warnings survive a reload and composed prose does not.
  // The doubled stop is repaired AFTER the fold, never before it: the
  // fold matches composed sentences against the warnings they duplicate,
  // and repairing punctuation first would change the strings being
  // compared and silently stop folding the pair it was written for.
  const prose = useMemo(() => {
    const folded = foldComposedDisclosures(a.narrative, warnings);
    return { ...folded, text: tidyProse(folded.text) };
  }, [a.narrative, warnings]);

  // Ceilings are separated from measurements, and the separation is
  // stated. A stable partition: within each block the engine's order
  // stands.
  const measuredFindings = useMemo(
    () => a.findings.filter((f) => f.measured?.isBound !== true),
    [a.findings],
  );
  const boundedFindings = useMemo(
    () => a.findings.filter((f) => f.measured?.isBound === true),
    [a.findings],
  );

  const checks = a.evidence?.probes.length ?? 0;
  const factsInline =
    !streaming && prose.text.trim() === "" && a.findings.length > 0;

  /**
   * What a screen reader is told, and when (WCAG 2.2 SC 4.1.3).
   *
   * Terse on purpose. Piping the narrative through a live region would
   * read a thousand words aloud, interrupting itself on every delta; what
   * a non-sighted reader needs is that the answer arrived and how much of
   * it there is, then their own cursor.
   */
  const completionMessage = useMemo(() => {
    if (streaming) return "";
    if (a.status === "error") return "This turn stopped before it finished.";
    if (a.status === "clarification")
      return "The platform needs one more detail before it answers.";
    if (a.status !== "complete") return "";
    const cautions = a.warnings.filter((w) => w.severity === "caution").length;
    const parts = [
      `${a.findings.length} finding${a.findings.length === 1 ? "" : "s"}`,
      ...(charts.length > 0 ? [`${charts.length} chart${charts.length === 1 ? "" : "s"}`] : []),
      `${cautions} caution${cautions === 1 ? "" : "s"}`,
    ];
    return `Answer ready: ${parts.join(", ")}.`;
  }, [streaming, a.status, a.findings.length, a.warnings, charts.length]);

  return {
    streaming,
    charts,
    ...(primaryChart ? { primaryChart } : {}),
    secondaryCharts,
    ...(worklistIntro ? { worklistIntro } : {}),
    warnings,
    verdicts,
    verdictBodies,
    thingsToKnow,
    ...(windowLabel !== undefined ? { windowLabel } : {}),
    ...(comparisonWindows ? { comparisonWindows } : {}),
    copyable,
    emptyResult,
    csvCaveats,
    prose,
    measuredFindings,
    boundedFindings,
    checks,
    factsInline,
    completionMessage,
  };
}
