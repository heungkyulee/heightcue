#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json

import evidence


def test_rebuild_used_in_uses_only_verified_real_root_publications(tmp_path):
    cfg = {"paths": {"state_dir": str(tmp_path)}}
    atoms = {"schema_version": 1, "atoms": [
        {"atom_id": "a1", "used_in": {"threads_kr": ["PREVIEW-old"]}, "performance": {}},
        {"atom_id": "a2", "used_in": {"threads_us": ["DRY-old"]}, "performance": {}},
    ]}
    (tmp_path / "insight_atoms.json").write_text(json.dumps(atoms), encoding="utf-8")
    publications = [
        {"country": "KR", "media_id": "PREVIEW-1", "meta": {"publish_status": "verified", "atom_id": "a1"}},
        {"country": "KR", "media_id": "m1", "meta": {"publish_status": "verified", "atom_id": "a1", "thread_part": 1}},
        {"country": "KR", "media_id": "m2", "meta": {"publish_status": "verified", "atom_id": "a1", "thread_part": 2}},
        {"country": "US", "media_id": "m3", "meta": {"publish_status": "verification_pending", "atom_id": "a2"}},
    ]
    (tmp_path / "published.jsonl").write_text("".join(json.dumps(row) + "\n" for row in publications), encoding="utf-8")
    result = evidence.rebuild_used_in_from_publications(cfg)
    saved = json.loads((tmp_path / "insight_atoms.json").read_text())
    assert saved["atoms"][0]["used_in"] == {"threads_kr": ["m1"]}
    assert saved["atoms"][1]["used_in"] == {}
    assert result == {"atoms": 2, "real_root_publications": 1, "unknown_atom_ids": []}
