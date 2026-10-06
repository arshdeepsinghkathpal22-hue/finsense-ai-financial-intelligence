"""What-if scenarios: SIP projections, market shocks and assumption changes.

Every function here is either a deterministic formula or a seeded Monte
Carlo simulation. Outputs are hypothetical illustrations under the stated
assumptions, never predictions or guarantees.
"""

from __future__ import annotations

import math

import numpy as np
from scipy import stats

from app.core.errors import ValidationFailedError
from app.services.analytics import metrics


def sip_projection(
    *, monthly_amount: float, years: int, annual_return: float, lumpsum: float = 0.0,
    annual_step_up: float = 0.0,
) -> dict:
    """Deterministic SIP value with contributions at the start of each month.

    The annual return is converted to an equivalent monthly rate
    ``(1 + r)^(1/12) - 1`` so a 12% assumption compounds to exactly 12% a year.
    """
    if monthly_amount < 0 or lumpsum < 0:
        raise ValidationFailedError("Amounts must be non-negative.")
    if not 1 <= years <= 50:
        raise ValidationFailedError("Horizon must be between 1 and 50 years.")
    if not -0.5 < annual_return < 1.0:
        raise ValidationFailedError("Annual return assumption must be between -50% and 100%.")
    monthly_rate = (1 + annual_return) ** (1 / 12) - 1
    value = lumpsum
    invested = lumpsum
    contribution = monthly_amount
    yearly = []
    for month in range(1, years * 12 + 1):
        value = (value + contribution) * (1 + monthly_rate)
        invested += contribution
        if month % 12 == 0:
            yearly.append({"year": month // 12, "invested": round(invested, 2), "value": round(value, 2)})
            contribution *= 1 + annual_step_up
    return {
        "kind": "deterministic",
        "final_value": round(value, 2),
        "total_invested": round(invested, 2),
        "gain": round(value - invested, 2),
        "yearly": yearly,
        "assumptions": {
            "annual_return": annual_return, "monthly_rate": monthly_rate,
            "contribution_timing": "start of month", "annual_step_up": annual_step_up,
            "taxes_and_costs": "Not modelled (exit loads, taxes and expense changes ignored).",
        },
    }


def monte_carlo_sip(
    *, monthly_amount: float, years: int, annual_return: float, annual_volatility: float,
    lumpsum: float = 0.0, paths: int = 2000, seed: int = 7,
) -> dict:
    """Seeded Monte Carlo of SIP outcomes with log-normal monthly returns.

    Monthly log returns are drawn from N(m, s^2) with s = sigma / sqrt(12)
    and m chosen so the *expected* gross annual return equals the
    assumption. Returns are independent across months (no regime changes,
    fat tails or autocorrelation), which understates real-world tail risk.
    """
    if not 0 <= annual_volatility < 1.5:
        raise ValidationFailedError("Annual volatility must be between 0% and 150%.")
    if not 100 <= paths <= 20000:
        raise ValidationFailedError("Number of paths must be between 100 and 20,000.")
    deterministic = sip_projection(monthly_amount=monthly_amount, years=years,
                                   annual_return=annual_return, lumpsum=lumpsum)
    months = years * 12
    s = annual_volatility / math.sqrt(12)
    m = math.log(1 + annual_return) / 12 - 0.5 * s**2
    rng = np.random.default_rng(seed)
    growth = np.exp(rng.normal(m, s, size=(paths, months)))
    values = np.full(paths, lumpsum, dtype=float)
    percentile_rows = []
    for month in range(months):
        values = (values + monthly_amount) * growth[:, month]
        if (month + 1) % 12 == 0:
            p5, p50, p95 = np.percentile(values, [5, 50, 95])
            percentile_rows.append({"year": (month + 1) // 12, "p5": float(p5), "p50": float(p50),
                                    "p95": float(p95)})
    invested = lumpsum + monthly_amount * months
    return {
        "kind": "monte_carlo",
        "paths": paths,
        "seed": seed,
        "total_invested": invested,
        "final_percentiles": {k: float(np.percentile(values, q)) for k, q in
                              (("p5", 5), ("p25", 25), ("p50", 50), ("p75", 75), ("p95", 95))},
        "probability_of_loss": float(np.mean(values < invested)),
        "yearly": percentile_rows,
        "deterministic_reference": deterministic["final_value"],
        "assumptions": {
            "annual_return": annual_return, "annual_volatility": annual_volatility,
            "distribution": "independent log-normal monthly returns",
            "limitations": "Ignores fat tails, volatility clustering, taxes and costs.",
        },
    }


def market_shock(
    *, weights: list[float], betas: list[float | None], labels: list[str], market_decline: float,
    portfolio_value: float,
) -> dict:
    """First-order impact of an index decline using each asset's historical beta.

    impact_i = beta_i * shock. This linear approximation ignores
    idiosyncratic moves and the tendency of correlations to rise in crashes.
    """
    if not -0.9 <= market_decline <= 0.5:
        raise ValidationFailedError("Market move must be between -90% and +50%.")
    w = metrics.normalise_weights(weights)
    rows = []
    total = 0.0
    for i, label in enumerate(labels):
        beta = betas[i]
        if beta is None:
            raise ValidationFailedError(f"No beta is available for {label}; cannot run a beta-based shock.")
        impact = beta * market_decline
        total += w[i] * impact
        rows.append({"label": label, "weight": float(w[i]), "beta": beta, "estimated_return": impact})
    return {
        "kind": "deterministic",
        "market_move": market_decline,
        "estimated_portfolio_return": total,
        "estimated_value_after": portfolio_value * (1 + total),
        "estimated_change": portfolio_value * total,
        "assets": rows,
        "assumptions": {
            "method": "Linear beta approximation: asset move = beta x market move.",
            "limitations": "Ignores idiosyncratic risk, non-linear effects and correlation changes in stress.",
        },
    }


def assumption_stress(
    *, weights: list[float], expected_returns: list[float], volatilities: list[float],
    correlation: list[list[float]] | None, volatility_multiplier: float = 1.0,
    correlation_override: float | None = None, return_shift: float = 0.0,
    confidence: float = 0.95, risk_free_rate: float = 0.0, horizon_days: int = 1,
    periods_per_year: int = 252,
) -> dict:
    """Portfolio risk under changed volatility / correlation / return assumptions."""
    w = metrics.normalise_weights(weights)
    n = len(w)
    mu = np.asarray(expected_returns, dtype=float)
    vols = np.asarray(volatilities, dtype=float)
    if mu.shape != (n,) or vols.shape != (n,):
        raise ValidationFailedError("Provide one expected return and volatility per asset.")
    if correlation is None:
        corr = np.eye(n)
    else:
        corr = np.asarray(correlation, dtype=float)
        if corr.shape != (n, n):
            raise ValidationFailedError("Correlation matrix must be N x N.")
    if not 0.1 <= volatility_multiplier <= 5:
        raise ValidationFailedError("Volatility multiplier must be between 0.1 and 5.")

    def evaluate(mu_v: np.ndarray, vol_v: np.ndarray, corr_m: np.ndarray) -> dict:
        cov = np.outer(vol_v, vol_v) * corr_m
        eig_min = float(np.linalg.eigvalsh(cov).min())
        if eig_min < -1e-10:
            raise ValidationFailedError(
                "These correlation assumptions are internally inconsistent (the implied "
                "covariance matrix is not positive semi-definite)."
            )
        ret = float(w @ mu_v)
        vol = metrics.portfolio_volatility(w, cov)
        h = horizon_days / periods_per_year
        z = stats.norm.ppf(1 - confidence)
        var = -(ret * h + z * vol * math.sqrt(h))
        return {"expected_return": ret, "volatility": vol,
                "sharpe": (ret - risk_free_rate) / vol if vol > 0 else None,
                "parametric_var": var, "horizon_days": horizon_days, "confidence": confidence}

    stressed_corr = corr.copy()
    if correlation_override is not None:
        if not -1 / max(n - 1, 1) <= correlation_override <= 1:
            raise ValidationFailedError("Uniform correlation must be between -1/(N-1) and 1.")
        stressed_corr = np.full((n, n), correlation_override)
        np.fill_diagonal(stressed_corr, 1.0)
    return {
        "kind": "deterministic",
        "baseline": evaluate(mu, vols, corr),
        "scenario": evaluate(mu + return_shift, vols * volatility_multiplier, stressed_corr),
        "changes": {"volatility_multiplier": volatility_multiplier,
                    "correlation_override": correlation_override, "return_shift": return_shift},
        "assumptions": {"method": "Parametric (normal) portfolio model using the supplied inputs."},
    }
