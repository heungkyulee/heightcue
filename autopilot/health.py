# -*- coding: utf-8 -*-
"""운영 상태 점검 — "지금 잘 돌아가고 있나?"에 사실로 답한다.

로그를 눈으로 뒤지지 않아도 되도록, 조용히 죽어 있는 상태를 잡아낸다.
2026-08-29에 두 가지가 로그 속에 묻혀 있었다:
  - mode.publish=false로 하루치 발행이 전부 preview로만 쌓임
  - cron PATH에 aside가 없어 harvest가 매일 FileNotFoundError

실행: ../.venv/bin/python health.py        (사람용)
      ../.venv/bin/python health.py --json (기계용, 이상 있으면 exit 1)
"""
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

from common import is_real_publication, load_config, read_json, read_jsonl, state_path

OK, WARN, FAIL = "ok", "warn", "fail"


def _mtime_age_min(path):
    if not os.path.exists(path):
        return None
    return (datetime.now() - datetime.fromtimestamp(os.path.getmtime(path))).total_seconds() / 60


def check_publish_gate(cfg):
    """발행 게이트. 꺼져 있으면 콘텐츠가 생성돼도 전부 preview로 샌다."""
    publishing = bool(cfg["mode"].get("publish", False))
    dry = bool(cfg["mode"].get("dry_run", False))
    if dry:
        return FAIL, "dry_run=true — 아무것도 발행되지 않는다"
    if not publishing:
        return FAIL, "mode.publish=false — 생성은 되지만 preview.jsonl로만 쌓인다 (실발행 0)"
    return OK, "발행 활성"


def check_recent_publish(cfg):
    """마지막 실발행 이후 경과. 스케줄상 하루 3~4건이 정상."""
    rows = [r for r in read_jsonl(state_path(cfg, "published.jsonl"))
            if is_real_publication(r) and r.get("meta", {}).get("kind") != "reply"]
    if not rows:
        return FAIL, "실발행 기록 없음"
    last = rows[-1].get("ts", "")
    try:
        age_h = (datetime.now() - datetime.fromisoformat(str(last))).total_seconds() / 3600
    except ValueError:
        return WARN, f"마지막 발행 시각 파싱 불가: {last}"
    if age_h > 24:
        return FAIL, f"마지막 실발행이 {age_h:.0f}시간 전 ({last}) — 스케줄이 죽었을 수 있다"
    if age_h > 12:
        return WARN, f"마지막 실발행이 {age_h:.0f}시간 전 ({last})"
    return OK, f"마지막 실발행 {age_h:.1f}시간 전"


def check_comments_alive(cfg):
    """댓글 크론(3분 주기) 생존. 매 실행 replies_handled.json을 다시 쓴다."""
    age = _mtime_age_min(state_path(cfg, "replies_handled.json"))
    if age is None:
        return WARN, "replies_handled.json 없음 (아직 한 번도 안 돌았을 수 있음)"
    if age > 15:
        return FAIL, f"댓글 크론이 {age:.0f}분간 안 돎 (3분 주기여야 함) — crontab 확인"
    return OK, f"댓글 크론 정상 ({age:.0f}분 전 실행)"


def check_outreach_alive(cfg, rows=None, now=None):
    settings = cfg.get("outreach") or {}
    if not settings.get("enabled") or not settings.get("publish"):
        return WARN, "외부 답글 발행이 비활성"
    observed = read_jsonl(state_path(cfg, "outreach.jsonl")) if rows is None else list(rows)
    if not observed:
        return FAIL, "외부 답글 원장 없음 — 첫 검증 실행 필요"
    current = now or datetime.now().astimezone()

    def parsed(value):
        try:
            stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if stamp.tzinfo is None and current.tzinfo is not None:
                stamp = stamp.replace(tzinfo=current.tzinfo)
            return stamp
        except (TypeError, ValueError):
            return None

    latest = {}
    for index, row in enumerate(observed):
        key = row.get("idempotency_key") or f"row-{index}"
        latest[key] = {**latest.get(key, {}), **row}
    stale = []
    for row in latest.values():
        if row.get("status") not in {"reserved", "verification_pending"}:
            continue
        stamp = parsed(row.get("reserved_at") or row.get("verified_at"))
        if stamp is None or (current - stamp).total_seconds() > 90 * 60:
            stale.append(str(row.get("market") or "?"))
    if stale:
        return FAIL, "외부 답글 미확인 예약 90분 초과: " + ",".join(stale)

    missing = []
    markets = tuple(settings.get("markets") or ("KR", "US"))
    for market in markets:
        stamps = [parsed(row.get("verified_at")) for row in latest.values()
                  if row.get("market") == market and row.get("status") == "verified"]
        valid = [stamp for stamp in stamps if stamp is not None]
        if not valid or (current - max(valid)).total_seconds() > 12 * 3600:
            missing.append(market)
    if missing:
        return FAIL, "최근 12시간 검증 답글 없음: " + ",".join(missing)
    return OK, "외부 답글 최근 검증 및 예약 상태 정상: " + ",".join(markets)


def check_cron_registered():
    """crontab 등록 여부. crontab.txt만 고치고 등록을 안 한 사고 전례가 있다."""
    try:
        out = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=10).stdout
    except Exception as e:
        return WARN, f"crontab 조회 실패: {e}"
    lines = [l for l in out.splitlines() if "heightcue" in l and not l.strip().startswith("#")]
    if not lines:
        return FAIL, "crontab에 heightcue 작업이 없다 — `crontab ~/heightcue-autopilot/crontab.txt` 필요"
    if not any("PATH=" in l for l in out.splitlines()):
        return WARN, "crontab에 PATH 설정 없음 — aside 등 외부 CLI가 실패할 수 있다"
    return OK, f"crontab {len(lines)}개 등록"


def check_external_tools():
    """cron이 실제로 쓰는 PATH에서 외부 CLI가 보이는지."""
    try:
        out = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return WARN, "crontab 조회 실패"
    path = next((l.split("=", 1)[1].strip() for l in out.splitlines()
                 if l.startswith("PATH=")), "/usr/bin:/bin")
    missing = [t for t in ("aside",)
               if subprocess.run(["env", "-i", f"PATH={path}", "sh", "-c", f"command -v {t}"],
                                 capture_output=True).returncode != 0]
    if missing:
        return FAIL, f"cron PATH에서 못 찾음: {', '.join(missing)} — harvest가 죽는다"
    return OK, "외부 CLI(aside) 접근 가능"


def check_recent_errors(cfg):
    """최근 24시간 에러. 단 '이후에 같은 작업이 성공했다면' 해결된 것으로 본다.

    고친 문제가 24시간 동안 계속 경보를 울리면 health.py를 아무도 안 보게 된다.
    같은 where가 반복 중인지(만성)와 이미 회복됐는지(일시)를 구분한다.
    """
    rows = read_jsonl(state_path(cfg, "errors.jsonl"))
    cutoff = datetime.now() - timedelta(hours=24)
    recent = []
    for r in rows:
        try:
            if datetime.fromisoformat(str(r.get("ts", ""))) >= cutoff:
                recent.append(r)
        except ValueError:
            continue
    if not recent:
        return OK, "최근 24시간 에러 없음"

    # 마지막 에러 이후 해당 파이프라인이 성공했는지 — 실제 산출물 기록으로 판정한다.
    def ts_value(value):
        try:
            return datetime.fromisoformat(str(value)).timestamp()
        except (ValueError, TypeError):
            return 0.0

    last_by_where = {}
    for row in recent:
        key = str(row.get("where", "?")).split(".")[0]
        last_by_where[key] = max(last_by_where.get(key, 0.0), ts_value(row.get("ts")))

    recovered = []
    if "harvest" in last_by_where:
        ev = read_jsonl(state_path(cfg, "evidence.jsonl"))
        if any(ts_value(e.get("ts") or e.get("harvested_at")) > last_by_where["harvest"] for e in ev):
            recovered.append("harvest")

    previews = read_jsonl(state_path(cfg, "preview.jsonl"))
    success_times = {}
    for row in previews:
        meta = row.get("meta") or {}
        country = str(row.get("country") or "").lower()
        post_type = str(meta.get("post_type") or "").lower()
        key = f"{country}_{post_type}" if country and post_type else ""
        if key:
            success_times[key] = max(success_times.get(key, 0.0), ts_value(row.get("ts")))
    # A policy hold proves generation, critic, and the post-check boundary recovered.
    # It does not count as publication success; that remains a separate health check.
    for row in read_jsonl(state_path(cfg, "holdbox.jsonl")):
        country = str(row.get("country") or "").lower()
        post_type = str(row.get("post_type") or "").lower()
        key = f"{country}_{post_type}" if country and post_type else ""
        if key:
            success_times[key] = max(success_times.get(key, 0.0), ts_value(row.get("ts")))
    publication_times = {}
    for row in read_jsonl(state_path(cfg, "published.jsonl")):
        meta = row.get("meta") or {}
        if meta.get("publish_status") != "verified":
            continue
        country = str(row.get("country") or "").lower()
        post_type = str(meta.get("post_type") or "").lower()
        key = f"{country}_{post_type}" if country and post_type else ""
        if key:
            verified_at = meta.get("reconciled_at") or meta.get("published_at") or row.get("ts")
            publication_times[key] = max(publication_times.get(key, 0.0), ts_value(verified_at))
    for key in ("kr_value", "us_value", "kr_sales", "us_sales"):
        if key in last_by_where and success_times.get(key, 0.0) > last_by_where[key]:
            recovered.append(key)
        legacy_post_key = f"post_{key}"
        if (legacy_post_key in last_by_where
                and success_times.get(key, 0.0) > last_by_where[legacy_post_key]):
            recovered.append(legacy_post_key)
    if ("post_us_sales" in last_by_where
            and publication_times.get("us_sales", 0.0) > last_by_where["post_us_sales"]):
        recovered.append("post_us_sales")

    recovered = list(dict.fromkeys(recovered))
    where = {}
    for r in recent:
        w = str(r.get("where", "?")).split(".")[0]
        if w in recovered:
            continue
        where[w] = where.get(w, 0) + 1

    if not where:
        return OK, f"최근 24시간 에러 {len(recent)}건 — 이후 모두 복구 확인 ({', '.join(recovered)})"
    top = ", ".join(f"{k}×{v}" for k, v in sorted(where.items(), key=lambda x: -x[1])[:3])
    note = f" (복구됨: {', '.join(recovered)})" if recovered else ""
    unresolved = sum(where.values())
    return (FAIL if unresolved >= 3 else WARN), f"미복구 에러 {unresolved}건 ({top}){note}"


def check_evidence_stock(cfg):
    """증거 원장 재고. 비면 가치글이 story 폴백으로만 나간다."""
    atoms = read_json(state_path(cfg, "insight_atoms.json"), [])
    items = atoms if isinstance(atoms, list) else atoms.get("atoms", [])
    unused = [a for a in items if not (a.get("used_in") or {})]
    if len(items) == 0:
        return FAIL, "인사이트 원자 0건 — 가치글이 근거 없이 나간다"
    if len(unused) < 3:
        return WARN, f"미사용 원자 {len(unused)}건 (총 {len(items)}) — harvest 확인 필요"
    return OK, f"원자 {len(items)}건 (미사용 {len(unused)})"


def check_companyos_workflow(cfg, probe=None):
    """Supabase 실행 원장의 승인·랜딩·오퍼·선점 불변식을 점검한다."""
    if probe is None:
        try:
            import companyos
            probe = companyos.workflow_health()
        except Exception as exc:
            return FAIL, f"Company OS 상품 원장 접근 실패: {type(exc).__name__}: {exc}"
    if not isinstance(probe, dict):
        return FAIL, "Company OS 상품 원장 응답 형식 오류"
    checks = probe.get("checks") or {}
    failed = sorted(key for key, value in checks.items() if not value)
    counts = probe.get("counts") or {}
    if not probe.get("ok") or failed:
        return FAIL, "상품 인계 불변식 실패: " + ", ".join(failed or ["unknown"])
    summary = ", ".join(f"{key}={value}" for key, value in sorted(counts.items())) or "상품 0"
    return OK, f"Company OS 상품 원장 정상 ({summary})"


_ACTIVE_CONTRACT_PATHS = (
    "AGENTS.md",
    "LAUNCH-STATUS.md",
    "aside-sourcing-routine.md",
    "reply-outreach.md",
    "heightcue-gemini-skills.md",
    "context/user-intent-contract.md",
    "autopilot/config.example.json",
)
_DRIFT_NEGATION = ("금지", "제거", "폐기", "퇴역", "사용하지", "복귀하지", "안 하는", "하지 않", "retired", "do not", "never")


def _active_contract_files():
    root = Path(__file__).resolve().parents[1]
    out = {}
    for relative in _ACTIVE_CONTRACT_PATHS:
        path = root / relative
        if path.is_file():
            out[relative] = path.read_text(encoding="utf-8")
    return out


def check_active_contract_drift(files=None):
    """Fail when active docs/config still declare the retired operating model."""
    supplied = files if files is not None else _active_contract_files()
    findings = []
    for name, content in supplied.items():
        text = str(content or "")
        for line in text.splitlines() or [text]:
            lowered = line.lower()
            negated = any(token in lowered for token in _DRIFT_NEGATION)
            if not negated and (
                ("167cm" in lowered and any(token in lowered for token in ("팩트폭격기", "페르소나", "고정")))
                or ("5'6" in lowered and "uncle" in lowered)
            ):
                findings.append(f"{name}:retired_persona")
            if not negated and re.search(r"(?:신장계|stadiometer|키\s*재기).{0,40}(?:판매|추천|제휴|상품|product|pick)", line, re.I):
                findings.append(f"{name}:measurement_commerce")
            if not negated and re.search(r"(?:일|하루)\s*10\s*건|10\s*posts?\s*(?:a|per)\s*day", line, re.I):
                findings.append(f"{name}:legacy_cadence")
        sales = re.search(r'"sales_per_day"\s*:\s*(\d+)', text)
        value = re.search(r'"value_per_day"\s*:\s*(\d+)', text)
        if sales and value and int(sales.group(1)) + int(value.group(1)) > 2:
            findings.append(f"{name}:legacy_cadence")
    findings = sorted(set(findings))
    if findings:
        return FAIL, "활성 계약 드리프트: " + ", ".join(findings)
    return OK, f"활성 계약 {len(supplied)}개 정합"


CHECKS = [
    ("발행 게이트", lambda c: check_publish_gate(c)),
    ("최근 발행", lambda c: check_recent_publish(c)),
    ("댓글 크론", lambda c: check_comments_alive(c)),
    ("외부 답글", lambda c: check_outreach_alive(c)),
    ("crontab 등록", lambda c: check_cron_registered()),
    ("외부 CLI", lambda c: check_external_tools()),
    ("최근 에러", lambda c: check_recent_errors(c)),
    ("증거 재고", lambda c: check_evidence_stock(c)),
    ("Company OS 상품", lambda c: check_companyos_workflow(c)),
    ("활성 계약", lambda c: check_active_contract_drift()),
]

ICON = {OK: "✓", WARN: "▲", FAIL: "✗"}


def main():
    cfg = load_config()
    results = []
    for name, fn in CHECKS:
        try:
            status, msg = fn(cfg)
        except Exception as e:
            status, msg = WARN, f"점검 실패: {type(e).__name__}: {e}"
        results.append({"name": name, "status": status, "detail": msg})

    worst = FAIL if any(r["status"] == FAIL for r in results) else (
        WARN if any(r["status"] == WARN for r in results) else OK)

    if "--json" in sys.argv:
        print(json.dumps({"overall": worst, "checks": results}, ensure_ascii=False, indent=2))
    else:
        print("=" * 52)
        print("HeightCue 운영 상태")
        print("=" * 52)
        for r in results:
            print(f"{ICON[r['status']]} {r['name']:<12} {r['detail']}")
        print("=" * 52)
        print({OK: "전부 정상", WARN: "주의 필요", FAIL: "조치 필요"}[worst])
    return 1 if worst == FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
