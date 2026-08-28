#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""heightcue 오토파일럿 — 오케스트레이터.

사용법:
  python3 run.py daily      # 지표 수집 → 소싱 → KR 판매1+가치1 + US 판매(로테이션)+가치1 → 발행/보류
  python3 run.py post       # 가치글 1건 추가 (저녁 슬롯 — KR 가치 2/일 완성)
  python3 run.py comments   # 댓글 수집 → 분류 → 자동 답글/보류
  python3 run.py weekly     # 주간 지표 요약 → 플레이북 갱신 → 리포트 → Threads 토큰 자동 갱신
  python3 run.py rehearsal  # 원커맨드 리허설: validate → daily(실생성·발행 없음) → preview 출력
  python3 run.py status     # 현재 상태 요약 (큐·보류함·오류·발행 카운트)
  python3 run.py golive     # 가동 전환: dry_run=false + publish=true 저장 + crontab 안내 출력
  python3 run.py dryrun     # API 키 없이 전체 사이클 모의 실행
  python3 run.py context    # 소싱용 채널 컨텍스트 출력 (바이오·게시글·답글·받은댓글·큐 상태, JSON)

발행 정책 (SSOT §8): 포맷 FAIL(500자 초과) → 1회 재생성, 재실패 시 보류함 /
리스크 메모 있음 → 보류함 / 깨끗함 → (publish=true일 때) 발행, 아니면 preview 기록.
각 단계는 오류 격리된다 — 한 단계가 죽어도 나머지는 계속 실행되고 errors.jsonl에 남는다.
"""
import json
import random
import re
import subprocess
import sys

import analytics
import comments as comments_mod
import evidence
import generate
import improve
import post_check
import publish
import sitegen
import sourcing
from common import (append_jsonl, is_real_publication, load_config, load_story_episodes, log,
                    recent_context, read_json, read_jsonl, record_error, set_mode_flags, state_path)


def _guess_pattern(hook):
    if any(q in hook for q in ["\"", "“", "'"]):
        return "리뷰 발굴형"
    if "?" in hook:
        return "공감 직격형"
    if any(w in hook for w in ["걸렀", "비교", "rejected", "compared"]):
        return "선별형"
    return "결과 관찰형"


def _sales_arm(cfg, country, key, default="ab"):
    """A/B 암 선택 — 발행된 해당 국가 판매글 수의 홁수로 결정적 교대. config로 고정 가능."""
    mode = cfg["mode"].get(key, default)
    if mode in ("direct", "site", "on", "off"):
        return mode
    n = sum(1 for p in read_jsonl(state_path(cfg, "published.jsonl"))
            if p.get("country") == country
            and not str(p.get("media_id") or "").startswith("DRY-")
            and (p.get("meta") or {}).get("post_type") == "sales")
    if key == "kr_link_mode":
        return "direct" if n % 2 == 0 else "site"
    return "on" if n % 2 == 0 else "off"


def _gate_and_publish(cfg, text, country, post_type, product=None, link=None, dry_run=False, meta_extra=None):
    text = text or ""
    if not text.strip():
        return None, "format_fail"
    """검사 → 발행. 반환: (media_id|None, reason) — reason ∈ published/format_fail/risk_hold/manual_hold"""
    if country == "US" and re.search(r"[가-힣]", text):
        log("검사(US): 한글 감지 — 영어로 재생성")
        return None, "language_fail"
    if country == "KR" and not re.search(r"[가-힣]", text):
        log("검사(KR): 한국어 없음 — 한국어로 재생성")
        return None, "language_fail"
    check = post_check.check_post({
        "country": country, "post_type": post_type, "text": text,
        # 원문 리뷰·라벨·스펙과 대조하는 검사가 추가돼도 근거가 잘리지 않도록 전체 제품 증거를 넘긴다.
        "product": product or {},
    })
    hook = text.splitlines()[0][:70]
    log(f"검사({country}/{post_type}): 포맷 {check['format_score']}점, 리스크 메모 {len(check['risk_notes'])}건")
    for t in check["format_tips"]:
        log(f"  ▲ {t}")
    for n in check["risk_notes"]:
        log(f"  ~ {n}")

    if check["verdict"] == "FAIL":
        return None, "format_fail"
    if check["risk_notes"] and cfg["mode"].get("hold_flagged", True):
        append_jsonl(state_path(cfg, "holdbox.jsonl"),
                     {"why": "risk_flagged", "country": country, "text": text, "notes": check["risk_notes"]})
        log("→ 리스크 메모가 있어 보류함(주간 리포트에서 확인)")
        return None, "risk_hold"
    if not cfg["mode"].get("auto_publish_clean", True):
        append_jsonl(state_path(cfg, "holdbox.jsonl"), {"why": "manual_mode", "country": country, "text": text})
        return None, "manual_hold"
    media = publish.publish_text(cfg, country, text, link=link, dry_run=dry_run,
                                 meta={"post_type": post_type, "hook_pattern": _guess_pattern(hook),
                                       "format_score": check["format_score"], **(meta_extra or {})})
    return media, "published"


def _publish_with_retry(cfg, build_fn, country, post_type, product=None, link=None, dry_run=False, meta_extra=None):
    """포맷·언어 실패 때만 1회 재생성. 리스크 보류는 재시도하지 않는다."""
    last_text = None
    last_reason = None
    for attempt in (1, 2):
        text = build_fn()
        last_text = text
        media, reason = _gate_and_publish(cfg, text, country, post_type,
                                          product=product, link=link, dry_run=dry_run, meta_extra=meta_extra)
        if reason not in ("format_fail", "language_fail"):
            return media, reason
        last_reason = reason
        label = "포맷" if reason == "format_fail" else "언어"
        log(f"{label} 실패 — {'재생성 1회 시도' if attempt == 1 else '재시도도 실패, 보류함으로'}")
    append_jsonl(state_path(cfg, "holdbox.jsonl"),
                 {"why": last_reason, "country": country, "text": last_text})
    return None, last_reason


def _publish_thread(cfg, parts, country, dry_run=False, meta_extra=None):
    """타래 발행 — 각 편을 검사기에 태우고 앞 편에 답글로 잇는다.

    설계 결정: **전량 사전 검사 후 발행.** 1편을 올린 뒤 2편이 검사에서
    걸리면 미완성 타래가 채널에 남는데, 이건 삭제 스코프 이슈까지 얽혀
    수습이 번거롭다. 그래서 한 편이라도 걸리면 아무것도 올리지 않는다.

    반환: (root_media_id|None, reason)
    """
    if not parts or len(parts) < 2:
        return None, "thread_too_short"

    # 1단계 — 전량 사전 검사 (발행 없음)
    for i, text in enumerate(parts, 1):
        text = (text or "").strip()
        if not text:
            log(f"타래 {i}편 비어 있음 — 전체 취소")
            return None, "format_fail"
        if country == "US" and re.search(r"[가-힣]", text):
            return None, "language_fail"
        if country == "KR" and not re.search(r"[가-힣]", text):
            return None, "language_fail"
        check = post_check.check_post({"country": country, "post_type": "value",
                                       "text": text, "product": {}})
        if check["verdict"] == "FAIL":
            log(f"타래 {i}편 포맷 FAIL — 전체 취소")
            return None, "format_fail"
        if check["risk_notes"] and cfg["mode"].get("hold_flagged", True):
            append_jsonl(state_path(cfg, "holdbox.jsonl"),
                         {"why": "risk_flagged", "country": country, "text": text,
                          "notes": check["risk_notes"], "thread_part": i})
            log(f"타래 {i}편 리스크 메모 — 전체 보류")
            return None, "risk_hold"
    if not cfg["mode"].get("auto_publish_clean", True):
        append_jsonl(state_path(cfg, "holdbox.jsonl"),
                     {"why": "manual_mode", "country": country, "text": "\n---\n".join(parts)})
        return None, "manual_hold"

    # 2단계 — 순차 발행. 각 편은 직전 편에 답글로 붙는다.
    root, prev = None, None
    for i, text in enumerate(parts, 1):
        meta = {"post_type": "value", "thread_part": i, "thread_total": len(parts),
                **(meta_extra or {})}
        if i == 1:
            meta["hook_pattern"] = _guess_pattern(text.splitlines()[0][:70])
        media = publish.publish_text(cfg, country, text.strip(), reply_to=prev,
                                     dry_run=dry_run, meta=meta)
        if not media:
            # 사전 검사를 통과했으므로 여기 오면 API 장애다. 남은 편을 보류함에 남겨
            # 사람이 이어붙일 수 있게 한다(추측 재시도로 중복 발행하지 않는다).
            log(f"타래 {i}편 발행 실패 — 남은 편 보류함 기록")
            append_jsonl(state_path(cfg, "holdbox.jsonl"),
                         {"why": "thread_broken", "country": country,
                          "published_root": root, "failed_at_part": i,
                          "remaining": parts[i - 1:]})
            return root, "thread_partial"
        root = root or media
        prev = media
    log(f"타래 발행 완료({country}): {len(parts)}편, root={root}")
    return root, "published"


def make_and_publish_value(cfg, dry_run=False, country="KR"):
    episodes = load_story_episodes(cfg)
    recent = [p.get("text", "").splitlines()[0]
              for p in read_jsonl(state_path(cfg, "published.jsonl"))
              if is_real_publication(p)][-10:]
    kind = "story" if (episodes and random.random() < 0.6) else "info"
    episode = random.choice(episodes) if (kind == "story" and episodes) else None

    # info 글은 증거 원장의 검증된 원자에서 주제를 받는다. 원장이 비면
    # 지어낸 사실이 나가는 대신 story로 폴백한다(무근거 발행 금지).
    atom = None
    topic = None
    if not episode:
        atom = evidence.pick_atom(cfg, country=country, channel="threads")
        if atom:
            topic = evidence.to_generation_topic(atom)
        elif episodes:
            log("증거 원장 비어 있음 — story로 폴백")
            kind, episode = "story", random.choice(episodes)
        else:
            topic = "성장기 수면·식사·검진 중 하나를 사실 위주로 정리"

    meta_extra = {"atom_id": atom["atom_id"], "topic": atom["topic"],
                  "distance": atom["distance"]} if atom else None

    # 근거가 탄탄한 원자(strong/moderate)는 타래로 푼다. 사실·반론·실행이
    # 한 원자에 다 들어있어 480자 단편에 넣으면 정보가 뭉개진다.
    # 확신도 weak이나 story 글은 단편 유지.
    thread_ratio = cfg["mode"].get("value_thread_ratio", 0.5)
    if (atom and atom.get("confidence") in ("strong", "moderate")
            and random.random() < thread_ratio):
        parts_n = 4 if atom.get("confidence") == "strong" else 3
        try:
            result = generate.make_value_thread(cfg, topic, parts=parts_n,
                                                dry_run=dry_run, country=country)
            parts = [p for p in (result.get("parts") or []) if (p or "").strip()]
            if len(parts) >= 2:
                media, reason = _publish_thread(cfg, parts, country, dry_run=dry_run,
                                                meta_extra=meta_extra)
                if reason in ("published", "thread_partial"):
                    if media and not dry_run:
                        evidence.mark_used(cfg, atom["atom_id"], "threads", country, media)
                    return media, reason
                log(f"타래 실패({reason}) — 단편으로 폴백")
        except Exception as e:
            record_error(cfg, "value_thread", e)
            log("타래 생성 오류 — 단편으로 폴백")

    def build():
        result = generate.make_value_post(cfg, kind, episode=episode, topic=topic,
                                          recent=recent, dry_run=dry_run, country=country)
        return result["text"]

    media, reason = _publish_with_retry(cfg, build, country, "value",
                                        dry_run=dry_run, meta_extra=meta_extra)
    if atom and media and not dry_run:
        evidence.mark_used(cfg, atom["atom_id"], "threads", country, media)
    return media, reason


def _kr_sales(cfg, hint, dry_run):
    product = sourcing.pick(cfg, dry_run=dry_run)
    if not product:
        return
    master = generate.make_master(cfg, product, playbook_hint=hint, dry_run=dry_run)

    # 링크 모드 A/B — direct: 글에 쿠팡링크+고지 / site: 자사 제품페이지 경유(고지는 페이지 상단)
    link_mode = _sales_arm(cfg, "KR", "kr_link_mode")
    link = product.get("link")
    if link_mode == "site":
        deploy = not (dry_run or cfg["mode"].get("_rehearsal") or not cfg["mode"].get("publish", False))
        page_url = None
        try:
            page_url = sitegen.kr_page(cfg, product, master, deploy=deploy)
        except Exception as e:
            record_error(cfg, "sitegen.kr_page", e)
        if page_url:
            link = page_url
        else:
            link_mode = "direct"  # 배포 실패 → 직링크+고지로 안전 폴백
            link = product.get("link")
    product = {**product, "link": link, "link_mode": link_mode}
    log(f"KR 판매글 링크 모드: {link_mode}")

    publication_meta = {
        "experiment_id": "kr_link_mode", "experiment_arm": link_mode,
        "link_mode": link_mode, "category": product.get("category"),
        "product_id": product.get("product_key"),
        "formfactor_id": product.get("formfactor_id"),
        "ux_grade": product.get("ux_grade"), "sub_id": product.get("sub_id"),
    }

    def build():
        result = generate.make_sales_post(cfg, master, product, playbook_hint=hint, dry_run=dry_run)
        publication_meta.update({key: result.get(key) for key in
                                 ("hook_family", "angle_id", "writer_variant", "viral_score")})
        return result["text"]

    _publish_with_retry(cfg, build, product.get("country", "KR"), "sales",
                        product=product, link=link, dry_run=dry_run,
                        meta_extra=publication_meta)


def _us_sales(cfg, hint, dry_run):
    product = sourcing.pick_us(cfg, dry_run=dry_run)
    if not product:
        return
    master = generate.make_master(cfg, product, playbook_hint=hint, dry_run=dry_run)

    # US 판매글은 자사 가이드 경유 여부와 무관하게 추천 자체의 경제적 이해관계를 첫 줄에 고지한다.
    # FTC의 clear/conspicuous·recommendation 근접 원칙상 #ad 유무는 성과 실험 대상이 아니다.
    ad_mode = "on"
    product = {**product, "ad_mode": ad_mode}
    log(f"US 판매글 #ad 모드: {ad_mode}")

    publication_meta = {
        "ad_mode": ad_mode,
        "product_id": product.get("product_key"),
        "formfactor_id": product.get("formfactor_id"),
        "ux_grade": product.get("ux_grade"),
        "category": product.get("category"),
        "sub_id": product.get("sub_id"),
    }

    def build():
        result = generate.make_sales_post(cfg, master, product, playbook_hint=hint, dry_run=dry_run)
        publication_meta.update({key: result.get(key) for key in
                                 ("hook_family", "angle_id", "writer_variant", "viral_score")})
        return result["text"]

    _publish_with_retry(cfg, build, "US", "sales",
                        product=product, link=product.get("link"), dry_run=dry_run,
                        meta_extra=publication_meta)


def daily(cfg, dry_run=False):
    log("=== daily 시작 ===")
    
    # NEW: Run digest first to set ACP
    import digest
    try:
        digest.run_digest(cfg)
    except Exception as e:
        log(f"Digest failed (ignored): {e}")

    try:
        analytics.collect(cfg, dry_run=dry_run)
    except Exception as e:
        record_error(cfg, "analytics.collect", e)

    # 증거 원장 승격 — Aside 수집 워커가 쌓은 원본을 claim_gate에 태운다.
    # 가치글 생성보다 반드시 먼저 실행되어야 당일 원자가 공급된다.
    try:
        evidence.promote_pending(cfg)
    except Exception as e:
        record_error(cfg, "evidence.promote_pending", e)

    hint = improve.playbook_hint(cfg)

    # KR 판매 1
    try:
        _kr_sales(cfg, hint, dry_run)
    except Exception as e:
        record_error(cfg, "kr_sales", e)
    # KR 가치 1 (두 번째는 post 명령이 저녁 슬롯에서)
    try:
        make_and_publish_value(cfg, dry_run=dry_run)
    except Exception as e:
        record_error(cfg, "kr_value", e)

    # US 트랙 (확정 스코프)
    us_ready = dry_run or cfg["threads"].get("us_access_token")
    if cfg["mode"].get("us_sales_posts", True) and us_ready:
        try:
            _us_sales(cfg, hint, dry_run)
        except Exception as e:
            record_error(cfg, "us_sales", e)
    if cfg["mode"].get("us_value_posts", True) and us_ready:
        try:
            make_and_publish_value(cfg, dry_run=dry_run, country="US")
        except Exception as e:
            record_error(cfg, "us_value", e)

    # 소싱 버퍼 충전 — Aside 루틴이 다음 슬롯 전까지 결과를 채워 둔다
    if not dry_run:
        try:
            sourcing.top_up_requests(cfg)
        except Exception as e:
            record_error(cfg, "top_up_requests", e)
        if not cfg["mode"].get("publish", False):
            log("※ 리허설 모드: 생성 결과는 state/preview.jsonl 확인. 실발행은 golive 명령 또는 \"publish\": true")
    log("=== daily 종료 ===")


def status(cfg):
    reqs = read_json(state_path(cfg, "browser-queue/requests.json"), [])
    results = read_json(state_path(cfg, "browser-queue/results.json"), [])
    used = {h.get("product_key") for h in read_json(state_path(cfg, "sourced_history.json"), [])}
    ready = [r for r in results if r.get("status") == "done"
             and r.get("product_key") not in used and sourcing.is_audit_approved(r)]
    audit_hold = [r for r in results if r.get("status") == "done"
                  and r.get("product_key") not in used and not sourcing.is_audit_approved(r)]
    published = [p for p in read_jsonl(state_path(cfg, "published.jsonl"))
                 if is_real_publication(p) and p.get("meta", {}).get("kind") != "reply"]
    preview = read_jsonl(state_path(cfg, "preview.jsonl"))
    holds = read_jsonl(state_path(cfg, "holdbox.jsonl"))
    errors = read_jsonl(state_path(cfg, "errors.jsonl"))
    mode = {k: v for k, v in cfg["mode"].items() if not k.startswith("_")}
    print("heightcue 상태")
    print("=" * 44)
    print(f"모드            : {mode}")
    print(f"소싱 큐         : 대기 {sum(1 for q in reqs if q.get('status') == 'pending')}건 / 감사 통과 {len(ready)}건 / 감사 보류 {len(audit_hold)}건")
    print(f"US 판매 레지스트리: {len(read_json(state_path(cfg, 'us_products.json'), []))}건")
    print(f"발행 이력        : 실발행 {len(published)}건 / 리허설 preview {len(preview)}건")
    print(f"보류함          : {len(holds)}건 (주간 리포트에서 검토)")
    print(f"오류 로그        : {len(errors)}건" + (f" — 최근: {errors[-1]['where']}" if errors else ""))
    print(f"스토리 뱅크      : 사용 가능 에피소드 {len(load_story_episodes(cfg))}개 (하이브리드 모드)")
    ux = sourcing.ux_store(cfg)["formfactors"]
    cand = sum(1 for f in ux if f.get("status") == "candidate")
    novel = sum(1 for f in ux if f.get("ux_grade") == "novel" and f.get("status") == "active")
    proven = sum(1 for f in ux if f.get("ux_grade") == "proven" and f.get("status") == "active")
    last_disc = max((f.get("discovered_ts") or 0) for f in ux)
    import time as _t
    disc_str = _t.strftime("%Y-%m-%d", _t.localtime(last_disc)) if last_disc else "없음(시드만)"
    print(f"UX 발굴 저장소    : 활성 proven {proven} / novel {novel} / 후보 {cand} / 마지막 발굴 {disc_str}")


def rehearsal(cfg):
    log("=== 리허설: 자격 검증 → 실제 생성(발행 없음) → 미리보기 ===")
    if cfg["mode"].get("publish"):
        log("경고: publish=true 상태 — 리허설은 발행 없이 돌리는 모드입니다. config에서 publish를 끄고 다시 실행하세요.")
        return 1
    if subprocess.call([sys.executable, "validate.py"]):
        log("자격 검증 실패 — 생성하지 않고 중단합니다.")
        return 1
    cfg["mode"]["_rehearsal"] = True
    try:
        daily(cfg, dry_run=False)
    finally:
        cfg["mode"].pop("_rehearsal", None)
    print("\n──── 생성된 미리보기 (state/preview.jsonl 최근 5건) ────")
    for rec in read_jsonl(state_path(cfg, "preview.jsonl"))[-5:]:
        print(f"\n[{rec.get('country')}] {'링크: ' + str(rec.get('link')) if rec.get('link') else '(링크 없음)'}")
        print(rec.get("text", ""))
    print("\n→ 이 내용을 Claude에게 공유해 톤·품질 점검을 받은 뒤, 만족스러우면 `python3 run.py golive`")
    return 0


def golive(cfg):
    mode = set_mode_flags(dry_run=False, publish=True)
    log(f"가동 전환 저장 완료: {mode}")
    print("""
다음 실행부터 실제 발행됩니다. OS crontab에 아래 6개 작업을 등록하세요(이 레포의 `crontab.txt`와 동일):

30 9  * * * cd $HOME/heightcue-autopilot/autopilot && ../.venv/bin/python run.py daily >> state/cron.log 2>&1
30 12 * * * cd $HOME/heightcue-autopilot/autopilot && ../.venv/bin/python run.py post KR value >> state/cron.log 2>&1 && ../.venv/bin/python run.py post US value >> state/cron.log 2>&1
0  14 * * * cd $HOME/heightcue-autopilot/autopilot && ../.venv/bin/python run.py comments >> state/cron.log 2>&1
0  16 * * * cd $HOME/heightcue-autopilot/autopilot && ../.venv/bin/python run.py post KR sales >> state/cron.log 2>&1 && ../.venv/bin/python run.py post US value >> state/cron.log 2>&1
30 19 * * * cd $HOME/heightcue-autopilot/autopilot && ../.venv/bin/python run.py post KR value >> state/cron.log 2>&1 && ../.venv/bin/python run.py post US value >> state/cron.log 2>&1 && ../.venv/bin/python run.py comments >> state/cron.log 2>&1
0  21 * * 0 cd $HOME/heightcue-autopilot/autopilot && ../.venv/bin/python run.py weekly >> state/cron.log 2>&1

정지(킬스위치): crontab에서 6개 작업을 제거, 또는 config.json의 "publish"를 false로.""")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "dryrun"
    cfg = load_config()
    dry = cfg["mode"].get("dry_run", True) or cmd == "dryrun"
    if cmd in ("daily", "dryrun"):
        daily(cfg, dry_run=dry)
        if cmd == "dryrun":
            comments_mod.run(cfg, dry_run=True)
            improve.run(cfg, dry_run=True)
    elif cmd == "post":
        # 사용법: run.py post [KR|US] [value|sales] — 기본 KR value
        country = (sys.argv[2] if len(sys.argv) > 2 else "KR").upper()
        ptype = (sys.argv[3] if len(sys.argv) > 3 else "value").lower()
        try:
            if ptype == "sales":
                hint = improve.playbook_hint(cfg)
                if country == "KR":
                    _kr_sales(cfg, hint, dry)
                else:
                    _us_sales(cfg, hint, dry)
            else:
                make_and_publish_value(cfg, dry_run=dry, country=country)
        except Exception as e:
            record_error(cfg, f"post_{country.lower()}_{ptype}", e)
    elif cmd == "comments":
        try:
            comments_mod.run(cfg, dry_run=dry)
        except Exception as e:
            record_error(cfg, "comments", e)
    elif cmd == "weekly":
        improve.run(cfg, dry_run=dry)
    elif cmd == "rehearsal":
        raise SystemExit(rehearsal(cfg))
    elif cmd == "status":
        status(cfg)
    elif cmd == "golive":
        golive(cfg)
    elif cmd == "context":
        # 소싱 루틴(Aside)이 읽는 읽기전용 채널 컨텍스트 — 발행/답글 없음
        out = {}
        for country in ("KR", "US"):
            try:
                out[country] = recent_context(cfg, country)
            except Exception as e:
                out[country] = {"error": f"{type(e).__name__}: {e}"}
        try:
            from sourcing import _queue_paths
            from common import read_json
            req_p, res_p, _ = _queue_paths(cfg)
            out["queue"] = {
                "pending_requests": [q for q in read_json(req_p, []) if q.get("status") == "pending"],
                "done_results": [{"category": r.get("category"), "product_name": r.get("product_name")}
                                  for r in read_json(res_p, []) if r.get("status") == "done"],
            }
        except Exception as e:
            out["queue"] = {"error": str(e)}
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
