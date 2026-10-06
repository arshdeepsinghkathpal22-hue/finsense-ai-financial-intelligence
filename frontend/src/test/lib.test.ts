import { describe, expect, it } from "vitest";

import { shouldPollDocuments, splitCitations } from "../lib/citations";
import { crore, formatDate, formatMetric, inr, pct, ratio, tone } from "../lib/format";
import { checkWeights, fractionsToPercentages, toCsv } from "../lib/weights";

describe("formatting", () => {
  it("formats fractions as percentages", () => {
    expect(pct(0.1234)).toBe("12.34%");
    expect(pct(0.05, 1, true)).toBe("+5.0%");
    expect(pct(-0.031, 1, true)).toBe("-3.1%");
    expect(pct(null)).toBe("n/a");
    expect(pct(Number.NaN)).toBe("n/a");
  });
  it("formats money in Indian grouping", () => {
    expect(inr(123456)).toBe("₹1,23,456");
    expect(crore(12450.36)).toBe("₹12,450.36 cr");
    expect(inr(undefined)).toBe("n/a");
  });
  it("formats metrics by unit", () => {
    expect(formatMetric(0.25, "fraction/yr")).toBe("25.00%");
    expect(formatMetric(1.234, "ratio")).toBe("1.23");
    expect(formatMetric(null, "ratio")).toBe("n/a");
    expect(ratio(2)).toBe("2.00");
  });
  it("formats ISO dates without timezone drift", () => {
    expect(formatDate("2026-03-31")).toBe("31 Mar 2026");
    expect(formatDate(null)).toBe("n/a");
  });
  it("classifies gains and losses", () => {
    expect(tone(0.1)).toBe("gain");
    expect(tone(-0.1)).toBe("loss");
    expect(tone(null)).toBe("neutral");
  });
});

describe("weights", () => {
  it("accepts allocations that sum to 100", () => {
    const check = checkWeights([{ fundId: 1, weightPct: "60" }, { fundId: 2, weightPct: "40" }]);
    expect(check.valid).toBe(true);
    expect(check.total).toBe(100);
  });
  it("flags totals other than 100, duplicates and missing funds", () => {
    expect(checkWeights([{ fundId: 1, weightPct: "60" }, { fundId: 2, weightPct: "30" }]).valid).toBe(false);
    const dup = checkWeights([{ fundId: 1, weightPct: "50" }, { fundId: 1, weightPct: "50" }]);
    expect(dup.rowErrors[1]).toMatch(/already/);
    const missing = checkWeights([{ fundId: null, weightPct: "100" }]);
    expect(missing.rowErrors[0]).toMatch(/Choose/);
  });
  it("converts optimiser fractions to percentages summing to exactly 100", () => {
    const rows = fractionsToPercentages([1, 2, 3, 4], [0.333333, 0.333333, 0.333334, 0]);
    expect(rows).toHaveLength(3);
    const total = rows.reduce((s, r) => s + r.weight_pct, 0);
    expect(Math.round(total * 100) / 100).toBe(100);
    rows.forEach((r) => expect(Number.isInteger(Math.round(r.weight_pct * 100))).toBe(true));
  });
  it("quotes CSV cells that need it", () => {
    expect(toCsv([["a", "b,c"], ['say "hi"', 1]])).toBe('a,"b,c"\n"say ""hi""",1');
  });
});

describe("citations", () => {
  it("splits known markers and leaves unknown ones as text", () => {
    const parts = splitCitations("Fee is 0.25% [S1] and vol 14% [T1] but [S9] is unknown.", new Set(["S1", "T1"]));
    expect(parts.filter((p) => p.kind === "cite").map((p) => (p.kind === "cite" ? p.marker : ""))).toEqual(["S1", "T1"]);
    expect(parts.map((p) => (p.kind === "text" ? p.text : `<${p.marker}>`)).join("")).toBe(
      "Fee is 0.25% <S1> and vol 14% <T1> but [S9] is unknown.",
    );
  });
  it("polls documents only while indexing is in progress", () => {
    expect(shouldPollDocuments(["indexed", "processing"])).toBe(true);
    expect(shouldPollDocuments(["pending"])).toBe(true);
    expect(shouldPollDocuments(["indexed", "failed", "needs_ocr"])).toBe(false);
  });
});
