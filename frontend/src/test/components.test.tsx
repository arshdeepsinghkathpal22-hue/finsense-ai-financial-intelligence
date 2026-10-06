import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ProtectedRoute } from "../auth/AuthContext";
import { KpiCard, RiskKpis } from "../components/metrics";
import { AnswerText } from "../pages/Assistant";
import { LoginPage, RegisterPage, passwordProblems } from "../pages/Auth";
import { jsonResponse, mockFetch, renderWithProviders } from "./utils";

const unauthenticated = () => jsonResponse(401, { error: { code: "not_authenticated", message: "Sign in to continue." } });

describe("authentication UI", () => {
  it("redirects anonymous users from protected pages to login", async () => {
    mockFetch(unauthenticated);
    renderWithProviders(<ProtectedRoute><p>secret page</p></ProtectedRoute>, { route: "/portfolios" });
    expect(await screen.findByText("login page")).toBeInTheDocument();
    expect(screen.queryByText("secret page")).not.toBeInTheDocument();
  });

  it("shows protected content once the session is valid", async () => {
    mockFetch(() => jsonResponse(200, { id: "u1", email: "a@example.com", display_name: "A", role: "user", preferences: {}, created_at: "2026-01-01" }));
    renderWithProviders(<ProtectedRoute><p>secret page</p></ProtectedRoute>, { route: "/portfolios" });
    expect(await screen.findByText("secret page")).toBeInTheDocument();
  });

  it("validates the login form before calling the API", async () => {
    const fetchMock = mockFetch(unauthenticated);
    renderWithProviders(<LoginPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Sign in" }));
    expect(screen.getByText("Enter a valid e-mail address.")).toBeInTheDocument();
    expect(screen.getByText("Enter your password.")).toBeInTheDocument();
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(0);
  });

  it("logs in with the entered credentials and shows server errors", async () => {
    const fetchMock = mockFetch((method, path) => {
      if (method === "POST" && path === "/auth/login") return jsonResponse(401, { error: { code: "not_authenticated", message: "Invalid e-mail or password." } });
      return unauthenticated();
    });
    renderWithProviders(<LoginPage />);
    await userEvent.type(screen.getByLabelText("E-mail"), "demo@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "wrong-Password1");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("Invalid e-mail or password.")).toBeInTheDocument();
    const login = fetchMock.mock.calls.find(([url, init]) => String(url).endsWith("/auth/login") && init?.method === "POST");
    expect(JSON.parse(String(login?.[1]?.body))).toEqual({ email: "demo@example.com", password: "wrong-Password1" });
  });

  it("applies the password policy on registration", async () => {
    mockFetch(unauthenticated);
    renderWithProviders(<RegisterPage />);
    await userEvent.type(screen.getByLabelText("Name"), "Asha");
    await userEvent.type(screen.getByLabelText("E-mail"), "asha@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "short");
    await userEvent.type(screen.getByLabelText("Repeat password"), "short");
    await userEvent.click(screen.getByRole("button", { name: "Create account" }));
    await waitFor(() => expect(screen.getByText(/Password needs at least 10 characters/)).toBeInTheDocument());
    expect(passwordProblems("Valid-Password1")).toEqual([]);
  });
});

describe("display components", () => {
  it("renders n/a with the reason when a metric is unavailable", () => {
    render(<KpiCard label="Beta" metric={{ value: null, unit: "ratio", unavailable_reason: "Beta needs at least 60 return observations." }} />);
    expect(screen.getByText("n/a")).toBeInTheDocument();
    expect(screen.getByText(/at least 60 return observations/)).toBeInTheDocument();
  });

  it("turns citation markers into buttons that report the marker", async () => {
    const onCite = vi.fn();
    render(
      <AnswerText
        text="Exit load is 0.25% [S1]; volatility 14.6% [T1]; forged [S7]."
        sources={[{ marker: "S1", cited: true, chunk_id: "c", document_id: "d", document_title: "SID", filename: "f.pdf", doc_type: "sid", as_of_date: null, page_start: 5, page_end: 5, section: null, excerpt: "", is_synthetic: true, flags: [] }]}
        calculations={[{ marker: "T1", title: "Risk", lines: [] }]}
        onCite={onCite}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Show source S1" }));
    expect(onCite).toHaveBeenCalledWith("S1");
    expect(screen.getByRole("button", { name: "Show calculation T1" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /S7/ })).not.toBeInTheDocument();
    expect(screen.getByText(/forged \[S7\]/)).toBeInTheDocument();
  });

  it("warns when the history is shorter than the requested period", () => {
    const assumptions = {
      frequency: "daily", periods_per_year: 252, risk_free_rate_annual: 0.065, return_basis: "nav_growth",
      return_basis_note: "NAV growth", var_method: "historical", sharpe_method: "excess", sortino_method: "downside",
    };
    const period = { start: "2022-03-01", end: "2026-09-30", note: "Available history starts on 2022-03-01, which is shorter than the requested 5y period." };
    render(<RiskKpis metrics={{ cumulative_return: { value: 0.5, unit: "fraction" } }} assumptions={assumptions} period={period} />);
    expect(screen.getByText(/shorter than the requested 5y period/)).toBeInTheDocument();
  });
});
