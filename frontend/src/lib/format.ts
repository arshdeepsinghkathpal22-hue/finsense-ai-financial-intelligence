// Number and date formatting. The API returns returns/volatility/VaR as
// fractions (0.12 = 12%); these helpers are the only place that converts.

const inrFormatter = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 });
const groupFormatter = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2, minimumFractionDigits: 2 });

export const NA = "n/a";

function isNumber(value: number | null | undefined): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

export function pct(value: number | null | undefined, digits = 2, signed = false): string {
  if (!isNumber(value)) return NA;
  const text = `${(value * 100).toFixed(digits)}%`;
  return signed && value > 0 ? `+${text}` : text;
}

export function ratio(value: number | null | undefined, digits = 2): string {
  return isNumber(value) ? value.toFixed(digits) : NA;
}

export function inr(value: number | null | undefined): string {
  return isNumber(value) ? inrFormatter.format(value) : NA;
}

export function crore(value: number | null | undefined): string {
  return isNumber(value) ? `₹${groupFormatter.format(value)} cr` : NA;
}

export function nav(value: number | null | undefined): string {
  return isNumber(value) ? `₹${value.toFixed(4)}` : NA;
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return NA;
  const [year, month, day] = value.slice(0, 10).split("-").map(Number);
  if (!year || !month || !day) return value;
  return new Date(Date.UTC(year, month - 1, day)).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  });
}

/** Formats a metric according to the unit string the API sends with it. */
export function formatMetric(value: number | null | undefined, unit: string): string {
  if (unit.startsWith("fraction")) return pct(value);
  if (unit === "ratio") return ratio(value);
  if (!isNumber(value)) return NA;
  return Number.isInteger(value) ? value.toLocaleString("en-IN") : value.toFixed(2);
}

export function tone(value: number | null | undefined): "gain" | "loss" | "neutral" {
  if (!isNumber(value) || value === 0) return "neutral";
  return value > 0 ? "gain" : "loss";
}
