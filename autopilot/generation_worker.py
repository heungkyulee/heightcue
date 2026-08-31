#!/usr/bin/env python3
"""Authoritative HeightCue generation service; accepts high-level tasks only."""
import base64
import hashlib
import json
import math
import os
import stat
import subprocess
import sys
import urllib.request
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from generation_ssot import TASK_DIRECTIVES, canonical, digest, resolve_inputs, source_context

TASKS = set(TASK_DIRECTIVES)
TOURNAMENT_TASKS = {"sales_post", "value_post"}


def atomic(path, data, mode=0o600):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data); handle.flush(); os.fsync(handle.fileno())
        os.replace(tmp, path); os.chmod(path, mode)
    finally:
        if tmp.exists(): tmp.unlink()


def keypair(keydir):
    directory = Path(keydir); private = directory / "private.pem"; ring = directory / "public_keys.json"
    if private.exists():
        if stat.S_IMODE(private.stat().st_mode) != 0o600:
            raise RuntimeError("private key mode must be 0600")
        key = serialization.load_pem_private_key(private.read_bytes(), password=None)
    else:
        key = Ed25519PrivateKey.generate()
        atomic(private, key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    key_id = hashlib.sha256(public).hexdigest()[:16]
    try: keys = json.loads(ring.read_text()) if ring.exists() else {}
    except Exception as exc: raise RuntimeError(f"corrupt public key ring: {exc}") from exc
    keys[key_id] = base64.b64encode(public).decode("ascii"); atomic(ring, canonical(keys), 0o644)
    return key, key_id


def _fixture_call(executable, request):
    completed = subprocess.run([executable], input=json.dumps(request, ensure_ascii=False)+"\n",
                               text=True, capture_output=True, check=True)
    return json.loads(completed.stdout.splitlines()[-1])


def _api_call(cfg, model, system_prompt, payload):
    body = {"model": model, "messages": [
        {"role":"system", "content":system_prompt},
        {"role":"user", "content":json.dumps(payload, ensure_ascii=False)},
    ], "response_format":{"type":"json_object"}}
    request = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions",
        data=canonical(body), headers={"Authorization":"Bearer "+cfg["openrouter"]["api_key"],
        "Content-Type":"application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=90) as response:
        envelope = json.loads(response.read())
    return json.loads(envelope["choices"][0]["message"]["content"])


def invoke(fixture, cfg, phase, task, model, prompt, payload):
    request = {"phase":phase, "task":task, "model":model, "payload":payload}
    if fixture:
        return _fixture_call(fixture, request)
    return _api_call(cfg, model, prompt, payload)


def _strings(value, minimum=1, maximum=None):
    return (isinstance(value, list) and len(value) >= minimum and
            (maximum is None or len(value) <= maximum) and
            all(isinstance(x, str) and x.strip() for x in value))


def validate_result(task, result):
    if not isinstance(result, dict): raise RuntimeError("task output must be an object")
    if task == "sales_master":
        if not _strings(result.get("hooks")) or not _strings(result.get("verified_points")):
            raise RuntimeError("invalid sales_master output")
    elif task == "sales_hooks":
        hooks = result.get("hooks")
        if not _strings(hooks, 6, 6) or len({x.strip() for x in hooks}) != 6:
            raise RuntimeError("invalid sales_hooks output")
    elif task in ("sales_post", "value_post"):
        if not isinstance(result.get("text"), str) or not result["text"].strip():
            raise RuntimeError(f"invalid {task} output")
    elif task == "value_thread":
        if not _strings(result.get("parts"), 2, 4): raise RuntimeError("invalid value_thread output")
    elif task == "comment_reply":
        if not all(isinstance(result.get(k), str) and result[k].strip() for k in ("category","action","reason")):
            raise RuntimeError("invalid comment_reply output")
        if result["action"] == "reply" and not (isinstance(result.get("text"), str) and result["text"].strip()):
            raise RuntimeError("reply action requires text")
    return result


def bind_friction_contract(task, country, resolved, result, stage=None):
    """Bind authoritative source identities to output; model fields are never trusted."""
    row = dict(result)
    source = resolved[0] if resolved else {}
    if task in {"value_post", "value_thread"}:
        active_stage = stage or "discovery"
        if active_stage not in {"discovery", "bridge"}:
            raise RuntimeError("invalid non-commercial friction stage")
        pointers = source.get("source_pointers") or [source.get("source_pointer")]
        row.update({"friction_id": source.get("friction_id"), "stage": active_stage,
                    "market": country, "source_pointers": [x for x in pointers if x]})
    elif task == "sales_post":
        disclosure = "#ad" if country == "US" else "이 포스팅은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다."
        row.update({"friction_id": source.get("friction_id"), "stage": "verdict",
                    "market": country, "source_pointers": source.get("source_pointers") or [],
                    "mechanism": source.get("mechanism"), "failure_mode": source.get("failure_mode"),
                    "skip_if": source.get("skip_if"), "attributable_route": source.get("link"),
                    "disclosure": disclosure})
    if task in {"value_post", "value_thread", "sales_post"}:
        required = ("friction_id", "stage", "market", "source_pointers")
        if any(row.get(key) in (None, "", []) for key in required):
            raise RuntimeError("incomplete friction-stage output")
        if task == "sales_post" and any(not row.get(key) for key in
                ("mechanism", "failure_mode", "skip_if", "attributable_route", "disclosure")):
            raise RuntimeError("incomplete verdict output")
    return row


def validate_candidates(raw):
    candidates = raw.get("candidates") if isinstance(raw, dict) else None
    if not isinstance(candidates, list) or len(candidates) < 2: raise RuntimeError("writer must return at least two candidates")
    ids = []
    for item in candidates:
        if not isinstance(item, dict) or set(item) != {"id", "text"}: raise RuntimeError("invalid candidate schema")
        if not isinstance(item["id"], str) or not item["id"].strip() or not isinstance(item["text"], str) or not item["text"].strip():
            raise RuntimeError("invalid candidate values")
        ids.append(item["id"])
    if len(set(ids)) != len(ids): raise RuntimeError("candidate ids must be unique")
    return candidates


def select_candidate(candidates, critic):
    scores = critic.get("scores") if isinstance(critic, dict) else None
    if not isinstance(scores, list) or len(scores) != len(candidates): raise RuntimeError("invalid critic response")
    candidate_ids = {item["id"] for item in candidates}; seen = set(); normalized = []
    for row in scores:
        if (not isinstance(row, dict)
                or set(row) != {"id", "score", "disqualified", "reason"}
                or row["id"] not in candidate_ids or row["id"] in seen
                or not isinstance(row["disqualified"], bool)
                or not isinstance(row["reason"], str) or not row["reason"].strip()):
            raise RuntimeError("invalid critic score schema")
        if isinstance(row["score"], bool) or not isinstance(row["score"], (int, float)) or not math.isfinite(float(row["score"])):
            raise RuntimeError("critic scores must be finite numbers")
        seen.add(row["id"])
        if not row["disqualified"]:
            normalized.append((float(row["score"]), row["id"]))
    if seen != candidate_ids: raise RuntimeError("critic ids must exactly match candidates")
    if not normalized: raise RuntimeError("all candidates disqualified by grounded critic")
    winner_id = sorted(normalized, key=lambda item: (-item[0], item[1]))[0][1]
    return {"text": next(item["text"] for item in candidates if item["id"] == winner_id)}


def output_digests(result):
    values = result.get("parts") if isinstance(result.get("parts"), list) else [result.get("text")]
    return [hashlib.sha256(str(value).encode("utf-8")).hexdigest() for value in values if value is not None]


def run(capability):
    root, keydir, fixture = capability["root"], capability["keydir"], capability.get("fixture")
    key, key_id = keypair(keydir)
    for line in sys.stdin:
        try:
            request = json.loads(line)
            if not {"task","country","input_ids"} <= set(request) or set(request) - {"task","country","input_ids","stage"}:
                raise RuntimeError("invalid request fields")
            task = request["task"]
            if task not in TASKS: raise RuntimeError("unsupported task")
            manifest, cfg, prompt, source_hashes = source_context(root, task)
            if request["country"] not in manifest["countries"]:
                raise RuntimeError("unsupported execution country")
            allow_rehearsal = capability.get("rehearsal") is True
            resolved = resolve_inputs(
                root, task, request["input_ids"], allow_rehearsal=allow_rehearsal
            )
            stage = request.get("stage")
            user_payload = {"task":task, "country":request["country"], "stage": stage,
                            "resolved_payload":resolved}
            writer_model = cfg["openrouter"]["model"]
            raw = invoke(fixture, cfg, "writer", task, writer_model, prompt.decode("utf-8"), user_payload)
            critic_status, critic_model = "not_run", None
            if task in TOURNAMENT_TASKS:
                candidates = validate_candidates(raw)
                critic_model = str(cfg["openrouter"].get("critic_model") or writer_model).strip()
                critic_payload = {"task":task, "country":request["country"],
                                  "source_of_truth": resolved,
                                  "candidates":[{"id":x["id"],"text":x["text"]} for x in candidates]}
                critic_prompt = ('You are HeightCue\'s grounded blind revenue critic. Judge only the finished text; '
                                   'angle labels and writer rationale are intentionally absent. The source_of_truth is '
                                   'the complete factual boundary. Return only JSON in exactly this shape: '
                                   '{"scores":[{"id":"candidate-id","score":0.0,"disqualified":false,'
                                   '"reason":"brief factual reason"}]}. Copy every supplied candidate id exactly once. '
                                   'Set disqualified=true for ANY invented number, price, ingredient, product fact, '
                                   'family history, purchase/use experience, medical fact, comparison, prevalence '
                                   'claim (including phrases such as half the time, often, or many), or first-person '
                                   'memory not explicitly present in source_of_truth. The fixed operator persona '
                                   '(26-year-old man who stopped at 167cm / 5\'6) is trusted context, but no other '
                                   'personal memory is. Also disqualify fake DM claims, teacher-style wrap-ups, '
                                   'AI essay cadence, moral conclusions, and content that advances no link in '
                                   'reach -> follow/trust -> commercial click -> purchase. A polished fabrication '
                                   'must lose to a plain grounded draft. Among non-disqualified drafts, score scroll '
                                   'stop, specificity, credible trust, buyer relevance, and commercial pathway. '
                                   'Do not return a winner field or prose.')
                critic = invoke(fixture, cfg, "critic", task, critic_model, critic_prompt, critic_payload)
                result = select_candidate(candidates, critic); critic_status = "verified"
            else:
                result = raw
            result = bind_friction_contract(task, request["country"], resolved, result, stage)
            validate_result(task, result)
            attested = {"schema_version":1,"task":task,"country":request["country"],"input_ids":request["input_ids"],
                "execution_scope":"rehearsal" if allow_rehearsal else "production",
                "output_digests":output_digests(result),"prompt_digest":hashlib.sha256(prompt).hexdigest(),
                "input_payload_digest":digest(resolved),"source_hashes":source_hashes,"model":writer_model,
                "critic_status":critic_status,"critic_model":critic_model,"stage":result.get("stage")}
            signature = key.sign(canonical(attested))
            result["_attestation"] = {"key_id":key_id,"payload":attested,"signature":base64.b64encode(signature).decode("ascii")}
            print(json.dumps({"ok":True,"result":result},ensure_ascii=False),flush=True)
        except Exception as exc:
            print(json.dumps({"ok":False,"error":str(exc)}),flush=True)


if __name__ == "__main__":
    fd = int(os.environ.pop("HEIGHTCUE_CAP_FD")); capability = json.loads(os.read(fd, 65536)); os.close(fd); run(capability)
