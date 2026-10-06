import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from "recharts";

import type { OptimisationResult, Period, RiskProfile } from "../api/types";
import { pct, ratio } from "../lib/format";
import { downloadText, fractionsToPercentages, toCsv } from "../lib/weights";
import { ChartFrame, axisProps, palette, pctTick, pctTick1, tooltipStyle } from "./charts";
import { Button, Card, Disclaimer, ErrorState, Field, KindLabel, Notice } from "./ui";

export interface OptimiserRequest {
  objective: "mean_variance" | "min_variance" | "max_sharpe";
  risk_profile: RiskProfile;
  min_weight: number;
  max_weight: number;
  lookback: Period;
  risk_free_rate?: number;
}

export function OptimiserPanel({
  run,
  onApply,
}: {
  run: (request: OptimiserRequest) => Promise<OptimisationResult>;
  onApply?: (assets: { fund_id: number; weight_pct: number }[]) => Promise<unknown>;
}) {
  const [objective, setObjective] = useState<OptimiserRequest["objective"]>("mean_variance");
  const [profile, setProfile] = useState<RiskProfile>("moderate");
  const [minW, setMinW] = useState("0");
  const [maxW, setMaxW] = useState("60");
  const [lookback, setLookback] = useState<Period>("3y");
  const mutation = useMutation({ mutationFn: run });
  const apply = useMutation({ mutationFn: (assets: { fund_id: number; weight_pct: number }[]) => onApply!(assets) });
  const result = mutation.data;

  const submit = () =>
    mutation.mutate({
      objective,
      risk_profile: profile,
      min_weight: Number(minW) / 100,
      max_weight: Number(maxW) / 100,
      lookback,
    });

  return (
    <Card title="Optimise allocation" subtitle="Markowitz mean-variance optimisation, long-only, weights sum to 100%.">
      <div className="grid gap-3 sm:grid-cols-5">
        <Field label="Objective">
          {(id) => (
            <select id={id} className="field-input" value={objective} onChange={(e) => setObjective(e.target.value as OptimiserRequest["objective"])}>
              <option value="mean_variance">Return vs risk (utility)</option>
              <option value="min_variance">Minimum variance</option>
              <option value="max_sharpe">Maximum Sharpe ratio</option>
            </select>
          )}
        </Field>
        <Field label="Risk preference" hint={objective === "mean_variance" ? "Sets the risk-aversion λ" : "Used only by the utility objective"}>
          {(id) => (
            <select id={id} className="field-input" value={profile} onChange={(e) => setProfile(e.target.value as RiskProfile)} disabled={objective !== "mean_variance"}>
              <option value="conservative">Conservative (λ = 8)</option>
              <option value="moderate">Moderate (λ = 4)</option>
              <option value="aggressive">Aggressive (λ = 1.5)</option>
            </select>
          )}
        </Field>
        <Field label="Minimum weight %">{(id) => <input id={id} className="field-input" inputMode="decimal" value={minW} onChange={(e) => setMinW(e.target.value)} />}</Field>
        <Field label="Maximum weight %">{(id) => <input id={id} className="field-input" inputMode="decimal" value={maxW} onChange={(e) => setMaxW(e.target.value)} />}</Field>
        <Field label="Estimation window">
          {(id) => (
            <select id={id} className="field-input" value={lookback} onChange={(e) => setLookback(e.target.value as Period)}>
              <option value="1y">1 year</option><option value="3y">3 years</option><option value="5y">5 years</option><option value="max">Full history</option>
            </select>
          )}
        </Field>
      </div>
      <Button className="mt-4" busy={mutation.isPending} onClick={submit}>Run optimisation</Button>
      <p className="mt-2 text-xs text-faint">Objective for "Return vs risk": maximise w′μ − (λ/2)·w′Σw, using historical annualised mean returns μ and covariance Σ.</p>

      {mutation.error && <div className="mt-4"><ErrorState error={mutation.error} /></div>}
      {result && <OptimisationView result={result} onApply={onApply ? (a) => apply.mutate(a) : undefined} applying={apply.isPending} applied={apply.isSuccess} applyError={apply.error} />}
    </Card>
  );
}

function OptimisationView({
  result,
  onApply,
  applying,
  applied,
  applyError,
}: {
  result: OptimisationResult;
  onApply?: (assets: { fund_id: number; weight_pct: number }[]) => void;
  applying: boolean;
  applied: boolean;
  applyError: unknown;
}) {
  const { comparison, assets } = result;
  const rows = assets.map((a, i) => ({
    name: a.scheme_code,
    current: comparison.current?.weights[i],
    optimised: comparison.optimised.weights[i],
    equal: comparison.equal_weight.weights[i],
  }));
  const columns = [
    ...(comparison.current ? [{ key: "current" as const, label: "Current" }] : []),
    { key: "optimised" as const, label: "Optimised" },
    { key: "equal_weight" as const, label: "Equal weight" },
  ];
  const points = (key: "current" | "optimised" | "equal_weight") => {
    const stats = comparison[key];
    return stats ? [{ x: stats.volatility, y: stats.expected_return }] : [];
  };
  const download = () => {
    const header = ["scheme_code", "fund_name", "expected_return_pct", "volatility_pct", ...columns.map((c) => `${c.key}_weight_pct`)];
    const body = assets.map((a, i) => [
      a.scheme_code, a.name, (a.expected_return * 100).toFixed(2), (a.volatility * 100).toFixed(2),
      ...columns.map((c) => ((comparison[c.key]?.weights[i] ?? 0) * 100).toFixed(2)),
    ]);
    const footer = [[`# ${result.objective}; window ${result.estimation.start} to ${result.estimation.end}; historical estimates, not a forecast`]];
    downloadText("finsense-optimised-allocation.csv", toCsv([header, ...body, [], ...footer]));
  };

  return (
    <div className="mt-6 space-y-5">
      <div className="flex flex-wrap items-center gap-2">
        <KindLabel kind="historical" />
        <span className="text-sm text-muted">
          Estimated on {result.estimation.observations} daily returns, {result.estimation.start} to {result.estimation.end}; solver {result.solver.method}, {result.solver.iterations} iterations.
        </span>
      </div>
      {result.estimation.notes.map((note) => <Notice key={note} tone="caution">{note}</Notice>)}

      <div className="overflow-x-auto">
        <table className="table-base">
          <thead><tr><th>Allocation</th><th className="text-right">Expected return</th><th className="text-right">Volatility</th><th className="text-right">Sharpe</th><th className="text-right">HHI</th></tr></thead>
          <tbody>
            {columns.map((c) => {
              const s = comparison[c.key]!;
              return (
                <tr key={c.key}>
                  <td>{c.label}</td>
                  <td className="num text-right">{pct(s.expected_return)}</td>
                  <td className="num text-right">{pct(s.volatility)}</td>
                  <td className="num text-right">{ratio(s.sharpe)}</td>
                  <td className="num text-right">{ratio(s.herfindahl_index, 3)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <p className="mt-1 text-xs text-faint">Annualised historical estimates; Sharpe uses a risk-free rate of {pct(result.estimation.risk_free_rate)}.</p>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <ChartFrame title="Weights before and after" caption="Optimised weights within the chosen limits.">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={rows}>
              <CartesianGrid stroke={palette.grid} strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="name" {...axisProps} />
              <YAxis {...axisProps} tickFormatter={pctTick} width={48} />
              <Tooltip {...tooltipStyle} formatter={(v, n) => [pct(Number(v), 1), String(n)]} />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              {comparison.current && <Bar dataKey="current" name="Current" fill={palette.muted} isAnimationActive={false} />}
              <Bar dataKey="optimised" name="Optimised" fill={palette.ledger} isAnimationActive={false} />
              <Bar dataKey="equal" name="Equal weight" fill={palette.info} isAnimationActive={false} />
            </BarChart>
          </ResponsiveContainer>
        </ChartFrame>
        <ChartFrame title="Efficient frontier" caption={result.frontier.length ? "Minimum-variance portfolios for each target return, under the same limits." : "The frontier could not be traced for these limits."}>
          <ResponsiveContainer width="100%" height="100%">
            <ScatterChart margin={{ left: 4, right: 12 }}>
              <CartesianGrid stroke={palette.grid} strokeDasharray="3 3" />
              <XAxis type="number" dataKey="x" name="Volatility" {...axisProps} tickFormatter={pctTick1} domain={["auto", "auto"]} />
              <YAxis type="number" dataKey="y" name="Expected return" {...axisProps} tickFormatter={pctTick1} width={56} domain={["auto", "auto"]} />
              <ZAxis range={[40, 40]} />
              <Tooltip {...tooltipStyle} formatter={(v, n) => [pct(Number(v)), String(n)]} />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Scatter name="Frontier" data={result.frontier.map((p) => ({ x: p.volatility, y: p.expected_return }))} fill={palette.muted} line isAnimationActive={false} />
              {comparison.current && <Scatter name="Current" data={points("current")} fill={palette.caution} isAnimationActive={false} />}
              <Scatter name="Optimised" data={points("optimised")} fill={palette.ledger} isAnimationActive={false} />
              <Scatter name="Equal weight" data={points("equal_weight")} fill={palette.info} isAnimationActive={false} />
            </ScatterChart>
          </ResponsiveContainer>
        </ChartFrame>
      </div>

      <div className="flex flex-wrap gap-2">
        <Button variant="secondary" onClick={download}>Download allocation (CSV)</Button>
        {onApply && (
          <Button busy={applying} onClick={() => onApply(fractionsToPercentages(assets.map((a) => a.fund_id), comparison.optimised.weights))}>
            Apply optimised weights
          </Button>
        )}
      </div>
      {applied && <Notice>Optimised weights saved to the portfolio.</Notice>}
      {applyError ? <ErrorState error={applyError} /> : null}
      <Disclaimer>{result.disclaimer}</Disclaimer>
    </div>
  );
}
