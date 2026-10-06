import { useMutation, useQuery } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";

import { api, errorMessage } from "../api/client";
import type { Period, RiskProfile, SystemStatus, User } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { Badge, Button, Card, ErrorState, Field, Loading, Notice, PageHeader } from "../components/ui";
import { formatDate, pct } from "../lib/format";
import { passwordProblems } from "./Auth";

export default function SettingsPage() {
  const { user } = useAuth();
  return (
    <>
      <PageHeader title="Settings" description="Your profile, analysis preferences, security and how this installation is configured." />
      <div className="grid gap-6 xl:grid-cols-2">
        <ProfileCard />
        <PasswordCard />
        <SystemCard />
        <Card title="Sessions">
          <p className="text-sm text-muted">Sign out of FinSense on every browser and device, including this one.</p>
          <SignOutEverywhere />
        </Card>
      </div>
      {user?.role === "admin" && (
        <p className="mt-6 text-sm text-muted">Data imports, retrieval diagnostics and the audit log are on the <Link className="text-ledger hover:underline" to="/admin">administration page</Link>.</p>
      )}
    </>
  );
}

function ProfileCard() {
  const { user, setUser } = useAuth();
  const [name, setName] = useState(user?.display_name ?? "");
  const [rf, setRf] = useState(user?.preferences.risk_free_rate !== undefined ? String(+(user.preferences.risk_free_rate * 100).toFixed(4)) : "");
  const [period, setPeriod] = useState<Period | "">(user?.preferences.default_period ?? "");
  const [profile, setProfile] = useState<RiskProfile | "">(user?.preferences.risk_profile ?? "");
  const save = useMutation({
    mutationFn: () => {
      const preferences: Record<string, unknown> = {};
      if (rf.trim() !== "") preferences.risk_free_rate = Number(rf) / 100;
      if (period) preferences.default_period = period;
      if (profile) preferences.risk_profile = profile;
      return api.patch<User>("/auth/me", { display_name: name.trim(), preferences });
    },
    onSuccess: setUser,
  });
  return (
    <Card title="Profile and preferences">
      <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        <Field label="Display name">{(id) => <input id={id} className="field-input" value={name} maxLength={80} onChange={(e) => setName(e.target.value)} />}</Field>
        <p className="text-sm text-muted">E-mail: {user?.email}</p>
        <Field label="Risk-free rate for your analyses (% a year)" hint="Used for Sharpe and Sortino ratios. Empty: the server default.">
          {(id) => <input id={id} className="field-input" inputMode="decimal" value={rf} onChange={(e) => setRf(e.target.value)} placeholder="e.g. 6.5" />}
        </Field>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Preferred analysis period">
            {(id) => (
              <select id={id} className="field-input" value={period} onChange={(e) => setPeriod(e.target.value as Period)}>
                <option value="">No preference</option><option value="1y">1 year</option><option value="3y">3 years</option><option value="5y">5 years</option><option value="max">Full history</option>
              </select>
            )}
          </Field>
          <Field label="Risk profile">
            {(id) => (
              <select id={id} className="field-input" value={profile} onChange={(e) => setProfile(e.target.value as RiskProfile)}>
                <option value="">No preference</option><option value="conservative">Conservative</option><option value="moderate">Moderate</option><option value="aggressive">Aggressive</option>
              </select>
            )}
          </Field>
        </div>
        {save.error && <Notice tone="loss">{errorMessage(save.error)}</Notice>}
        {save.isSuccess && <Notice>Saved.</Notice>}
        <Button type="submit" busy={save.isPending} disabled={!name.trim()}>Save profile</Button>
      </form>
    </Card>
  );
}

function PasswordCard() {
  const { setUser } = useAuth();
  const navigate = useNavigate();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault();
    const problems = passwordProblems(next);
    if (problems.length) return setError(`New password needs ${problems.join(", ")}.`);
    setBusy(true);
    setError(null);
    try {
      await api.post("/auth/change-password", { current_password: current, new_password: next });
      setUser(null);
      navigate("/login", { replace: true });
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Card title="Change password" subtitle="You will be signed out everywhere afterwards.">
      <form className="space-y-3" onSubmit={submit} noValidate>
        <Field label="Current password">{(id) => <input id={id} type="password" autoComplete="current-password" className="field-input" value={current} onChange={(e) => setCurrent(e.target.value)} />}</Field>
        <Field label="New password" hint="At least 10 characters with upper- and lower-case letters and a digit.">
          {(id) => <input id={id} type="password" autoComplete="new-password" className="field-input" value={next} onChange={(e) => setNext(e.target.value)} />}
        </Field>
        {error && <Notice tone="loss">{error}</Notice>}
        <Button type="submit" busy={busy} disabled={!current || !next}>Change password</Button>
      </form>
    </Card>
  );
}

function SignOutEverywhere() {
  const { setUser } = useAuth();
  const navigate = useNavigate();
  const run = useMutation({
    mutationFn: () => api.post("/auth/logout-all"),
    onSuccess: () => {
      setUser(null);
      navigate("/login", { replace: true });
    },
  });
  return (
    <>
      <Button variant="danger" className="mt-3" busy={run.isPending} onClick={() => run.mutate()}>Sign out everywhere</Button>
      {run.error && <div className="mt-2"><ErrorState error={run.error} /></div>}
    </>
  );
}

function SystemCard() {
  const status = useQuery({ queryKey: ["system-status"], queryFn: () => api.get<SystemStatus>("/system/status") });
  const s = status.data;
  return (
    <Card title="System status" subtitle="What this installation is configured to do. Secrets are never shown.">
      {status.isLoading && <Loading />}
      {status.error && <ErrorState error={status.error} />}
      {s && (
        <dl className="grid grid-cols-[11rem_1fr] gap-y-2 text-sm">
          <dt className="text-faint">Language model</dt>
          <dd>{s.llm.configured ? <><Badge tone="gain">Configured</Badge> {s.llm.provider} / {s.llm.model}</> : <><Badge>Not configured</Badge> <span className="text-muted">{s.llm.mode_without_llm}</span></>}</dd>
          <dt className="text-faint">Embeddings</dt><dd>{s.embeddings.provider} · {s.embeddings.model} · {s.embeddings.dimensions} dimensions</dd>
          <dt className="text-faint">Reranker</dt><dd>{s.reranker}</dd>
          <dt className="text-faint">Live NAV data (AMFI)</dt><dd>{s.live_data.amfi_enabled ? "Enabled" : "Disabled"}</dd>
          <dt className="text-faint">Password reset e-mail</dt><dd>{s.email.password_reset_available ? "Available" : "Not configured"}</dd>
          <dt className="text-faint">Risk-free rate</dt><dd>{pct(s.conventions.risk_free_rate)} a year (server default)</dd>
          <dt className="text-faint">Annualisation</dt><dd>{s.conventions.trading_days_per_year} trading days</dd>
          <dt className="text-faint">Data</dt><dd>{s.data.synthetic_funds} synthetic and {s.data.real_funds} real funds; latest NAV {formatDate(s.data.latest_nav_date)}; {s.data.documents} documents</dd>
          <dt className="text-faint">Environment</dt><dd>{s.app.environment}</dd>
        </dl>
      )}
      {s?.notice && <div className="mt-3"><Notice tone="caution">{s.notice}</Notice></div>}
    </Card>
  );
}
