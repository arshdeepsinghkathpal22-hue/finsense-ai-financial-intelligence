"""Generates the FinSense AI demonstration dataset.

EVERYTHING produced by this script is SYNTHETIC. Fund names, companies,
benchmarks, NAVs, AUM, SIP flows, holdings and documents are invented for
demonstrating and testing the application. They are not real market data
and must not be used for investment decisions.

The generator is deterministic (fixed seed), so running it again reproduces
the committed files exactly:

    python sample_data/generate_sample_data.py

Outputs:
    sample_data/csv/*.csv          structured data loaded by `python -m app.cli seed`
    sample_data/documents/*.pdf    sample documents indexed by `python -m app.cli ingest-samples`
    sample_data/documents/*.txt
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

SEED = 20260930
START = date(2021, 1, 4)
END = date(2026, 9, 30)
TRADING_DAYS = 252
HERE = Path(__file__).resolve().parent
CSV_DIR = HERE / "csv"
DOC_DIR = HERE / "documents"
SYNTHETIC_FOOTER = "SYNTHETIC DEMONSTRATION DOCUMENT - fictional fund, not real market data"


# --------------------------------------------------------------------------- market model


@dataclass(frozen=True)
class Regime:
    start: str
    end: str
    drift: float  # annualised
    vol: float  # annualised


# Calm and stressed periods for the synthetic equity market factor.
MARKET_REGIMES = [
    Regime("2021-01-01", "2022-04-29", 0.16, 0.14),
    Regime("2022-05-02", "2022-06-30", -0.95, 0.30),  # sharp correction
    Regime("2022-07-01", "2024-05-31", 0.15, 0.13),
    Regime("2024-06-03", "2024-06-28", -0.40, 0.28),
    Regime("2024-07-01", "2025-12-31", 0.12, 0.12),
    Regime("2026-01-01", "2026-03-31", -0.10, 0.26),  # volatile quarter
    Regime("2026-04-01", "2026-12-31", 0.14, 0.15),
]


def business_days() -> pd.DatetimeIndex:
    return pd.bdate_range(START, END)


def regime_returns(dates: pd.DatetimeIndex, regimes: list[Regime], rng: np.random.Generator) -> np.ndarray:
    out = np.empty(len(dates))
    for i, day in enumerate(dates):
        regime = next(r for r in regimes if pd.Timestamp(r.start) <= day <= pd.Timestamp(r.end))
        mu = regime.drift / TRADING_DAYS
        sigma = regime.vol / math.sqrt(TRADING_DAYS)
        # Student-t shocks (df=5) rescaled to unit variance give fatter tails.
        shock = rng.standard_t(5) / math.sqrt(5 / 3)
        out[i] = mu + sigma * shock
    return out


@dataclass(frozen=True)
class FundSpec:
    scheme_code: str
    name: str
    amc: str
    category: str
    asset_class: str
    benchmark: str
    beta: float
    alpha: float  # annualised, before expenses
    idio_vol: float  # annualised
    expense: float  # % per year
    start_nav: float
    launch: date
    risk_label: str
    option: str = "Growth"


BENCHMARKS = {
    "FSLC100": ("FS Large Cap 100 TRI (synthetic)", "total_return"),
    "FSMC150": ("FS Midcap 150 TRI (synthetic)", "total_return"),
    "FSSC250": ("FS Smallcap 250 TRI (synthetic)", "total_return"),
    "FS500": ("FS Broad Market 500 TRI (synthetic)", "total_return"),
    "FSBOND": ("FS Composite Bond Index (synthetic)", "total_return"),
    "FSLIQ": ("FS Liquid Index (synthetic)", "total_return"),
    "FSHYB": ("FS Hybrid 50:50 Index (synthetic)", "total_return"),
}

FUNDS = [
    FundSpec("FS-LC-001", "Aurora Bluechip Equity Fund - Direct Growth", "Aurora Asset Management",
             "Large Cap", "equity", "FSLC100", 0.93, 0.010, 0.035, 0.72, 52.40, date(2013, 1, 1),
             "Very High"),
    FundSpec("FS-MC-002", "Northstar Midcap Opportunities Fund - Direct Growth", "Northstar Mutual Fund",
             "Mid Cap", "equity", "FSMC150", 0.95, 0.015, 0.050, 0.81, 88.15, date(2014, 6, 1),
             "Very High"),
    FundSpec("FS-SC-003", "Kestrel Smallcap Fund - Direct Growth", "Kestrel Investment Managers",
             "Small Cap", "equity", "FSSC250", 0.90, 0.020, 0.070, 0.66, 10.00, date(2022, 3, 1),
             "Very High"),
    FundSpec("FS-FX-004", "Horizon Flexi Cap Fund - Direct Growth", "Horizon Mutual Fund",
             "Flexi Cap", "equity", "FS500", 0.97, 0.008, 0.040, 0.58, 41.72, date(2015, 9, 1),
             "Very High"),
    FundSpec("FS-FX-004D", "Horizon Flexi Cap Fund - Direct IDCW", "Horizon Mutual Fund",
             "Flexi Cap", "equity", "FS500", 0.97, 0.008, 0.040, 0.58, 24.10, date(2015, 9, 1),
             "Very High", option="IDCW"),
    FundSpec("FS-ELSS-005", "Banyan Tax Saver Fund (ELSS) - Direct Growth", "Banyan Capital AMC",
             "ELSS", "equity", "FS500", 1.02, 0.005, 0.045, 0.79, 96.30, date(2012, 2, 1), "Very High"),
    FundSpec("FS-IDX-006", "Polaris Large Cap 100 Index Fund - Direct Growth", "Polaris Funds",
             "Index Fund", "equity", "FSLC100", 1.00, 0.000, 0.004, 0.20, 18.65, date(2019, 8, 1),
             "Very High"),
    FundSpec("FS-DB-007", "Meridian Dynamic Bond Fund - Direct Growth", "Meridian Asset Management",
             "Dynamic Bond", "debt", "FSBOND", 1.10, 0.004, 0.012, 0.48, 31.08, date(2013, 1, 1),
             "Moderate"),
    FundSpec("FS-LIQ-008", "Harbor Liquid Fund - Direct Growth", "Harbor Mutual Fund", "Liquid", "debt",
             "FSLIQ", 1.00, 0.000, 0.001, 0.15, 3105.20, date(2013, 1, 1), "Low to Moderate"),
    FundSpec("FS-BAF-009", "Saffron Balanced Advantage Fund - Direct Growth", "Saffron Investments",
             "Dynamic Asset Allocation", "hybrid", "FSHYB", 0.90, 0.006, 0.020, 0.65, 27.90,
             date(2017, 11, 1), "Very High"),
]

# Idiosyncratic events planted so anomaly detection has something to find.
PLANTED_EVENTS = {
    ("FS-SC-003", "2023-11-15"): 0.062,  # fund-specific jump
    ("FS-MC-002", "2025-08-21"): -0.058,  # fund-specific drop
    ("FS-LC-001", "2026-02-12"): -0.041,
}
IDCW_PAYOUTS = {"2023-03-15": 1.50, "2025-03-14": 1.75}  # per unit, FS-FX-004D


def build_market(rng: np.random.Generator) -> tuple[pd.DatetimeIndex, dict[str, np.ndarray]]:
    dates = business_days()
    market = regime_returns(dates, MARKET_REGIMES, rng)
    # A single-day crash, shared by all equity indices.
    shock_day = dates.get_loc(pd.Timestamp("2024-06-04"))
    market[shock_day] = -0.055
    size = rng.normal(0.01 / TRADING_DAYS, 0.08 / math.sqrt(TRADING_DAYS), len(dates))
    rates = rng.normal(0.0, 0.028 / math.sqrt(TRADING_DAYS), len(dates))
    # Rates sell off in mid-2022 and rally in 2024.
    rates[(dates >= "2022-04-01") & (dates <= "2022-10-31")] -= 0.10 / TRADING_DAYS
    rates[(dates >= "2024-01-01") & (dates <= "2024-12-31")] += 0.03 / TRADING_DAYS
    bond = 0.072 / TRADING_DAYS + rates
    liquid = 0.064 / TRADING_DAYS + rng.normal(0.0, 0.0015 / math.sqrt(TRADING_DAYS), len(dates))

    def noise(vol: float) -> np.ndarray:
        return rng.normal(0.0, vol / math.sqrt(TRADING_DAYS), len(dates))

    bench = {
        "FSLC100": market,
        "FSMC150": 1.12 * market + 0.6 * size + noise(0.03),
        "FSSC250": 1.25 * market + 1.0 * size + noise(0.05),
        "FS500": 1.02 * market + 0.25 * size + noise(0.015),
        "FSBOND": bond,
        "FSLIQ": liquid,
    }
    bench["FSHYB"] = 0.5 * bench["FSLC100"] + 0.5 * bond
    return dates, bench


def build_fund_returns(spec: FundSpec, dates: pd.DatetimeIndex, bench: dict[str, np.ndarray],
                       rng: np.random.Generator) -> np.ndarray:
    b = bench[spec.benchmark]
    excess = b - (0.064 / TRADING_DAYS if spec.asset_class == "equity" else 0.0)
    base = (0.064 / TRADING_DAYS if spec.asset_class == "equity" else 0.0)
    daily_alpha = (spec.alpha - spec.expense / 100) / TRADING_DAYS
    idio = rng.normal(0.0, spec.idio_vol / math.sqrt(TRADING_DAYS), len(dates))
    returns = base + spec.beta * excess + daily_alpha + idio
    for (code, day), move in PLANTED_EVENTS.items():
        if code == spec.scheme_code:
            returns[dates.get_loc(pd.Timestamp(day))] = move
    return returns


def levels(start: float, returns: np.ndarray) -> np.ndarray:
    return start * np.cumprod(1.0 + returns)


# --------------------------------------------------------------------------- CSV writers


def write_csv(path: Path, header: list[str], rows: list[list]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def month_ends(dates: pd.DatetimeIndex) -> pd.DatetimeIndex:
    frame = pd.Series(dates, index=dates)
    return pd.DatetimeIndex(frame.groupby([dates.year, dates.month]).max().values)


# Holdings for the Aurora factsheets: (name, sector, weight) for top 10.
AURORA_TOP10 = {
    "2025-09-30": [
        ("Saraswati Infotech Ltd", "Information Technology", 7.9),
        ("Meridian Bank Ltd", "Financial Services", 7.6),
        ("Konark Energy Ltd", "Energy", 6.6),
        ("Vasant Consumer Products Ltd", "Consumer Goods", 5.4),
        ("Trident Finance Corporation", "Financial Services", 4.9),
        ("Lotus Pharma Ltd", "Healthcare", 3.9),
        ("Northgate Motors Ltd", "Automobiles", 3.5),
        ("Arcadia Telecom Ltd", "Telecom", 2.6),
        ("Pinnacle Software Services Ltd", "Information Technology", 2.5),
        ("Indus Capital Bank Ltd", "Financial Services", 2.4),
    ],
    "2026-03-31": [
        ("Meridian Bank Ltd", "Financial Services", 8.9),
        ("Saraswati Infotech Ltd", "Information Technology", 7.2),
        ("Konark Energy Ltd", "Energy", 6.4),
        ("Trident Finance Corporation", "Financial Services", 6.1),
        ("Vasant Consumer Products Ltd", "Consumer Goods", 5.0),
        ("Indus Capital Bank Ltd", "Financial Services", 4.8),
        ("Northgate Motors Ltd", "Automobiles", 3.9),
        ("Lotus Pharma Ltd", "Healthcare", 3.6),
        ("Pinnacle Software Services Ltd", "Information Technology", 3.2),
        ("Arcadia Telecom Ltd", "Telecom", 2.7),
    ],
}
AURORA_SECTORS = {
    "2025-09-30": {"Financial Services": 29.8, "Information Technology": 16.3, "Consumer Goods": 10.6,
                   "Energy": 9.1, "Healthcare": 7.8, "Automobiles": 7.0, "Capital Goods": 5.2,
                   "Materials": 4.5, "Telecom": 3.9, "Cash & Equivalents": 5.8},
    "2026-03-31": {"Financial Services": 34.2, "Information Technology": 14.1, "Consumer Goods": 9.8,
                   "Energy": 8.7, "Automobiles": 7.4, "Healthcare": 6.9, "Capital Goods": 5.6,
                   "Telecom": 4.1, "Materials": 3.9, "Cash & Equivalents": 5.3},
}
FILLER_NAMES = {
    "Financial Services": ["Bharat Housing Finance", "Coastal Small Finance Bank", "Sentinel Insurance",
                           "Prakash Asset Finance", "Unity Payments Ltd", "Ganga Cooperative Bank",
                           "Vista Life Insurance"],
    "Information Technology": ["Quanta Digital Ltd", "Nimbus Cloud Systems", "Orbit Data Labs"],
    "Consumer Goods": ["Amrit Foods Ltd", "Kesar Beverages Ltd", "Monsoon Home Care"],
    "Energy": ["Deccan Power Ltd", "Sagar Oil & Gas Ltd", "Surya Renewables Ltd"],
    "Healthcare": ["Neem Hospitals Ltd", "Cedar Diagnostics", "Tulsi Life Sciences"],
    "Automobiles": ["Rajpath Two Wheelers", "Velocity Auto Parts", "Tarmac Tyres Ltd"],
    "Capital Goods": ["Ashoka Engineering Ltd", "Banyan Electricals", "Forge Industrial Ltd"],
    "Materials": ["Himalaya Cement Ltd", "Steelpeak Ltd", "Coromandel Chemicals Ltd", "Tara Paints Ltd"],
    "Telecom": ["Signal Towers Ltd", "Relay Broadband Ltd"],
}


def aurora_holdings(as_of: str) -> list[tuple[str, str, str, float]]:
    """Top-10 plus filler holdings whose sector totals match the factsheet exactly."""
    rows = [(name, sector, "equity", weight) for name, sector, weight in AURORA_TOP10[as_of]]
    min_top = min(w for *_, w in rows)
    for sector, total in AURORA_SECTORS[as_of].items():
        if sector == "Cash & Equivalents":
            rows.append(("Cash & Equivalents (TREPS / net receivables)", sector, "cash", total))
            continue
        remaining = round(total - sum(w for _, s, _, w in rows if s == sector), 1)
        if remaining <= 0:
            continue
        names = FILLER_NAMES[sector]
        pieces = max(1, math.ceil(remaining / (min_top - 0.2)))
        if pieces > len(names):
            raise ValueError(f"Not enough filler names for {sector}")
        base = round(remaining / pieces, 1)
        weights = [base] * (pieces - 1) + [round(remaining - base * (pieces - 1), 1)]
        rows += [(names[i], sector, "equity", weights[i]) for i in range(pieces)]
    total = round(sum(w for *_, w in rows), 1)
    assert abs(total - 100.0) < 0.05, (as_of, total)
    return rows


GENERIC_HOLDINGS = {
    "equity": [("Meridian Bank Ltd", "Financial Services"), ("Saraswati Infotech Ltd", "Information Technology"),
               ("Konark Energy Ltd", "Energy"), ("Lotus Pharma Ltd", "Healthcare"),
               ("Ashoka Engineering Ltd", "Capital Goods"), ("Amrit Foods Ltd", "Consumer Goods"),
               ("Rajpath Two Wheelers", "Automobiles"), ("Himalaya Cement Ltd", "Materials"),
               ("Unity Payments Ltd", "Financial Services"), ("Nimbus Cloud Systems", "Information Technology"),
               ("Neem Hospitals Ltd", "Healthcare"), ("Signal Towers Ltd", "Telecom")],
    "debt": [("7.18% Government of India 2033", "Sovereign"), ("7.10% Government of India 2034", "Sovereign"),
             ("7.42% State Development Loan 2031", "Sovereign"),
             ("7.65% Bharat Housing Finance NCD 2028", "Corporate Bond (AAA)"),
             ("7.80% Deccan Power Ltd NCD 2029", "Corporate Bond (AAA)"),
             ("8.05% Coastal Small Finance Bank NCD 2027", "Corporate Bond (AA+)"),
             ("91-day Treasury Bill", "Money Market"), ("Certificate of Deposit - Indus Capital Bank", "Money Market")],
}


# --------------------------------------------------------------------------- documents


def pdf_document(path: Path, title: str, pages: list[list]) -> None:
    """Renders pages of (kind, payload) blocks to a PDF with a synthetic-data footer."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    styles = getSampleStyleSheet()
    body = ParagraphStyle("Body", parent=styles["BodyText"], fontName="Helvetica", fontSize=10, leading=13.5)
    h1 = ParagraphStyle("H1", parent=styles["Heading1"], fontName="Helvetica-Bold", fontSize=17, leading=21)
    h2 = ParagraphStyle("H2", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=13, leading=16,
                        spaceBefore=8)

    def footer(canvas, doc):  # type: ignore[no-untyped-def]
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.drawString(18 * mm, 10 * mm, SYNTHETIC_FOOTER)
        canvas.drawRightString(192 * mm, 10 * mm, f"Page {doc.page}")
        canvas.restoreState()

    story: list = []
    for page_no, blocks in enumerate(pages):
        if page_no:
            story.append(PageBreak())
        for kind, payload in blocks:
            if kind == "h1":
                story.append(Paragraph(payload, h1))
            elif kind == "h2":
                story.append(Paragraph(payload, h2))
            elif kind == "p":
                story.append(Paragraph(payload, body))
                story.append(Spacer(1, 4))
            elif kind == "table":
                table = Table(payload, hAlign="LEFT")
                table.setStyle(TableStyle([
                    ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9),
                    ("FONT", (0, 1), (-1, -1), "Helvetica", 9),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
                ]))
                story.append(table)
                story.append(Spacer(1, 6))
    doc = SimpleDocTemplate(str(path), pagesize=A4, title=title, author="FinSense AI sample generator",
                            leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=18 * mm,
                            invariant=1)
    doc.build(story, onFirstPage=footer, onLaterPages=footer)


def fmt_pct(x: float, digits: int = 2) -> str:
    return f"{x * 100:.{digits}f}%"


def stats_for(nav: pd.Series, as_of: str, rf: float = 0.065) -> dict:
    """Figures the synthetic AMC 'reports' in its factsheet (3-year daily window)."""
    end = pd.Timestamp(as_of)
    window = nav.loc[end - pd.DateOffset(years=3): end]
    r = window.pct_change().dropna()
    rf_d = (1 + rf) ** (1 / TRADING_DAYS) - 1
    sd = float(r.std(ddof=1) * math.sqrt(TRADING_DAYS))
    sharpe = float((r - rf_d).mean() / (r - rf_d).std(ddof=1) * math.sqrt(TRADING_DAYS))
    one_year = nav.loc[end - pd.DateOffset(years=1): end]
    three_year_cagr = float((window.iloc[-1] / window.iloc[0]) ** (365.25 / (window.index[-1] - window.index[0]).days) - 1)
    return {"sd": sd, "sharpe": sharpe, "one_year": float(one_year.iloc[-1] / one_year.iloc[0] - 1),
            "three_year": three_year_cagr, "nav": float(nav.loc[:end].iloc[-1])}


def aurora_factsheet(path: Path, as_of: str, label: str, nav: pd.Series, bench: pd.Series,
                     aum: float, previous: dict | None) -> dict:
    s = stats_for(nav, as_of)
    b = stats_for(bench, as_of)
    beta = float(np.cov(nav.loc[:as_of].pct_change().dropna().iloc[-756:],
                        bench.loc[:as_of].pct_change().dropna().iloc[-756:])[0, 1]
                 / bench.loc[:as_of].pct_change().dropna().iloc[-756:].var())
    top10 = AURORA_TOP10[as_of]
    sectors = AURORA_SECTORS[as_of]
    top10_total = sum(w for *_, w in top10)
    turnover = 0.24 if as_of.startswith("2025") else 0.28
    expense_direct = "0.69%" if as_of.startswith("2025") else "0.72%"
    page1 = [
        ("h1", f"Aurora Bluechip Equity Fund - Monthly Factsheet, {label}"),
        ("p", "<b>Scheme code:</b> FS-LC-001 &nbsp;&nbsp; <b>Category:</b> Large Cap Fund &nbsp;&nbsp; "
              "<b>Plan / Option:</b> Direct Plan - Growth"),
        ("h2", "Investment Objective"),
        ("p", "The scheme seeks to generate long-term capital appreciation by investing predominantly in "
              "equity and equity-related instruments of large-cap companies. At least 80% of net assets are "
              "invested in the 100 largest companies by full market capitalisation. There is no assurance "
              "that the investment objective of the scheme will be achieved."),
        ("h2", "Fund Facts"),
        ("table", [["Item", "Detail"],
                   ["Assets under management (AUM)", f"Rs {aum:,.2f} crore as on {as_of}"],
                   ["Benchmark", "FS Large Cap 100 TRI (synthetic index)"],
                   ["Fund managers", "Ananya Rao (since 2019), Vikram Mehta (since 2023)"],
                   ["Total expense ratio", f"Direct plan {expense_direct}; Regular plan 1.68%"],
                   ["Exit load", "1% if redeemed within 365 days of allotment; nil thereafter"],
                   ["Minimum SIP", "Rs 500 per month (minimum 6 instalments)"],
                   ["NAV (Direct - Growth)", f"Rs {s['nav']:.4f} as on {as_of}"]]),
    ]
    page2 = [
        ("h2", "Portfolio - Top 10 Holdings"),
        ("table", [["Company", "Sector", "% of net assets"]]
         + [[n, sec, f"{w:.1f}"] for n, sec, w in top10]
         + [["Top 10 total", "", f"{top10_total:.1f}"]]),
        ("h2", "Sector Allocation"),
        ("table", [["Sector", "% of net assets"]] + [[k, f"{v:.1f}"] for k, v in sectors.items()]),
        ("p", f"The ten largest holdings together accounted for {top10_total:.1f}% of net assets as on "
              f"{as_of}. Financial services remained the largest sector exposure at "
              f"{sectors['Financial Services']:.1f}%."),
    ]
    page3 = [
        ("h2", "Performance (Direct Plan - Growth)"),
        ("table", [["Period", "Fund", "Benchmark (FS Large Cap 100 TRI)"],
                   ["1 year (absolute)", fmt_pct(s["one_year"]), fmt_pct(b["one_year"])],
                   ["3 years (CAGR)", fmt_pct(s["three_year"]), fmt_pct(b["three_year"])]]),
        ("p", "Past performance may or may not be sustained in future. Returns are computed from NAV of the "
              "Direct Plan - Growth option and are net of expenses."),
        ("h2", "Risk Statistics"),
        ("table", [["Measure", "Value"],
                   ["Standard deviation (annualised)", fmt_pct(s["sd"])],
                   ["Beta (vs benchmark)", f"{beta:.2f}"],
                   ["Sharpe ratio", f"{s['sharpe']:.2f}"],
                   ["Portfolio turnover ratio", f"{turnover:.2f} times"]]),
        ("p", "Risk statistics are calculated on daily returns over the trailing three years. The Sharpe "
              "ratio uses a risk-free rate of 6.50% per annum."),
    ]
    if previous is None:
        commentary = (
            "Markets were range-bound during the half-year. We maintained a diversified large-cap "
            "portfolio with technology as the second-largest sector. The fund continues to avoid highly "
            "leveraged companies, and cash was held at about 5.8% of net assets to meet redemptions."
        )
    else:
        commentary = (
            f"Why has the fund's risk increased? Over the six months to {label}, we raised the allocation "
            f"to financial services from {previous['fin']:.1f}% to {sectors['Financial Services']:.1f}% "
            f"by adding to private sector banks and Trident Finance Corporation, and reduced information "
            f"technology from {previous['it']:.1f}% to {sectors['Information Technology']:.1f}%. As a "
            f"result the top 10 holdings now make up {top10_total:.1f}% of the portfolio, up from "
            f"{previous['top10']:.1f}%, so the portfolio is more concentrated. The market was also more "
            f"volatile in January to March 2026, and the three-year standard deviation of the fund rose "
            f"from {fmt_pct(previous['sd'])} to {fmt_pct(s['sd'])}."
        )
    page4 = [
        ("h2", "Fund Manager Commentary"),
        ("p", commentary),
        ("h2", "Outlook"),
        ("p", "We expect credit growth to support bank earnings, but valuations in parts of consumer "
              "goods remain elevated. We will continue to prefer companies with strong balance sheets and "
              "consistent cash flows."),
        ("h2", "Disclaimer"),
        ("p", "Mutual fund investments are subject to market risks; read all scheme related documents "
              "carefully. This factsheet is a synthetic sample created to demonstrate document retrieval "
              "and does not describe any real scheme."),
    ]
    pdf_document(path, f"Aurora Bluechip Equity Fund Factsheet {label}", [page1, page2, page3, page4])
    return {"fin": sectors["Financial Services"], "it": sectors["Information Technology"],
            "top10": top10_total, "sd": s["sd"]}


def meridian_sid(path: Path) -> None:
    pages = [
        [("h1", "Meridian Dynamic Bond Fund - Scheme Information Document"),
         ("p", "<b>Scheme code:</b> FS-DB-007. An open-ended dynamic debt scheme investing across duration. "
               "A relatively high interest rate risk and moderate credit risk scheme."),
         ("h2", "Investment Objective"),
         ("p", "To generate optimal returns through active management of a portfolio of debt and money "
               "market instruments across the yield curve. There is no assurance or guarantee that the "
               "investment objective of the scheme will be achieved."),
         ("h2", "Key Scheme Details"),
         ("table", [["Item", "Detail"], ["Benchmark", "FS Composite Bond Index (synthetic)"],
                    ["Fund manager", "Rohan Iyer (since 2020)"],
                    ["Potential Risk Class", "B-III (moderate credit risk, relatively high interest rate risk)"],
                    ["Riskometer", "Moderate"]])],
        [("h2", "Asset Allocation Pattern"),
         ("p", "Under normal circumstances the asset allocation of the scheme will be as follows:"),
         ("table", [["Instrument", "Minimum (%)", "Maximum (%)", "Risk profile"],
                    ["Government securities and treasury bills", "0", "100", "Low to Medium"],
                    ["Corporate bonds rated AA and above", "0", "60", "Medium"],
                    ["Money market instruments", "0", "100", "Low"],
                    ["Units of REITs and InvITs", "0", "10", "Medium to High"]]),
         ("p", "The scheme may use interest rate derivatives for hedging and portfolio rebalancing up to "
               "50% of net assets. The scheme will not invest in securitised debt or credit default swaps.")],
        [("h2", "Investment Strategy"),
         ("p", "The fund manager actively manages the portfolio's duration based on the outlook for "
               "interest rates, inflation and liquidity. The modified duration of the portfolio will "
               "normally be kept between 1 year and 8 years: it is lengthened when interest rates are "
               "expected to fall and shortened when rates are expected to rise."),
         ("p", "Credit quality is managed conservatively. At least 70% of the portfolio will be invested in "
               "sovereign securities and AAA-rated instruments, and exposure to any single corporate "
               "issuer is limited to 10% of net assets."),
         ("h2", "Portfolio Construction Process"),
         ("p", "Each month the investment committee reviews a macroeconomic framework covering policy "
               "rates, the fiscal deficit, government borrowing calendars and system liquidity, and sets a "
               "target duration band for the scheme.")],
        [("h2", "Risk Factors"),
         ("p", "<b>Interest rate risk:</b> bond prices fall when interest rates rise; schemes with longer "
               "duration are more sensitive. <b>Credit risk:</b> an issuer may default on interest or "
               "principal payments. <b>Liquidity risk:</b> some corporate bonds trade infrequently and may "
               "have to be sold at a discount. <b>Reinvestment risk:</b> coupons may be reinvested at lower "
               "rates when interest rates fall."),
         ("p", "Investors should note that the scheme's NAV can fall in periods of rising interest rates, "
               "such as the period from April to October 2022 in the synthetic dataset.")],
        [("h2", "Fees, Loads and Investment Limits"),
         ("table", [["Item", "Detail"], ["Exit load", "0.25% if redeemed within 30 days; nil thereafter"],
                    ["Total expense ratio", "Direct plan 0.48%; Regular plan 1.12%"],
                    ["Minimum lumpsum investment", "Rs 5,000"],
                    ["Minimum SIP", "Rs 500 per month (minimum 12 instalments)"],
                    ["NAV disclosure", "Every business day by 11 pm"]]),
         ("p", "This scheme information document is a synthetic sample created for the FinSense AI "
               "project and does not describe any real scheme.")],
    ]
    pdf_document(path, "Meridian Dynamic Bond Fund SID", pages)


def northstar_annual_review(path: Path, nav: pd.Series, bench: pd.Series) -> None:
    fy_start, fy_end = pd.Timestamp("2025-03-31"), pd.Timestamp("2026-03-31")
    f = nav.loc[:fy_end].iloc[-1] / nav.loc[:fy_start].iloc[-1] - 1
    b = bench.loc[:fy_end].iloc[-1] / bench.loc[:fy_start].iloc[-1] - 1
    text = f"""NORTHSTAR MIDCAP OPPORTUNITIES FUND
Annual Review for Financial Year 2025-26 (April 2025 to March 2026)

{SYNTHETIC_FOOTER}

PERFORMANCE REVIEW

During FY2025-26 the Direct Plan - Growth option of Northstar Midcap Opportunities Fund (scheme code FS-MC-002) returned {fmt_pct(f)} against {fmt_pct(b)} for its benchmark, the FS Midcap 150 TRI. Performance was helped by holdings in capital goods and hurt by a sharp fall in one consumer holding in August 2025.

PORTFOLIO CHANGES

The fund increased its allocation to capital goods and industrial companies from 14% to 19% of net assets, reflecting the government's infrastructure spending programme. Exposure to consumer discretionary companies was reduced from 17% to 12% after valuations rose. The number of stocks in the portfolio was kept between 55 and 65 to limit stock-specific risk.

RISK MANAGEMENT

No single stock is allowed to exceed 5% of net assets, and the fund keeps at least 65% of assets in midcap companies ranked 101 to 250 by market capitalisation. Liquidity is monitored weekly: the fund estimates that 80% of the portfolio can be sold within five trading days without exceeding 25% of average daily traded volume.

PORTFOLIO TURNOVER AND EXPENSES

Portfolio turnover for the year was 0.41 times. The total expense ratio of the Direct Plan was 0.81% and of the Regular Plan 1.84%.

OUTLOOK

Midcap valuations are above their ten-year average, so the fund manager expects returns to be more moderate than in the previous two years and will favour companies with earnings visibility.
"""
    path.write_text(text, encoding="utf-8")


# --------------------------------------------------------------------------- main


def main() -> None:
    rng = np.random.default_rng(SEED)
    dates, bench_returns = build_market(rng)
    CSV_DIR.mkdir(parents=True, exist_ok=True)
    DOC_DIR.mkdir(parents=True, exist_ok=True)

    write_csv(CSV_DIR / "benchmarks.csv", ["benchmark_code", "name", "return_basis"],
              [[code, name, basis] for code, (name, basis) in BENCHMARKS.items()])
    bench_levels = {code: pd.Series(levels(10000.0, r), index=dates) for code, r in bench_returns.items()}
    write_csv(CSV_DIR / "benchmark_values.csv", ["benchmark_code", "date", "value"],
              [[code, d.date().isoformat(), f"{v:.4f}"] for code, s in bench_levels.items() for d, v in s.items()])

    navs: dict[str, pd.Series] = {}
    nav_rows = []
    for spec in FUNDS:
        returns = build_fund_returns(spec, dates, bench_returns, rng)
        live = dates >= pd.Timestamp(spec.launch)  # funds launched mid-sample start at their NFO NAV
        series = pd.Series(levels(spec.start_nav, returns[live]), index=dates[live])
        if spec.option == "IDCW":
            # Payout reduces NAV by the distributed amount on the ex-date.
            factor = pd.Series(1.0, index=series.index)
            for day, amount in IDCW_PAYOUTS.items():
                ex = pd.Timestamp(day)
                factor.loc[ex:] *= 1 - amount / series.loc[ex]
            series = series * factor
        navs[spec.scheme_code] = series
        nav_rows += [[spec.scheme_code, d.date().isoformat(), f"{v:.4f}"] for d, v in series.items()]
    write_csv(CSV_DIR / "nav.csv", ["scheme_code", "date", "nav"], nav_rows)

    write_csv(
        CSV_DIR / "funds.csv",
        ["scheme_code", "name", "amc", "category", "asset_class", "plan", "option", "benchmark_code",
         "launch_date", "expense_ratio_pct", "risk_label"],
        [[f.scheme_code, f.name, f.amc, f.category, f.asset_class, "Direct", f.option, f.benchmark,
          f.launch.isoformat(), f"{f.expense:.2f}", f.risk_label] for f in FUNDS],
    )

    # AUM: grows with NAV plus noisy net flows. Aurora's series is then
    # rescaled smoothly (log-linear between anchors) so it passes exactly
    # through the AUM figures printed in its two factsheets.
    aum_rows, sip_rows = [], []
    anchors = {"FS-LC-001": {"2025-09-30": 11020.18, "2026-03-31": 12450.36}}
    base_aum = {"FS-LC-001": 6200, "FS-MC-002": 4100, "FS-SC-003": 450, "FS-FX-004": 3800, "FS-FX-004D": 620,
                "FS-ELSS-005": 2900, "FS-IDX-006": 900, "FS-DB-007": 2100, "FS-LIQ-008": 15800, "FS-BAF-009": 5200}
    for spec in FUNDS:
        nav = navs[spec.scheme_code]
        months = month_ends(nav.index)
        aum = float(base_aum[spec.scheme_code])
        values = []
        prev_nav = None
        for me in months:
            current = float(nav.loc[me])
            if prev_nav is not None:
                aum *= current / prev_nav
                aum *= 1 + rng.normal(0.006 if spec.asset_class != "debt" else 0.003, 0.01)
            prev_nav = current
            values.append(aum)
        series_aum = pd.Series(values, index=months)
        if spec.scheme_code in anchors:
            points = {pd.Timestamp(d): target / series_aum.loc[pd.Timestamp(d)]
                      for d, target in anchors[spec.scheme_code].items()}
            log_ratio = pd.Series(np.nan, index=months)
            for day, ratio_ in points.items():
                log_ratio.loc[day] = math.log(ratio_)
            log_ratio = log_ratio.interpolate().bfill().ffill()
            series_aum = series_aum * np.exp(log_ratio)
        for me, value in series_aum.items():
            aum_rows.append([spec.scheme_code, me.date().isoformat(), f"{value:.2f}"])
            if spec.asset_class in ("equity", "hybrid") and spec.option == "Growth":
                sip = value * rng.uniform(0.008, 0.014)
                accounts = int(sip * 1e7 / rng.uniform(2800, 3600))
                sip_rows.append([spec.scheme_code, me.replace(day=1).date().isoformat(), f"{sip:.2f}", accounts])
    write_csv(CSV_DIR / "aum.csv", ["scheme_code", "as_of_date", "aum_crore"], aum_rows)
    write_csv(CSV_DIR / "sip_flows.csv", ["scheme_code", "month", "sip_inflow_crore", "sip_accounts"], sip_rows)

    holding_rows = []
    for as_of in ("2025-09-30", "2026-03-31"):
        holding_rows += [["FS-LC-001", as_of, n, s, t, f"{w:.1f}"] for n, s, t, w in aurora_holdings(as_of)]
    for spec in FUNDS:
        if spec.scheme_code == "FS-LC-001" or spec.option != "Growth":
            continue
        pool = GENERIC_HOLDINGS["debt" if spec.asset_class == "debt" else "equity"]
        raw = rng.dirichlet(np.full(len(pool), 2.0)) * 94
        weights = np.round(raw, 1)
        weights[-1] = round(94 - weights[:-1].sum(), 1)
        for (name, sector), w in zip(pool, weights, strict=True):
            asset_type = "debt" if spec.asset_class == "debt" else "equity"
            holding_rows.append([spec.scheme_code, "2026-09-30", name, sector, asset_type, f"{w:.1f}"])
        holding_rows.append([spec.scheme_code, "2026-09-30", "Cash & Equivalents (TREPS / net receivables)",
                             "Cash & Equivalents", "cash", "6.0"])
    write_csv(CSV_DIR / "holdings.csv", ["scheme_code", "as_of_date", "holding_name", "sector", "asset_type",
                                         "weight_pct"], holding_rows)

    write_csv(CSV_DIR / "example_portfolio.csv", ["scheme_code", "weight_pct"],
              [["FS-LC-001", 30], ["FS-MC-002", 20], ["FS-SC-003", 10], ["FS-DB-007", 25], ["FS-LIQ-008", 15]])

    # A deliberately messy file for demonstrating import validation.
    write_csv(CSV_DIR / "nav_with_errors.csv", ["scheme_code", "date", "nav"], [
        ["FS-LC-001", "2026-09-29", f"{navs['FS-LC-001'].iloc[-2]:.4f}"],
        ["FS-LC-001", "2026-09-29", f"{navs['FS-LC-001'].iloc[-2]:.4f}"],  # exact duplicate
        ["FS-LC-001", "30-09-2026", f"{navs['FS-LC-001'].iloc[-1]:.4f}"],  # DD-MM-YYYY, accepted
        ["FS-MC-002", "2026-09-30", ""],  # missing NAV
        ["FS-MC-002", "2026-09-31", "100.0"],  # invalid date
        ["FS-SC-003", "2026-09-30", "-12.5"],  # negative NAV
        ["FS-XX-999", "2026-09-30", "10.0"],  # unknown scheme
        ["FS-IDX-006", "2026-09-30", f"{navs['FS-IDX-006'].iloc[-1] * 1.4:.4f}"],  # outlier, flagged
    ])

    previous = aurora_factsheet(DOC_DIR / "aurora_bluechip_factsheet_2025-09.pdf", "2025-09-30",
                                "September 2025", navs["FS-LC-001"], bench_levels["FSLC100"], 11020.18, None)
    current = aurora_factsheet(DOC_DIR / "aurora_bluechip_factsheet_2026-03.pdf", "2026-03-31", "March 2026",
                               navs["FS-LC-001"], bench_levels["FSLC100"], 12450.36, previous)
    if current["sd"] <= previous["sd"]:
        raise RuntimeError("Synthetic regimes no longer produce a risk increase; adjust MARKET_REGIMES.")
    meridian_sid(DOC_DIR / "meridian_dynamic_bond_sid.pdf")
    northstar_annual_review(DOC_DIR / "northstar_midcap_annual_review_fy2026.txt", navs["FS-MC-002"],
                            bench_levels["FSMC150"])
    print(f"Wrote {len(nav_rows)} NAV rows for {len(FUNDS)} synthetic funds to {CSV_DIR}")
    print(f"Wrote sample documents to {DOC_DIR}")


if __name__ == "__main__":
    main()
