# -*- coding: utf-8 -*-
"""HeightCue 생성·검사·발행 경로가 공유하는 버전형 실행 계약."""
from __future__ import annotations

import hashlib

import json
import os
import threading
from typing import Any, Dict, Iterable, Mapping, Optional

BASE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE)
DEFAULT_MANIFEST = "context/execution-contract.json"
_REQUIRED = (
    "schema_version", "contract_id", "owner_profile", "execution_mode",
    "business_kpi", "intent_source", "prompt_sources", "model_source",
    "validator", "publisher", "tasks", "countries",
)
_CODE_OWNED = {
    "schema_version": 1,
    "contract_id": "heightcue-content-v1",
    "owner_profile": "jaehyun-publisher",
    "execution_mode": "script_only",
    "intent_source": "context/user-intent-contract.md",
    "model_source": "runtime_config:openrouter.model",
    "validator": "post_check.check_post",
    "publisher": "publish.publish_text",
}
_TASK_INPUT_SCHEMAS = {
    "sales_master": (("product:", "queue_product:"),),
    "sales_hooks": (("product:", "queue_product:"),),
    "sales_post": (("product:", "queue_product:"),),
    "value_post": ("friction:",),
    "value_thread": ("friction:",),
    "comment_reply": ("comment:", "post:"),
}
_WORKER_LOCK = threading.Lock()



def _bind_generated_result(result, provenance, critic_status=None, critic_model=None):
    """Legacy in-process test adapter; never produces a production-valid signature."""
    if not isinstance(result, dict):
        return result
    bound = dict(provenance)
    if not bound.pop("_test_mode", False):
        raise ContractError("only the authoritative generation service can attest output")
    bound["critic_status"] = critic_status if critic_status is not None else bound.get("critic_status", "not_run")
    if bound["critic_status"] == "verified":
        bound["critic_model"] = str(critic_model)
    bound["content_digests"] = [hashlib.sha256(x.encode()).hexdigest() for x in _result_texts(result)]
    bound["generation_receipt"] = "TEST-ONLY-NOT-A-SIGNATURE"
    return {**result, "_provenance": bound}


def _verify_receipt(provenance):
    return provenance.get("generation_receipt") == "TEST-ONLY-NOT-A-SIGNATURE"


class ContractError(RuntimeError):
    """계약을 완전히 설명할 수 없어 실행을 중단한다."""


def _root(cfg: Mapping[str, Any]) -> str:
    if cfg.get("_testing") is True:
        return os.path.abspath(os.path.expanduser(str(cfg.get("_project_root") or PROJECT_ROOT)))
    return PROJECT_ROOT


def _relative_path(cfg: Mapping[str, Any], relative: str) -> str:
    path = os.path.abspath(os.path.join(_root(cfg), relative))
    root = _root(cfg) + os.sep
    if path != root.rstrip(os.sep) and not path.startswith(root):
        raise ContractError(f"contract source escapes project root: {relative}")
    return path


def load_manifest(cfg: Mapping[str, Any]) -> Dict[str, Any]:
    relative = DEFAULT_MANIFEST
    if cfg.get("_testing") is True:
        relative = str((cfg.get("paths") or {}).get("contract_manifest") or DEFAULT_MANIFEST)
    path = _relative_path(cfg, relative)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as exc:
        raise ContractError(f"cannot load contract manifest {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ContractError(f"contract manifest must be an object: {path}")
    missing = [key for key in _REQUIRED if key not in data]
    if missing:
        raise ContractError(f"contract manifest missing fields {missing}: {path}")
    if not isinstance(data["prompt_sources"], list) or not data["prompt_sources"]:
        raise ContractError("contract prompt_sources must be a non-empty list")
    for key, expected in _CODE_OWNED.items():
        if data.get(key) != expected:
            raise ContractError(
                f"contract {key} must be code-owned value {expected!r}, got {data.get(key)!r}")
    if not isinstance(data["tasks"], list) or not data["tasks"]:
        raise ContractError("contract tasks must be a non-empty list")
    unsupported = [task for task in data["tasks"] if task not in _TASK_INPUT_SCHEMAS]
    if unsupported:
        raise ContractError(f"unsupported execution tasks in manifest: {unsupported}")
    if not isinstance(data["countries"], list) or not data["countries"]:
        raise ContractError("contract countries must be a non-empty list")
    return data


def _sha256(path: str) -> str:
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except Exception as exc:
        raise ContractError(f"cannot hash contract source {path}: {exc}") from exc


def build_context(cfg: Mapping[str, Any], task: str, country: str,
                  runtime_context: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """활성 모델과 정본 파일 해시만 담은 안전한 생성 계약 패킷."""
    del runtime_context  # private/runtime bodies must never enter durable provenance
    manifest = load_manifest(cfg)
    runtime_cfg = cfg
    if cfg.get("_testing") is not True:
        config_path = os.path.join(PROJECT_ROOT, "autopilot", "config.json")
        try:
            with open(config_path, encoding="utf-8") as fh:
                runtime_cfg = json.load(fh)
        except Exception as exc:
            raise ContractError(f"cannot load trusted runtime config: {exc}") from exc
    model = str((runtime_cfg.get("openrouter") or {}).get("model") or "").strip()
    if not model:
        raise ContractError("runtime config is missing openrouter.model")
    sources = [manifest["intent_source"], *manifest["prompt_sources"]]
    hashes: Dict[str, str] = {}
    for relative in sources:
        rel = str(relative)
        hashes[rel] = _sha256(_relative_path(cfg, rel))
    packet = {
        "schema_version": manifest["schema_version"],
        "contract_id": manifest["contract_id"],
        "owner_profile": manifest["owner_profile"],
        "execution_mode": manifest["execution_mode"],
        "business_kpi": manifest["business_kpi"],
        "task": str(task),
        "country": str(country).upper(),
        "model": model,
        "model_source": manifest["model_source"],
        "validator": manifest["validator"],
        "publisher": manifest["publisher"],
        "source_hashes": hashes,
        "_testing": cfg.get("_testing") is True,
    }
    return packet


def generation_provenance(packet: Mapping[str, Any],
                          input_ids: Optional[Iterable[Any]] = None) -> Dict[str, Any]:
    """본문·비밀 없이 생성 경로를 재현할 수 있는 compact metadata."""
    keys = (
        "schema_version", "contract_id", "owner_profile", "execution_mode",
        "task", "country", "model", "model_source", "validator", "publisher",
        "source_hashes",
    )
    out = {key: packet[key] for key in keys}
    out["critic_status"] = "not_run"
    if packet.get("_testing") is True:
        out["_test_mode"] = True
    ids = [str(item) for item in (input_ids or []) if item is not None and str(item)]
    if ids:
        out["input_ids"] = ids
    _validate_input_ids(str(packet["task"]), ids)
    return out


def _validate_input_ids(task: str, ids: list[str]) -> None:
    schema = _TASK_INPUT_SCHEMAS.get(task)
    if schema is None:
        raise ContractError(f"unsupported execution task: {task}")
    if len(ids) != len(schema):
        raise ContractError(f"{task} requires exactly {len(schema)} input identities")
    for value, prefixes in zip(ids, schema):
        allowed = prefixes if isinstance(prefixes, tuple) else (prefixes,)
        if not value.startswith(allowed):
            raise ContractError(f"{task} input identity must start with {allowed}")


def _result_texts(result: Mapping[str, Any]) -> list[str]:
    if isinstance(result.get("parts"), list):
        return [str(item) for item in result["parts"] if str(item)]
    text = str(result.get("text") or "")
    return [text] if text else []


def validate_provenance(cfg: Mapping[str, Any], provenance: Mapping[str, Any],
                        country: str, text: Optional[str] = None) -> Dict[str, Any]:
    """Caller metadata를 신뢰하지 않고 활성 계약과 필드별로 대조한다."""
    if not isinstance(provenance, Mapping):
        raise ContractError("execution provenance must be an object")
    manifest = load_manifest(cfg)
    task = str(provenance.get("task") or "")
    if task not in manifest["tasks"]:
        raise ContractError(f"undeclared execution task: {task!r}")
    active_country = str(country).upper()
    if active_country not in [str(item).upper() for item in manifest["countries"]]:
        raise ContractError(f"undeclared execution country: {active_country!r}")
    expected = generation_provenance(
        build_context(cfg, task, active_country),
        input_ids=provenance.get("input_ids") or [])
    for key, value in expected.items():
        if key in ("critic_status", "_test_mode"):
            continue
        if provenance.get(key) != value:
            raise ContractError(f"execution provenance mismatch: {key}")
    input_ids = provenance.get("input_ids")
    if input_ids is not None and (
            not isinstance(input_ids, list)
            or not all(isinstance(item, str) and item for item in input_ids)):
        raise ContractError("execution provenance input_ids must be non-empty strings")
    _validate_input_ids(task, list(input_ids or []))
    critic_status = provenance.get("critic_status")
    if critic_status not in ("verified", "failed", "not_run"):
        raise ContractError("execution provenance critic_status invalid")
    if critic_status == "verified":
        if not str(provenance.get("critic_model") or "").strip():
            raise ContractError("verified critic requires critic_model")
    elif provenance.get("critic_model") is not None:
        raise ContractError("critic_model requires verified critic response")
    if task in {"sales_post", "value_post"} and critic_status != "verified":
        raise ContractError(f"{task} requires verified critic result")
    if not _verify_receipt(provenance):
        raise ContractError("execution provenance generation receipt mismatch")
    digests = provenance.get("content_digests")
    if not isinstance(digests, list) or not digests:
        raise ContractError("execution provenance content digests missing")
    if text is not None:
        digest = hashlib.sha256(str(text).encode("utf-8")).hexdigest()
        if digest not in digests:
            raise ContractError("execution provenance content digest mismatch")
    return dict(provenance)


def validate_runtime(cfg: Mapping[str, Any]) -> Dict[str, Any]:
    """모든 선언 task가 같은 source/model 계약으로 조립되는지 시작 전에 검증한다."""
    manifest = load_manifest(cfg)
    tasks = manifest.get("tasks") or []
    if not tasks:
        raise ContractError("execution contract tasks가 비어 있음")
    countries = manifest.get("countries") or ["KR"]
    model = None
    for task in tasks:
        schema = _TASK_INPUT_SCHEMAS.get(str(task))
        if schema is None:
            raise ContractError(f"unsupported execution task: {task}")
        sample_ids = []
        for prefixes in schema:
            allowed = prefixes if isinstance(prefixes, tuple) else (prefixes,)
            sample_ids.append(f"{allowed[0]}runtime-validation")
        _validate_input_ids(str(task), sample_ids)
        for country in countries:
            packet = build_context(cfg, str(task), str(country))
            model = model or packet["model"]
    return {
        "contract_id": manifest["contract_id"],
        "tasks": list(tasks),
        "countries": list(countries),
        "model": str(model or ""),
    }


def merge_provenance(meta: Optional[Mapping[str, Any]],
                     provenance: Mapping[str, Any]) -> Dict[str, Any]:
    out = dict(meta or {})
    out["execution_contract"] = dict(provenance)
    return out

# authoritative generation client / local Ed25519 verifier
import base64 as _b64
import subprocess
import sys
import tempfile
from pathlib import Path as _Path
from cryptography.exceptions import InvalidSignature as _InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey as _Ed25519PublicKey
from generation_ssot import digest as _input_digest, resolve_inputs as _resolve_inputs, source_context as _source_context

_SERVICE = None
_SERVICE_ID = None
DEFAULT_KEY_DIR = "/Users/leeheungkyu/.hermes/runtime/heightcue-attestor"

def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")

def sha256_text(text):
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()

def stop_generation_service():
    global _SERVICE, _SERVICE_ID
    if _SERVICE is not None:
        try: _SERVICE.stdin.close()
        except Exception: pass
        try: _SERVICE.wait(timeout=2)
        except Exception: _SERVICE.kill()
        for stream in (_SERVICE.stdout, _SERVICE.stderr):
            if stream is not None:
                try: stream.close()
                except Exception: pass
    _SERVICE = None; _SERVICE_ID = None

def _start_authoritative_service(root, keydir, fixture=None, rehearsal=False):
    global _SERVICE, _SERVICE_ID
    identity=(root,keydir,fixture,bool(rehearsal))
    if _SERVICE is not None and _SERVICE.poll() is None and _SERVICE_ID == identity: return
    stop_generation_service()
    rfd,wfd=os.pipe()
    cap={"root":root,"keydir":keydir,"rehearsal":bool(rehearsal)}
    if fixture is not None: cap["fixture"]=fixture
    env=dict(os.environ); env["HEIGHTCUE_CAP_FD"]=str(rfd)
    if fixture is not None:
        # The fixture boundary is test-only; it may resolve its local product
        # fixture without weakening the production Company OS fail-closed path.
        env["HEIGHTCUE_TEST_FIXTURE"] = "1"
    _SERVICE=subprocess.Popen([sys.executable,os.path.join(BASE,"generation_worker.py")],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True,bufsize=1,env=env,pass_fds=(rfd,))
    os.close(rfd); os.write(wfd,_canonical(cap)); os.close(wfd); _SERVICE_ID=identity

def _validate_fixture_boundary(project_root, key_dir, fixture):
    if fixture is None:
        return
    root = os.path.realpath(project_root); keys = os.path.realpath(os.path.expanduser(key_dir))
    if root == os.path.realpath(PROJECT_ROOT) or keys == os.path.realpath(DEFAULT_KEY_DIR):
        raise ContractError("test fixture cannot use a production project root or key directory")
    temp_root = os.path.realpath(tempfile.gettempdir()) + os.sep
    if not root.startswith(temp_root) or not keys.startswith(temp_root):
        raise ContractError("test fixture requires temporary project root and key directory")
    executable = os.path.realpath(str(fixture))
    if not os.path.isfile(executable) or not os.access(executable, os.X_OK):
        raise ContractError("test fixture must be an executable file")

def request_authoritative_generation(task, country, input_ids, *, project_root=PROJECT_ROOT, key_dir=DEFAULT_KEY_DIR, test_fixture_executable=None, rehearsal=False, stage=None):
    _validate_input_ids(str(task), list(input_ids))
    _validate_fixture_boundary(project_root, key_dir, test_fixture_executable)
    manifest, _, _, _ = _source_context(project_root, str(task))
    normalized_country = str(country).upper()
    if normalized_country not in manifest["countries"]:
        raise ContractError(f"unsupported execution country: {normalized_country}")
    _start_authoritative_service(os.path.abspath(project_root),os.path.abspath(os.path.expanduser(key_dir)),test_fixture_executable,rehearsal=bool(rehearsal))
    request={"task":str(task),"country":normalized_country,"input_ids":list(input_ids)}
    if stage is not None:
        request["stage"] = str(stage)
    with _WORKER_LOCK:
        _SERVICE.stdin.write(json.dumps(request,ensure_ascii=False)+"\n"); _SERVICE.stdin.flush(); line=_SERVICE.stdout.readline()
    if not line: raise ContractError("authoritative generation service exited")
    response=json.loads(line)
    if not response.get("ok"): raise ContractError(response.get("error") or "authoritative generation failed")
    return response["result"]

def _resolved_context(project_root, task, country, input_ids, allow_rehearsal=False):
    del country
    _, cfg, prompt, hashes = _source_context(project_root, task)
    resolved = _resolve_inputs(project_root, task, input_ids, allow_rehearsal=allow_rehearsal)
    return hashlib.sha256(prompt).hexdigest(), hashes, cfg["openrouter"]["model"], _input_digest(resolved)

def verify_attestation(attestation, result, *, project_root=None, key_dir=None,
                       expected_country=None, expected_rehearsal=False):
    project_root = PROJECT_ROOT if project_root is None else project_root
    key_dir = DEFAULT_KEY_DIR if key_dir is None else key_dir
    try:
        payload=attestation["payload"]; kid=attestation["key_id"]
        ring=json.loads((_Path(key_dir)/"public_keys.json").read_text())
        pub=_Ed25519PublicKey.from_public_bytes(_b64.b64decode(ring[kid],validate=True))
        pub.verify(_b64.b64decode(attestation["signature"],validate=True),_canonical(payload))
        manifest, _, _, _ = _source_context(project_root, payload["task"])
        if payload["country"] not in manifest["countries"]:
            return False
        if expected_country is not None and payload["country"] != str(expected_country).upper():
            return False
        if payload["execution_scope"] != ("rehearsal" if expected_rehearsal else "production"):
            return False
        prompt,hashes,model,input_digest=_resolved_context(
            project_root,payload["task"],payload["country"],payload["input_ids"],
            allow_rehearsal=bool(expected_rehearsal),
        )
        if "thread_part" in result and isinstance(result.get("text"), str):
            part_idx = int(result["thread_part"]) - 1
            if 0 <= part_idx < len(payload["output_digests"]):
                actual = [sha256_text(result["text"])]
                expected_digests = [payload["output_digests"][part_idx]]
            else:
                return False
        else:
            values = result.get("parts") if isinstance(result.get("parts"), list) else [result.get("text")]
            actual = [sha256_text(x) for x in values if x is not None]
            expected_digests = payload["output_digests"]
        critic_ok = (payload["critic_status"] == "verified" and bool(payload["critic_model"])) if payload["task"] in {"sales_post","value_post"} else (payload["critic_status"] == "not_run" and payload["critic_model"] is None)
        return (actual==expected_digests and prompt==payload["prompt_digest"] and hashes==payload["source_hashes"] and model==payload["model"] and input_digest==payload["input_payload_digest"] and critic_ok)
    except (_InvalidSignature, KeyError, ValueError, TypeError, OSError, json.JSONDecodeError):
        return False
    except Exception as exc:
        raise ContractError(f"attestation verification failed closed: {exc}") from exc

def rotate_attestation_key(key_dir=DEFAULT_KEY_DIR):
    stop_generation_service(); d=_Path(key_dir); private=d/"private.pem"
    if private.exists():
        archived=d/("private-"+hashlib.sha256(private.read_bytes()).hexdigest()[:16]+".pem")
        os.replace(private,archived); os.chmod(archived,0o600)
