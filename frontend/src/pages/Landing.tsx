import { Link, Navigate } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";
import { Logo } from "../components/Layout";
import { Disclaimer } from "../components/ui";

const capabilities = [
  {
    title: "Risk you can audit",
    text: "CAGR, volatility, Sharpe, Sortino, beta, drawdown, VaR and CVaR from NAV history, each shown with its period, method and risk-free rate.",
  },
  {
    title: "Answers with their sources",
    text: "Ask about factsheets and scheme documents. Every statement links to the passage and page it came from; if the documents don't say, the assistant says so.",
  },
  {
    title: "Portfolio construction",
    text: "Build allocations, see risk contributions, and run constrained mean-variance optimisation with an efficient frontier and before/after comparison.",
  },
  {
    title: "Forecasts held to account",
    text: "Ridge and random forest forecasts are tested on held-out data against a simple baseline, with SHAP explanations and a stated uncertainty band.",
  },
];

export function LandingPage() {
  const { user, loading } = useAuth();
  if (!loading && user) return <Navigate to="/dashboard" replace />;
  return (
    <div className="min-h-screen">
      <header className="mx-auto flex max-w-6xl items-center justify-between px-5 py-5">
        <Logo />
        <nav aria-label="Account" className="flex items-center gap-3 text-sm">
          <Link to="/login" className="text-muted hover:text-paper">
            Sign in
          </Link>
          <Link to="/register" className="rounded-md bg-ledger px-3.5 py-2 font-semibold text-ink-950 hover:bg-ledger/90">
            Create account
          </Link>
        </nav>
      </header>

      <main className="mx-auto max-w-6xl px-5">
        <section className="grid items-center gap-10 py-14 lg:grid-cols-[1.1fr_1fr]">
          <div>
            <h1 className="text-4xl leading-tight sm:text-5xl">Mutual fund research where every number shows its working.</h1>
            <p className="mt-5 max-w-xl text-lg text-muted">
              FinSense AI combines fund analytics, portfolio optimisation and a document assistant that cites the factsheet
              page behind each answer. It supports decisions; it doesn't make them.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <Link to="/register" className="rounded-md bg-ledger px-5 py-2.5 font-semibold text-ink-950 hover:bg-ledger/90">
                Create a free account
              </Link>
              <Link to="/login" className="rounded-md border border-ink-600 px-5 py-2.5 text-paper hover:border-ledger/70">
                Sign in
              </Link>
            </div>
          </div>

          <figure className="panel p-5" aria-label="Example of a cited answer">
            <p className="text-sm text-muted">You ask</p>
            <p className="mt-1 font-serif text-lg">Why has the Aurora Bluechip fund's risk increased?</p>
            <div className="mt-4 border-l-2 border-ledger pl-4 text-sm leading-relaxed">
              <p>
                The manager raised financial services from 29.8% to 34.2% and the top 10 holdings now make up 51.8% of the
                portfolio
                <span className="ml-1 rounded border border-ledger/50 px-1 text-xs text-ledger">S1</span>. FinSense's own
                calculation shows annualised volatility for the same window
                <span className="ml-1 rounded border border-info/50 px-1 text-xs text-info">T1</span>.
              </p>
            </div>
            <figcaption className="mt-4 text-xs text-faint">
              S1 · Aurora Bluechip factsheet, March 2026, page 4 &nbsp;|&nbsp; T1 · risk metrics computed from NAV history.
              Example uses the bundled synthetic demo data.
            </figcaption>
          </figure>
        </section>

        <section aria-labelledby="capabilities" className="border-t border-ink-800 py-12">
          <h2 id="capabilities" className="text-2xl">
            What you can do
          </h2>
          <div className="mt-6 grid gap-6 sm:grid-cols-2">
            {capabilities.map((item) => (
              <div key={item.title}>
                <h3 className="text-lg">{item.title}</h3>
                <p className="mt-1 text-muted">{item.text}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="border-t border-ink-800 py-10">
          <h2 className="text-2xl">A decision-support tool, not an oracle</h2>
          <p className="mt-3 max-w-3xl text-muted">
            Historical metrics describe the past. Optimised allocations are optimal for past data. Forecasts are uncertain
            estimates, reported next to the baseline they must beat. The demo dataset is synthetic and labelled wherever it
            appears.
          </p>
        </section>
      </main>
      <footer className="mx-auto max-w-6xl px-5 pb-8">
        <Disclaimer />
      </footer>
    </div>
  );
}
