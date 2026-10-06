// Helpers for allocation weights entered as percentages.

export interface WeightRow {
  fundId: number | null;
  weightPct: string;
}

export interface WeightCheck {
  total: number;
  valid: boolean;
  rowErrors: (string | null)[];
}

const EPSILON = 0.01;

/** Validates rows the way the API does: distinct funds, positive weights, total 100%. */
export function checkWeights(rows: WeightRow[]): WeightCheck {
  const seen = new Set<number>();
  let total = 0;
  const rowErrors = rows.map((row) => {
    if (row.fundId === null) return "Choose a fund.";
    if (seen.has(row.fundId)) return "This fund is already in the portfolio.";
    seen.add(row.fundId);
    const weight = Number(row.weightPct);
    if (!Number.isFinite(weight) || weight <= 0 || weight > 100) return "Enter a weight between 0 and 100.";
    total += weight;
    return null;
  });
  total = Math.round(total * 100) / 100;
  const valid = rows.length > 0 && rowErrors.every((e) => e === null) && Math.abs(total - 100) <= EPSILON;
  return { total, valid, rowErrors };
}

/**
 * Converts optimiser fractions to percentages with two decimals that add up
 * to exactly 100.00: zero weights are dropped and the rounding remainder is
 * given to the largest position.
 */
export function fractionsToPercentages(fundIds: number[], weights: number[]): { fund_id: number; weight_pct: number }[] {
  const rows = fundIds
    .map((fundId, i) => ({ fund_id: fundId, weight_pct: Math.round(weights[i] * 10000) / 100 }))
    .filter((row) => row.weight_pct > 0);
  if (rows.length === 0) return rows;
  const total = rows.reduce((sum, row) => sum + row.weight_pct, 0);
  const remainder = Math.round((100 - total) * 100) / 100;
  const largest = rows.reduce((best, row, i) => (row.weight_pct > rows[best].weight_pct ? i : best), 0);
  rows[largest].weight_pct = Math.round((rows[largest].weight_pct + remainder) * 100) / 100;
  return rows;
}

export function toCsv(rows: (string | number)[][]): string {
  return rows
    .map((row) =>
      row
        .map((cell) => {
          const text = String(cell);
          return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
        })
        .join(","),
    )
    .join("\n");
}

export function downloadText(filename: string, content: string, type = "text/csv"): void {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}
