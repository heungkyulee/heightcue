from pathlib import Path

import common

ROOT = Path(__file__).resolve().parents[1]
ACTIVE = [
    "context/persona.md", "context/voice-kr.md", "context/voice-us.md",
    "heightcue-gemini-skills.md", "autopilot/viral_intelligence.py",
    "autopilot/generation_worker.py",
]
BANNED = ("167cm", "5'6", "26-year-old", "short uncle", "raw_memory", "story-bank.md")


def test_active_generation_contract_has_no_retired_identity_tokens():
    offenders = {}
    for relative in ACTIVE:
        text = (ROOT / relative).read_text(encoding="utf-8").lower()
        hits = [token for token in BANNED if token.lower() in text]
        if hits:
            offenders[relative] = hits
    assert offenders == {}


def test_prompt_assembly_uses_editorial_compatibility_context_not_story_archive(tmp_path):
    root = tmp_path
    (root / "context").mkdir()
    (root / "autopilot").mkdir()
    for name in ("user-intent-contract.md", "compliance.md", "persona.md", "voice-kr.md"):
        (root / "context" / name).write_text(f"ACTIVE {name}", encoding="utf-8")
    (root / "heightcue-gemini-skills.md").write_text("## SKILL V1\nACTIVE SKILL", encoding="utf-8")
    (root / "story-bank.md").write_text("RETIRED SECRET BIOGRAPHY", encoding="utf-8")
    old_base = common.BASE
    common.BASE = str(root / "autopilot")
    try:
        prompt = common.load_skill({"paths": {"skills": "../heightcue-gemini-skills.md"}}, "V1", "KR")
    finally:
        common.BASE = old_base
    assert "ACTIVE persona.md" in prompt
    assert "RETIRED SECRET BIOGRAPHY" not in prompt
