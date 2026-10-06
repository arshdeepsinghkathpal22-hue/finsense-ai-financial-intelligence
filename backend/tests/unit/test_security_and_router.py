"""Security helpers, configuration validation and the assistant's tool routing."""

import logging

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.core import security
from app.core.logging import RedactingFilter
from app.core.rate_limit import RateLimiter
from app.services.assistant import router
from app.services.assistant.tools import TOOLS, ToolCall


def test_passwords_hashed_with_argon2id():
    hashed = security.hash_password("Correct-Horse-9")
    assert hashed.startswith("$argon2id$")
    assert security.verify_password(hashed, "Correct-Horse-9")
    assert not security.verify_password(hashed, "wrong")
    assert not security.verify_password(None, "anything")  # unknown user still runs a hash check


def test_password_policy():
    assert security.validate_password_strength("Strong-Pass-1") == []
    problems = security.validate_password_strength("short")
    assert len(problems) >= 2


def test_tokens_are_random_and_stored_hashed():
    a, b = security.new_token(), security.new_token()
    assert a != b and len(a) >= 40
    assert security.tokens_match(a, security.hash_token(a))
    assert not security.tokens_match(b, security.hash_token(a))


def test_rate_limiter_window():
    limiter = RateLimiter()
    assert all(limiter.hit("k", 3, 60)[0] for _ in range(3))
    allowed, retry = limiter.hit("k", 3, 60)
    assert not allowed and retry > 0
    assert limiter.hit("other", 3, 60)[0]


def test_log_redaction():
    redactor = RedactingFilter(["my-db-password", "sk-abcdefghijklmnop"])
    text = redactor.redact("connect postgresql+psycopg://app:my-db-password@db/x password=hunter22 key sk-abcdefghijklmnop")
    assert "my-db-password" not in text and "hunter22" not in text and "sk-abcdefghijklmnop" not in text
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "token=%s", ("abc123secret",), None)
    redactor.filter(record)
    assert "abc123secret" not in record.getMessage()


def test_configuration_rejects_unsafe_values():
    with pytest.raises(ValidationError):
        Settings(cors_origins="*")
    with pytest.raises(ValidationError):
        Settings(llm_provider="anthropic", llm_model="")
    with pytest.raises(ValidationError):
        Settings(app_env="production", database_url="postgresql+psycopg://app:change-me-app@db/finsense")
    production = Settings(app_env="production")
    assert production.secure_cookies and not production.api_docs_enabled
    assert not Settings(app_env="development").secure_cookies


class FakeFund:
    def __init__(self, fid, code, basis="nav_growth"):
        self.id, self.scheme_code, self.return_basis = fid, code, basis


AURORA, NORTHSTAR = FakeFund(1, "FS-LC-001"), FakeFund(2, "FS-MC-002")


@pytest.mark.parametrize("question,funds,expected", [
    ("What is the 3-year Sharpe ratio of Aurora?", [AURORA], ["fund_metrics"]),
    ("Why has the risk of Aurora increased?", [AURORA], ["risk_change"]),
    ("Compare Aurora and Northstar over 5 years", [AURORA, NORTHSTAR], ["compare_funds"]),
    ("Forecast Aurora for next month", [AURORA], ["forecast"]),
    ("What does the factsheet say about the exit load?", [], []),
])
def test_rule_based_routing(question, funds, expected):
    plan = router.rule_based_plan(question, funds, [])
    assert [c.name for c in plan.calls] == expected
    assert plan.use_documents  # documents are always consulted; the evidence gate decides


def test_routing_extracts_periods_and_portfolios():
    plan = router.rule_based_plan("Compare Aurora and Northstar over 5 years", [AURORA, NORTHSTAR], [])
    assert plan.calls[0].args["period"] == "5y"
    plan = router.rule_based_plan("Optimise my portfolio for a conservative profile", [], ["Core"])
    assert plan.calls[0].name == "optimise_portfolio"
    assert plan.calls[0].args == {"portfolio": "Core", "risk_profile": "conservative"}


def test_llm_planner_output_is_validated(fake_llm):
    ok = router.plan_with_llm(fake_llm(plan='{"tools": [{"name": "fund_metrics", "args": {"fund": "FS-LC-001"}}]}'),
                              "q", [AURORA], [])
    assert ok and ok.calls[0].name == "fund_metrics" and ok.planner == "llm"
    # Unknown tools, funds the question never named, garbage output -> fall back (None).
    assert router.plan_with_llm(fake_llm(plan='{"tools": [{"name": "run_sql", "args": {}}]}'), "q", [AURORA], []) is None
    assert router.plan_with_llm(fake_llm(plan='{"tools": [{"name": "fund_metrics", "args": {"fund": "FS-XX-999"}}]}'),
                                "q", [AURORA], []) is None
    assert router.plan_with_llm(fake_llm(plan="I think you should buy"), "q", [AURORA], []) is None


def test_tool_registry_is_a_closed_whitelist():
    assert set(TOOLS) == {"fund_metrics", "risk_change", "compare_funds", "forecast", "portfolio_summary",
                          "optimise_portfolio"}
    assert ToolCall("fund_metrics", {"fund": "x"}).name in TOOLS
