/**
 * The layout toggle's resolution rules.
 *
 * The one thing this must not get wrong is the default. It was `current`
 * while the A/B was under judgement — shipping a winner before the
 * judging is the one thing an A/B implementation must not do — and it is
 * `b` now that three reviewers have returned B_with_conditions
 * unanimously and every condition is built.
 */

import { beforeEach, describe, expect, it } from "vitest";

import {
  ANSWER_VARIANT_LABELS,
  ANSWER_VARIANT_STORAGE_KEY,
  currentAnswerVariant,
  DEFAULT_ANSWER_VARIANT,
  nextAnswerVariant,
  readAnswerVariant,
  resetAnswerVariantCache,
  resolveAnswerVariant,
  setAnswerVariant,
  TOGGLED_ANSWER_VARIANTS,
} from "@/lib/answerVariant";

beforeEach(() => {
  window.localStorage.clear();
  window.history.replaceState(null, "", "/");
  resetAnswerVariantCache();
});

describe("answer variant — what ships as the default", () => {
  it("is the CALM layout, with nothing set anywhere", () => {
    expect(DEFAULT_ANSWER_VARIANT).toBe("b");
    expect(resolveAnswerVariant(null, null)).toBe("b");
    expect(currentAnswerVariant()).toBe("b");
  });

  it("stays the default for a value that is not one of the three", () => {
    // `?variant=beta` is not variant B. Coercing it would send somebody
    // to a layout they did not ask for.
    expect(readAnswerVariant("beta")).toBeNull();
    expect(readAnswerVariant(42)).toBeNull();
    expect(resolveAnswerVariant("beta", "nonsense")).toBe("b");
  });

  it("keeps the retired layout reachable, and off the toggle", () => {
    // Kept in the code for one round: a layout deleted the same week its
    // replacement became the default leaves no way to check a regression
    // against what it replaced.
    expect(readAnswerVariant("current")).toBe("current");
    expect(resolveAnswerVariant("current", null)).toBe("current");
    expect(TOGGLED_ANSWER_VARIANTS).toEqual(["b", "a"]);
    expect(TOGGLED_ANSWER_VARIANTS).not.toContain("current");
  });
});

describe("answer variant — resolution order", () => {
  it("takes the URL parameter over the stored choice", () => {
    expect(resolveAnswerVariant("b", "a")).toBe("b");
  });

  it("falls back to the stored choice when the URL says nothing", () => {
    expect(resolveAnswerVariant(null, "a")).toBe("a");
    expect(resolveAnswerVariant("", "b")).toBe("b");
  });

  it("normalizes case and surrounding space", () => {
    expect(readAnswerVariant(" B ")).toBe("b");
  });

  it("reads a link's variant and makes it stick", () => {
    window.history.replaceState(null, "", "/s/sess_1?variant=a");
    expect(currentAnswerVariant()).toBe("a");
    // Sticky: somebody follows the link, clicks around, and stays on the
    // layout the link asked for.
    expect(window.localStorage.getItem(ANSWER_VARIANT_STORAGE_KEY)).toBe("a");
  });

  it("a link back to the default clears the stored choice", () => {
    window.localStorage.setItem(ANSWER_VARIANT_STORAGE_KEY, "a");
    window.history.replaceState(null, "", "/s/sess_1?variant=b");
    expect(currentAnswerVariant()).toBe("b");
    // Sticky in both directions: the default is stored as "nothing set",
    // so a browser sent back to it does not silently keep the old choice.
    expect(window.localStorage.getItem(ANSWER_VARIANT_STORAGE_KEY)).toBeNull();
  });

  it("reads the stored choice on a bare URL", () => {
    window.localStorage.setItem(ANSWER_VARIANT_STORAGE_KEY, "a");
    expect(currentAnswerVariant()).toBe("a");
  });
});

describe("answer variant — switching", () => {
  it("writes the choice and puts it in the address bar", () => {
    setAnswerVariant("a");
    expect(currentAnswerVariant()).toBe("a");
    expect(window.localStorage.getItem(ANSWER_VARIANT_STORAGE_KEY)).toBe("a");
    expect(window.location.search).toContain("variant=a");
  });

  it("clears both when it goes back to the default", () => {
    setAnswerVariant("a");
    setAnswerVariant("b");
    expect(currentAnswerVariant()).toBe("b");
    expect(window.localStorage.getItem(ANSWER_VARIANT_STORAGE_KEY)).toBeNull();
    expect(window.location.search).not.toContain("variant");
  });

  it("cycles between the two layouts the toggle offers", () => {
    expect(nextAnswerVariant("b")).toBe("a");
    expect(nextAnswerVariant("a")).toBe("b");
  });

  it("returns a browser sitting on the retired layout to the default", () => {
    expect(nextAnswerVariant("current")).toBe("b");
  });

  it("names each layout for the palette row", () => {
    expect(ANSWER_VARIANT_LABELS.b).toBe("Calm");
    expect(ANSWER_VARIANT_LABELS.a).toBe("Detailed");
    expect(ANSWER_VARIANT_LABELS.current).toBe("Legacy");
  });
});
