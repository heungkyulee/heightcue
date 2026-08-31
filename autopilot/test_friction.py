import json

import friction


def signal(**overrides):
    base = {
        "friction_id": "fr-001", "market": "KR", "domain": "storage",
        "source_type": "external_complaint", "source_pointer": "https://example.test/post/1",
        "verbatim": "장난감 통을 비워야 아래 블록을 꺼낼 수 있어요",
        "recurrence": 4, "intensity": 3, "mechanisms": ["front_open_bin"],
        "lifecycle": "validated",
    }
    base.update(overrides)
    return base


def test_append_validates_deduplicates_and_round_trips(tmp_path):
    path = tmp_path / "friction.jsonl"
    row = friction.append_signal(path, signal())
    assert row["friction_id"] == "fr-001"
    assert friction.load_signals(path) == [row]
    try:
        friction.append_signal(path, signal())
    except ValueError as exc:
        assert "duplicate" in str(exc)
    else:
        raise AssertionError("duplicate accepted")


def test_category_only_or_missing_source_fails_closed(tmp_path):
    path = tmp_path / "friction.jsonl"
    for bad in (signal(source_type="category_rotation"), signal(source_pointer=""), signal(recurrence=0)):
        try:
            friction.append_signal(path, bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid signal accepted: {bad}")


def test_pick_prefers_validated_recurrence_and_intensity(tmp_path):
    path = tmp_path / "friction.jsonl"
    friction.append_signal(path, signal(friction_id="low", recurrence=2, intensity=2))
    friction.append_signal(path, signal(friction_id="high", recurrence=5, intensity=4))
    friction.append_signal(path, signal(friction_id="retired", recurrence=5, intensity=5, lifecycle="retired"))
    assert friction.pick_signal(path, "KR")["friction_id"] == "high"
