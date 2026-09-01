# -*- coding: utf-8 -*-
"""보안 회귀 테스트: 비밀값은 로그·상태 파일에 남지 않는다."""
import json
import os
import stat

from common import append_jsonl, log, redact_secrets, write_json


SYNTHETIC_TOKEN = "synthetic-thread-token-123"


def test_log_redacts_query_string_token(capsys):
    log(f"request failed: https://graph.threads.net/x?fields=id&access_token={SYNTHETIC_TOKEN}")
    output = capsys.readouterr().out
    assert SYNTHETIC_TOKEN not in output
    assert "access_token=[REDACTED]" in output


def test_redact_secrets_handles_mapping_and_bearer_forms():
    text = (
        f"params={{'access_token': '{SYNTHETIC_TOKEN}'}} "
        f"Authorization: Bearer {SYNTHETIC_TOKEN}"
    )
    clean = redact_secrets(text)
    assert SYNTHETIC_TOKEN not in clean
    assert clean.count("[REDACTED]") >= 2


def test_state_writes_are_owner_only(tmp_path):
    json_path = tmp_path / "state.json"
    jsonl_path = tmp_path / "events.jsonl"

    write_json(json_path, {"ok": True})
    append_jsonl(jsonl_path, {"ok": True})

    assert json.loads(json_path.read_text())["ok"] is True
    assert json.loads(jsonl_path.read_text().splitlines()[0])["ok"] is True
    assert stat.S_IMODE(os.stat(json_path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(jsonl_path).st_mode) == 0o600
