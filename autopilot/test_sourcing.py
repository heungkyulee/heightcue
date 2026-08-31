import sourcing


def candidate(**overrides):
    base = {
        "friction_id": "fr-1", "source_pointers": ["review:r1"],
        "scores": {
            "friction_frequency": 4, "friction_intensity": 4, "mechanism_clarity": 5,
            "mobile_demo_clarity": 4, "consideration_cost": 1, "price_resistance": 2,
            "review_evidence_strength": 4, "failure_mode_severity": 2,
            "compliance_cost": 1, "expected_commission_value": 3, "attribution_readiness": 5,
        },
        "requires_professional_advice": False, "high_risk_child_safety": False,
        "wrong_purchase_reversible": True, "creator_testimony_required": False,
        "health_outcome_primary": False,
    }
    base.update(overrides)
    return base


def test_low_consideration_score_is_inspectable_and_serializable():
    result = sourcing.score_candidate(candidate())
    assert result["eligible"] is True
    assert set(result["components"]) == set(candidate()["scores"])
    assert result["source_pointers"] == ["review:r1"]
    assert isinstance(result["final_score"], float)


def test_low_consideration_exclusions_fail_closed():
    for field in ("requires_professional_advice", "high_risk_child_safety", "creator_testimony_required", "health_outcome_primary"):
        assert sourcing.score_candidate(candidate(**{field: True}))["eligible"] is False
    assert sourcing.score_candidate(candidate(wrong_purchase_reversible=False))["eligible"] is False
    assert sourcing.score_candidate(candidate(source_pointers=[]))["eligible"] is False


def test_revenue_hierarchy_beats_views():
    viral = {"views": 1_000_000, "qualified_engagement": 5000, "progression": 100, "clicks": 0, "orders": 0, "commission": 0}
    revenue = {"views": 100, "qualified_engagement": 5, "progression": 2, "clicks": 1, "orders": 1, "commission": 3.5}
    assert sourcing.revenue_rank(revenue) > sourcing.revenue_rank(viral)
