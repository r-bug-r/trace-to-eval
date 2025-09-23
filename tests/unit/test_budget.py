"""Budget limits, consent gates, and the offline pricing table."""

from __future__ import annotations

import pytest

from trace_to_eval.budget import BudgetLimits, BudgetTracker, require_live_consent
from trace_to_eval.errors import BudgetExceeded, ConfigError, LiveCallsDisabled
from trace_to_eval.pricing import Price, Pricing


def test_consent_requires_flag_environment_and_positive_caps() -> None:
    safe = BudgetLimits(max_cost_usd_per_run=1.0, max_requests_per_run=10)
    with pytest.raises(LiveCallsDisabled):
        require_live_consent(allow_live_flag=False, environment_value="1", limits=safe)
    with pytest.raises(LiveCallsDisabled):
        require_live_consent(allow_live_flag=True, environment_value=None, limits=safe)
    with pytest.raises(LiveCallsDisabled):
        require_live_consent(allow_live_flag=True, environment_value="true", limits=safe)
    with pytest.raises(LiveCallsDisabled, match="positive --max-cost-usd"):
        require_live_consent(
            allow_live_flag=True, environment_value="1", limits=BudgetLimits(max_requests_per_run=5)
        )
    with pytest.raises(LiveCallsDisabled, match="--max-requests"):
        require_live_consent(
            allow_live_flag=True,
            environment_value="1",
            limits=BudgetLimits(max_cost_usd_per_run=1.0),
        )
    require_live_consent(allow_live_flag=True, environment_value="1", limits=safe)


def test_budget_limits_reject_invalid_values() -> None:
    with pytest.raises(ConfigError):
        BudgetLimits(max_requests_per_run=-1)
    with pytest.raises(ConfigError):
        BudgetLimits(request_timeout_s=0)
    with pytest.raises(ConfigError, match="unknown budget fields"):
        BudgetLimits.from_mapping({"max_requests": 1})


def test_tracker_enforces_request_and_cost_caps_before_calls() -> None:
    tracker = BudgetTracker(BudgetLimits(max_requests_per_run=1, max_cost_usd_per_run=0.01))
    tracker.check(estimated_input_tokens=10, estimated_cost_usd=0.005)
    tracker.record(
        input_tokens=10,
        output_tokens=5,
        cost_usd=0.005,
        success=True,
    )
    assert tracker.requests == 1
    assert tracker.cost_usd == pytest.approx(0.005)

    with pytest.raises(BudgetExceeded, match="request budget"):
        tracker.check(estimated_input_tokens=10, estimated_cost_usd=0.001)
    assert tracker.stopped_reason == "max_requests_per_run"


def test_token_budget_counts_estimated_input() -> None:
    tracker = BudgetTracker(BudgetLimits(max_tokens_per_run=100))
    tracker.record(input_tokens=80, output_tokens=10, cost_usd=0.0, success=True)
    with pytest.raises(BudgetExceeded, match="token budget"):
        tracker.check(estimated_input_tokens=20, estimated_cost_usd=0.0)


def test_daily_and_monthly_caps_count_prior_spend() -> None:
    tracker = BudgetTracker(BudgetLimits(daily_cost_cap_usd=1.0, monthly_cost_cap_usd=2.0))
    tracker.daily_spend_usd = 0.9
    tracker.monthly_spend_usd = 0.5
    with pytest.raises(BudgetExceeded, match="daily cost cap"):
        tracker.check(estimated_input_tokens=10, estimated_cost_usd=0.2)


def test_record_never_subtracts_and_keeps_failures() -> None:
    tracker = BudgetTracker(BudgetLimits())
    tracker.record(
        input_tokens=-5,
        output_tokens=-1,
        cost_usd=-1.0,
        success=False,
        error_class="timeout",
    )
    assert tracker.input_tokens == 0
    assert tracker.cost_usd == 0.0
    assert tracker.requests == 1
    assert tracker.summary()["requests"] == 1


def test_pricing_uses_model_specific_rates_and_a_documented_default() -> None:
    pricing = Pricing(
        models={"expensive": Price(input_per_1k=0.01, output_per_1k=0.03)},
        default=Price(input_per_1k=0.001, output_per_1k=0.002),
        as_of="2026-10-01",
    )
    assert pricing.estimate("expensive", 1000, 1000) == pytest.approx(0.04)
    assert pricing.estimate("unknown", 1000, 500) == pytest.approx(0.002)
    assert pricing.price_for("unknown").input_per_1k == 0.001
    assert pricing.price_for("expensive-preview").input_per_1k == 0.01


def test_bundled_pricing_table_loads() -> None:
    pricing = Pricing.load()
    assert pricing.as_of
    price = pricing.price_for("gpt-4o-mini")
    assert price.input_per_1k >= 0
    assert price.output_per_1k >= 0
