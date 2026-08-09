import { describe, expect, it } from "vitest";

import {
  comparisonChipLabel,
  deltaTone,
  formatCents,
  formatCompactCents,
  formatCount,
  formatPct,
  formatSignedCents,
  formatSignedPct,
  formatWindow,
  MINUS,
  mediumDate,
  parseIsoDate,
  relativeTime,
  shortDate,
  windowChipLabel,
} from "@/lib/format";

describe("formatCents", () => {
  it("formats positive cents as dollars", () => {
    expect(formatCents(132_844_152)).toBe("$1,328,441.52");
  });
  it("formats negative cents with a typographic minus", () => {
    expect(formatCents(-9_909_308)).toBe(`${MINUS}$99,093.08`);
  });
  it("formats zero", () => {
    expect(formatCents(0)).toBe("$0.00");
  });
  it("keeps exact cents (no float drift)", () => {
    expect(formatCents(1)).toBe("$0.01");
    expect(formatCents(-4_894_041)).toBe(`${MINUS}$48,940.41`);
  });
});

describe("formatSignedCents", () => {
  it("always signs nonzero values", () => {
    expect(formatSignedCents(2_017_693)).toBe("+$20,176.93");
    expect(formatSignedCents(-19_352_579)).toBe(`${MINUS}$193,525.79`);
  });
  it("leaves zero unsigned", () => {
    expect(formatSignedCents(0)).toBe("$0.00");
  });
});

describe("formatCompactCents", () => {
  it("compacts millions", () => {
    expect(formatCompactCents(132_844_152)).toBe("$1.33M");
  });
  it("compacts thousands", () => {
    expect(formatCompactCents(-9_909_308)).toBe(`${MINUS}$99.1K`);
  });
  it("keeps small values whole", () => {
    expect(formatCompactCents(41_218)).toBe("$412");
  });
  it("trims trailing zeros", () => {
    expect(formatCompactCents(150_000_000)).toBe("$1.5M");
  });
});

describe("percentages", () => {
  it("formats a fraction as percent", () => {
    expect(formatPct(0.239)).toBe("23.9%");
  });
  it("formats negative with minus", () => {
    expect(formatPct(-0.127155)).toBe(`${MINUS}12.7%`);
  });
  it("signs both directions when asked", () => {
    expect(formatSignedPct(0.348)).toBe("+34.8%");
    expect(formatSignedPct(-0.5293)).toBe(`${MINUS}52.9%`);
    expect(formatSignedPct(0)).toBe("0.0%");
  });
});

describe("deltaTone (direction-of-good semantics)", () => {
  it("falling cash is bad", () => {
    expect(deltaTone(-19_352_579, "up_is_good")).toBe("bad");
  });
  it("falling denial rate is GOOD even though it falls", () => {
    expect(deltaTone(-5, "down_is_good")).toBe("good");
  });
  it("rising denial dollars are bad", () => {
    expect(deltaTone(100, "down_is_good")).toBe("bad");
  });
  it("zero and neutral metrics are neutral", () => {
    expect(deltaTone(0, "up_is_good")).toBe("neutral");
    expect(deltaTone(42, "neutral")).toBe("neutral");
  });
});

describe("dates and windows", () => {
  it("parses ISO by hand (no TZ drift)", () => {
    expect(parseIsoDate("2026-07-27")).toEqual({ y: 2026, m: 7, d: 27 });
  });
  it("rejects non-ISO input", () => {
    expect(() => parseIsoDate("07/27/2026")).toThrow();
  });
  it("renders short and medium dates", () => {
    expect(shortDate("2026-08-02")).toBe("Aug 2");
    expect(mediumDate("2026-08-02")).toBe("Aug 2, 2026");
  });
  it("renders the full window with basis", () => {
    expect(
      formatWindow({ start: "2026-07-27", end: "2026-08-02", basis: "post" }),
    ).toBe("Jul 27 – Aug 2, 2026 (post date)");
  });
  it("renders cross-year windows with both years", () => {
    expect(
      formatWindow({ start: "2025-12-29", end: "2026-01-04", basis: "post" }),
    ).toBe("Dec 29, 2025 – Jan 4, 2026 (post date)");
  });
  it("renders the verbatim §10.3 header chip", () => {
    expect(
      windowChipLabel({ start: "2026-07-27", end: "2026-08-02", basis: "post" }),
    ).toBe("Jul 27–Aug 2 (post date)");
  });
  it("collapses same-month comparison chips", () => {
    expect(
      comparisonChipLabel({ start: "2026-07-20", end: "2026-07-26", basis: "post" }),
    ).toBe("vs Jul 20–26");
  });
  it("keeps cross-month comparison chips explicit", () => {
    expect(
      comparisonChipLabel({ start: "2026-01-01", end: "2026-03-31", basis: "remit" }),
    ).toBe("vs Jan 1–Mar 31");
  });
});

describe("formatCount", () => {
  it("groups thousands", () => {
    expect(formatCount(120_000)).toBe("120,000");
  });
});

describe("relativeTime", () => {
  const now = Date.parse("2026-08-08T12:00:00Z");

  it("reads the last minute as now", () => {
    expect(relativeTime("2026-08-08T11:59:30Z", now)).toBe("now");
  });
  it("counts minutes, then hours, then days", () => {
    expect(relativeTime("2026-08-08T11:12:00Z", now)).toBe("48m");
    expect(relativeTime("2026-08-08T04:00:00Z", now)).toBe("8h");
    expect(relativeTime("2026-08-05T12:00:00Z", now)).toBe("3d");
  });
  it("falls back to a calendar date past a week", () => {
    expect(relativeTime("2026-07-22T12:00:00Z", now)).toMatch(/^Jul \d{1,2}$/);
  });
  it("returns an unparseable timestamp verbatim rather than inventing an age", () => {
    expect(relativeTime("not-a-date", now)).toBe("not-a-date");
  });
});
