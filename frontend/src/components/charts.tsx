import type { ReactNode } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { Point } from "../api/types";
import { formatDate } from "../lib/format";

export const palette = {
  ledger: "#5fbf8f",
  info: "#7fa8d9",
  caution: "#e2b45a",
  loss: "#e07a6b",
  muted: "#9fb0c6",
  grid: "#24405e",
  series: ["#5fbf8f", "#7fa8d9", "#e2b45a", "#c79be0", "#e07a6b", "#9fd0c4"],
};

export const axisProps = {
  stroke: palette.muted,
  tick: { fill: palette.muted, fontSize: 12 },
  tickLine: false,
};

export const tooltipStyle = {
  contentStyle: { background: "#13263b", border: "1px solid #36577a", borderRadius: 6, color: "#e7ecf3" },
  labelStyle: { color: "#9fb0c6" },
};

export function ChartFrame({
  title,
  caption,
  children,
  height = 280,
  badge,
}: {
  title: string;
  caption?: string;
  children: ReactNode;
  height?: number;
  badge?: ReactNode;
}) {
  return (
    <figure className="panel p-4" aria-label={title}>
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <h3>{title}</h3>
        {badge}
      </div>
      <div style={{ height }} className="w-full">
        {children}
      </div>
      {caption && <figcaption className="mt-2 text-xs text-faint">{caption}</figcaption>}
    </figure>
  );
}

const shortDate = (value: string) => {
  const d = formatDate(value);
  return d.split(" ").slice(1).join(" ");
};

/** Merges several {date, value} series into rows keyed by date for Recharts. */
export function mergeSeries(series: Record<string, Point[] | undefined>): Record<string, number | string>[] {
  const rows = new Map<string, Record<string, number | string>>();
  for (const [key, points] of Object.entries(series)) {
    for (const point of points ?? []) {
      const row = rows.get(point.date) ?? { date: point.date };
      row[key] = point.value;
      rows.set(point.date, row);
    }
  }
  return [...rows.values()].sort((a, b) => String(a.date).localeCompare(String(b.date)));
}

export function TimeSeriesChart({
  data,
  lines,
  yFormat,
  yLabel,
}: {
  data: object[];
  lines: { key: string; name: string; color?: string; dashed?: boolean }[];
  yFormat?: (value: number) => string;
  yLabel?: string;
}) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <LineChart data={data} margin={{ top: 5, right: 12, bottom: 0, left: 4 }}>
        <CartesianGrid stroke={palette.grid} strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey="date" {...axisProps} tickFormatter={shortDate} minTickGap={40} />
        <YAxis
          {...axisProps}
          width={64}
          tickFormatter={yFormat}
          domain={["auto", "auto"]}
          label={yLabel ? { value: yLabel, angle: -90, position: "insideLeft", fill: palette.muted, fontSize: 12 } : undefined}
        />
        <Tooltip
          {...tooltipStyle}
          labelFormatter={(label) => formatDate(String(label))}
          formatter={(value) => (yFormat && typeof value === "number" ? yFormat(value) : String(value))}
        />
        {lines.length > 1 && <Legend wrapperStyle={{ color: palette.muted, fontSize: 12 }} />}
        {lines.map((line, i) => (
          <Line
            key={line.key}
            type="monotone"
            dataKey={line.key}
            name={line.name}
            stroke={line.color ?? palette.series[i % palette.series.length]}
            strokeDasharray={line.dashed ? "5 4" : undefined}
            dot={false}
            strokeWidth={1.6}
            isAnimationActive={false}
            connectNulls
          />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}

export function DrawdownChart({ points }: { points: Point[] }) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <AreaChart data={points} margin={{ top: 5, right: 12, bottom: 0, left: 4 }}>
        <CartesianGrid stroke={palette.grid} strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey="date" {...axisProps} tickFormatter={shortDate} minTickGap={40} />
        <YAxis {...axisProps} width={56} tickFormatter={(v: number) => `${(v * 100).toFixed(0)}%`} />
        <Tooltip
          {...tooltipStyle}
          labelFormatter={(label) => formatDate(String(label))}
          formatter={(value) => [`${(Number(value) * 100).toFixed(2)}%`, "Drawdown"]}
        />
        <Area type="monotone" dataKey="value" stroke={palette.loss} fill={palette.loss} fillOpacity={0.18} isAnimationActive={false} />
      </AreaChart>
    </ResponsiveContainer>
  );
}

export const pctTick = (value: number) => `${(value * 100).toFixed(0)}%`;
export const pctTick1 = (value: number) => `${(value * 100).toFixed(1)}%`;
