#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import experiment_decisions as decisions


def experiment(**overrides):
    value = {
        "experiment_id": "exp-1",
        "decision_metric": "commission",
        "observation_unit": "content_id",
        "minimum_evidence": {"verified_impressions": 3000},
        "guardrail_metrics": ["compliance_violations", "returns"],
        "attribution_required": True,
        "direction": "maximize",
        "baseline": 4.0,
        "minimum_lift": 0.05,
    }
    value.update(overrides)
    return value


def test_missing_experiment_contract_is_insufficient_instead_of_guessing_a_threshold():
    result = decisions.evaluate({}, [])
    assert result["state"] == "insufficient"
    assert "missing_experiment_id" in result["reasons"]
    assert "missing_minimum_evidence" in result["reasons"]


def test_any_observed_compliance_violation_stops_immediately():
    observations = [{"content_id": "c1", "commission": None, "verified_impressions": 10, "attribution_complete": False, "compliance_violations": 1}]
    result = decisions.evaluate(experiment(), observations)
    assert result["state"] == "stop"
    assert result["reasons"] == ["compliance_violation"]


def test_revenue_experiment_with_views_but_no_retailer_outcome_stays_insufficient():
    observations = [{
        "content_id": "c1", "verified_impressions": 5000,
        "commission": None, "attribution_complete": False, "compliance_violations": 0,
    }]
    result = decisions.evaluate(experiment(), observations)
    assert result["state"] == "insufficient"
    assert set(result["reasons"]) == {"missing_decision_metric", "incomplete_attribution"}


def test_metric_specific_minimum_evidence_blocks_decision_even_when_metric_is_present():
    observations = [{
        "content_id": "c1", "verified_impressions": 2000,
        "commission": 10.0, "attribution_complete": True, "compliance_violations": 0,
    }]
    result = decisions.evaluate(experiment(), observations)
    assert result["state"] == "insufficient"
    assert result["reasons"] == ["minimum_evidence:verified_impressions:2000/3000"]


def test_attributed_metric_with_its_own_minimum_and_lift_can_expand():
    exp = experiment(
        decision_metric="landing_ctr", minimum_evidence={"landing_sessions": 200},
        baseline=0.05, minimum_lift=0.10,
    )
    observations = [
        {"content_id": "c1", "landing_sessions": 100, "landing_ctr": 0.06, "attribution_complete": True},
        {"content_id": "c2", "landing_sessions": 100, "landing_ctr": 0.065, "attribution_complete": True},
    ]
    result = decisions.evaluate(exp, observations)
    assert result["state"] == "expand"
    assert result["observed_metric"] == 0.0625
