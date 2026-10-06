"""Decides which tools (and whether document retrieval) a question needs.

The rule-based router is always available and fully deterministic. When an
LLM is configured, :func:`plan_with_llm` asks it for a JSON plan instead; the
plan is parsed strictly and every call is re-validated by ``tools.execute``,
falling back to the rule-based plan on any problem.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from app.models import Fund
from app.services.assistant.tools import TOOLS, ToolCall, tool_catalog
from app.services.rag.llm import ChatMessage, LLMClient, LLMError

logger = logging.getLogger("finsense.router")

_METRICS = re.compile(
    r"\b(return|returns|cagr|perform\w*|volatil\w*|sharpe|sortino|drawdown|var|cvar|value at risk|beta|"
    r"risk metrics?|risk-adjusted|standard deviation)\b", re.I)
_RISK_CHANGE = re.compile(
    r"\b(risk|volatil\w*)\b.*\b(increas\w*|ris(e|en|ing)|rose|higher|decreas\w*|fall|fell|lower|chang\w*)\b|"
    r"\b(increas\w*|chang\w*)\b.*\b(risk|volatil\w*)\b", re.I)
_COMPARE = re.compile(r"\b(compare|comparison|versus|vs\.?|better than|riskier than|which (fund|one) is)\b", re.I)
_FORECAST = re.compile(r"\b(forecast\w*|predict\w*|projection|next (week|month|quarter)|will .* (rise|fall|go))\b",
                       re.I)
_PORTFOLIO = re.compile(r"\bportfolio\b", re.I)
_OPTIMISE = re.compile(r"\b(optimi[sz]\w*|rebalanc\w*|efficient frontier|best allocation|ideal allocation|"
                       r"maximi[sz]e sharpe)\b", re.I)
_PERIOD = re.compile(r"\b(\d{1,2})\s*(y|yr|yrs|year|years)\b|\b(since inception|all time|max(imum)? history)\b", re.I)
_HORIZON = re.compile(r"\b(week|month|quarter|3 months|three months)\b", re.I)
_RISK_PROFILE = re.compile(r"\b(conservative|moderate|aggressive)\b", re.I)


@dataclass
class Plan:
    calls: list[ToolCall] = field(default_factory=list)
    use_documents: bool = True
    planner: str = "rules"
    notes: list[str] = field(default_factory=list)


def _period(question: str) -> str:
    match = _PERIOD.search(question)
    if not match:
        return "3y"
    if match.group(3):
        return "max"
    years = int(match.group(1))
    return {1: "1y", 3: "3y", 5: "5y"}.get(years, "max" if years > 5 else "3y")


def _horizon(question: str) -> int:
    match = _HORIZON.search(question)
    if not match:
        return 21
    word = match.group(1).lower()
    return {"week": 5, "month": 21}.get(word, 63)


def rule_based_plan(question: str, funds: list[Fund], portfolio_names: list[str]) -> Plan:
    plan = Plan()
    growth_funds = [f for f in funds if f.return_basis == "nav_growth"] or funds
    codes = [f.scheme_code for f in growth_funds]
    period = _period(question)
    wants_portfolio = bool(_PORTFOLIO.search(question)) and bool(portfolio_names)
    portfolio_ref = next((n for n in portfolio_names if n.lower() in question.lower()),
                         portfolio_names[0] if len(portfolio_names) == 1 else None)

    if _OPTIMISE.search(question) and wants_portfolio and portfolio_ref:
        profile = _RISK_PROFILE.search(question)
        plan.calls.append(ToolCall("optimise_portfolio", {
            "portfolio": portfolio_ref, "risk_profile": profile.group(1).lower() if profile else "moderate"}))
    elif wants_portfolio and portfolio_ref and not codes:
        plan.calls.append(ToolCall("portfolio_summary", {"portfolio": portfolio_ref, "period": period}))
    elif wants_portfolio and not portfolio_ref:
        plan.notes.append("You have several portfolios; name the one you mean.")

    if len(codes) >= 2 and _COMPARE.search(question):
        plan.calls.append(ToolCall("compare_funds", {"funds": codes[:5], "period": period}))
    elif codes:
        fund = codes[0]
        if _FORECAST.search(question):
            plan.calls.append(ToolCall("forecast", {"fund": fund, "horizon_days": _horizon(question)}))
        if _RISK_CHANGE.search(question):
            plan.calls.append(ToolCall("risk_change", {"fund": fund}))
        elif _METRICS.search(question):
            plan.calls.append(ToolCall("fund_metrics", {"fund": fund, "period": period}))
    elif (_METRICS.search(question) or _FORECAST.search(question)) and not wants_portfolio:
        plan.notes.append("Name a fund (or scheme code) to get calculated metrics.")
    # Documents are always searched; the evidence gate decides whether
    # anything relevant was found.
    plan.use_documents = True
    return plan


PLANNER_PROMPT = """You plan which analytics to run for a question about mutual funds.
Available tools (JSON schemas):
{catalog}

Funds mentioned in the question (scheme codes): {funds}
The user's portfolios: {portfolios}

Reply with ONLY a JSON object of the form
{{"tools": [{{"name": "<tool>", "args": {{...}}}}], "use_documents": true}}
Use at most 3 tools. Use an empty list when no calculation is needed. Never invent fund codes or
portfolio names that are not listed above."""


def plan_with_llm(llm: LLMClient, question: str, funds: list[Fund], portfolio_names: list[str]) -> Plan | None:
    system = PLANNER_PROMPT.format(
        catalog=json.dumps(tool_catalog()), funds=[f.scheme_code for f in funds] or "none",
        portfolios=portfolio_names or "none")
    try:
        raw = llm.complete(system, [ChatMessage("user", question[:1000])], max_tokens=400)
    except LLMError:
        return None
    match = re.search(r"\{.*\}", raw, re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
        calls = [ToolCall(str(c["name"]), dict(c.get("args") or {})) for c in data.get("tools", [])][:3]
    except (ValueError, TypeError, KeyError, AttributeError):
        return None
    if any(c.name not in TOOLS for c in calls):
        logger.info("LLM planner proposed an unknown tool; using rule-based plan")
        return None
    known_codes = {f.scheme_code for f in funds}
    for call in calls:
        # The planner may only reference funds the resolver found in the question.
        for key in ("fund",):
            if key in call.args and str(call.args[key]) not in known_codes:
                return None
        if "funds" in call.args and not set(map(str, call.args.get("funds") or [])) <= known_codes:
            return None
        if "portfolio" in call.args and str(call.args["portfolio"]) not in portfolio_names:
            return None
    return Plan(calls=calls, use_documents=bool(data.get("use_documents", True)), planner="llm")
