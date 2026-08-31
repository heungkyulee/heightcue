#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Metric-specific HeightCue experiment decisions."""

from __future__ import annotations


_REQUIRED = (
    "experiment_id",
    "decision_metric",
    "observation_unit",
    "minimum_evidence",
    "guardrail_metrics",
    "attribution_required",
)


def evaluate(experiment: dict, observations: list[dict]) -> dict:
    if any(int(row.get("compliance_violations") or 0) > 0 for row in observations):
        return {"state": "stop", "reasons": ["compliance_violation"], "observed": len(observations)}
    reasons = [f"missing_{field}" for field in _REQUIRED if experiment.get(field) in (None, "", [], {})]
    if reasons:
        return {"state": "insufficient", "reasons": reasons, "observed": 0}
    metric = experiment["decision_metric"]
    evidence_reasons = []
    if not observations or any(row.get(metric) is None for row in observations):
        evidence_reasons.append("missing_decision_metric")
    if experiment.get("attribution_required") and any(not row.get("attribution_complete") for row in observations):
        evidence_reasons.append("incomplete_attribution")
    if evidence_reasons:
        return {"state": "insufficient", "reasons": evidence_reasons, "observed": len(observations)}
    minimum_reasons = []
    for field, threshold in experiment["minimum_evidence"].items():
        observed_value = len(observations) if field == "observations" else sum(
            float(row[field]) for row in observations if row.get(field) is not None
        )
        if observed_value < float(threshold):
            shown = int(observed_value) if float(observed_value).is_integer() else observed_value
            minimum_reasons.append(f"minimum_evidence:{field}:{shown}/{threshold}")
    if minimum_reasons:
        return {"state": "insufficient", "reasons": minimum_reasons, "observed": len(observations)}
    values = [float(row[metric]) for row in observations]
    observed_metric = sum(values) / len(values)
    baseline = experiment.get("baseline")
    minimum_lift = float(experiment.get("minimum_lift") or 0)
    direction = experiment.get("direction")
    if baseline is None or direction not in {"maximize", "minimize"}:
        return {"state": "continue", "reasons": ["baseline_or_direction_missing"],
                "observed": len(observations), "observed_metric": observed_metric}
    baseline = float(baseline)
    if direction == "maximize":
        if observed_metric >= baseline * (1 + minimum_lift):
            state = "expand"
        elif observed_metric <= baseline * (1 - minimum_lift):
            state = "modify"
        else:
            state = "continue"
    else:
        if observed_metric <= baseline * (1 - minimum_lift):
            state = "expand"
        elif observed_metric >= baseline * (1 + minimum_lift):
            state = "modify"
        else:
            state = "continue"
    return {"state": state, "reasons": [], "observed": len(observations),
            "observed_metric": observed_metric}
