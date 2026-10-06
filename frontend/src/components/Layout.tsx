import { useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";
import { Button, Disclaimer } from "./ui";

const NAV = [
  { to: "/dashboard", label: "Dashboard" },
  { to: "/funds", label: "Fund explorer" },
  { to: "/risk", label: "Risk analytics" },
  { to: "/portfolios", label: "Portfolios" },
  { to: "/assistant", label: "Research assistant" },
  { to: "/documents", label: "Documents" },
  { to: "/ml", label: "Forecasts & anomalies" },
  { to: "/what-if", label: "What-if simulator" },
  { to: "/settings", label: "Settings" },
];

export function Logo() {
  return (
    <span className="flex items-center gap-2 font-serif text-xl font-semibold text-paper">
      <svg viewBox="0 0 32 32" className="h-7 w-7" aria-hidden="true">
        <rect width="32" height="32" rx="6" fill="#172b42" />
        <path d="M7 22l6-7 5 4 7-9" fill="none" stroke="#5fbf8f" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      FinSense AI
    </span>
  );
}

export function AppLayout() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const links = user?.role === "admin" ? [...NAV, { to: "/admin", label: "Administration" }] : NAV;

  const nav = (
    <nav aria-label="Main" className="flex flex-col gap-0.5">
      {links.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          onClick={() => setOpen(false)}
          className={({ isActive }) =>
            `rounded-md px-3 py-2 text-sm ${isActive ? "bg-ink-800 text-paper shadow-[inset_3px_0_0_var(--color-ledger)]" : "text-muted hover:bg-ink-850 hover:text-paper"}`
          }
        >
          {item.label}
        </NavLink>
      ))}
    </nav>
  );

  return (
    <div className="min-h-screen lg:grid lg:grid-cols-[15rem_1fr]">
      <aside className="hidden border-r border-ink-800 bg-ink-900/60 px-3 py-5 lg:flex lg:flex-col lg:gap-6">
        <div className="px-2">
          <Logo />
        </div>
        {nav}
        <div className="mt-auto space-y-3 px-2 text-sm">
          <p className="truncate text-muted" title={user?.email}>
            {user?.display_name}
            {user?.role === "admin" && <span className="ml-1 text-xs text-caution">(admin)</span>}
          </p>
          <Button variant="secondary" className="w-full" onClick={() => logout().then(() => navigate("/login"))}>
            Sign out
          </Button>
        </div>
      </aside>

      <header className="flex items-center justify-between border-b border-ink-800 px-4 py-3 lg:hidden">
        <Logo />
        <Button variant="secondary" aria-expanded={open} aria-controls="mobile-nav" onClick={() => setOpen((v) => !v)}>
          Menu
        </Button>
      </header>
      {open && (
        <div id="mobile-nav" className="border-b border-ink-800 bg-ink-900 px-3 py-3 lg:hidden">
          {nav}
          <Button variant="secondary" className="mt-3 w-full" onClick={() => logout().then(() => navigate("/login"))}>
            Sign out
          </Button>
        </div>
      )}

      <div className="flex min-w-0 flex-col">
        <main className="mx-auto w-full max-w-7xl flex-1 px-4 py-6 sm:px-6 lg:px-8">
          <Outlet />
        </main>
        <footer className="mx-auto w-full max-w-7xl px-4 pb-6 sm:px-6 lg:px-8">
          <Disclaimer />
        </footer>
      </div>
    </div>
  );
}
