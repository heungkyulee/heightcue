# -*- coding: utf-8 -*-
"""파이프라인 C-3: 주간 자가개선 — '플레이북' 경로로만.

설계 원칙 (SSOT §8):
  - 자가개선은 state/playbook.md 를 갱신하는 방식으로만 이뤄진다.
    playbook은 생성 단계에 '스타일 힌트'(우선 훅 패턴, 잘 먹힌 소재, 게시 시간대)로 주입된다.
  - 스킬 파일(gemini-skills.md)·SSOT·스토리 뱅크는 자동 수정하지 않는다.
    철칙·가드레일·실화는 자가개선의 대상이 아니라 전제다.
  - 매주 결과는 weekly_report.md 로 남겨 사람이 5분 안에 훑을 수 있게 한다(보류함 요약 포함).
"""
import json
import os
import shutil
import time

import analytics
import generate
import publish
import sourcing
from common import log, read_json, read_jsonl, record_error, save_threads_tokens, state_path

NORTH_STAR_KRW = 10_000_000  # 전사 목표: 월 수수료 수익 1,000만원

IMPROVE_PROMPT = """너는 heightcue 채널의 주간 전략 분석가다. 아래 지표 요약을 보고 '플레이북'을 갱신한다.
플레이북은 다음 3개 항목만 담는 짧은 문서다:
1) 우선 훅 패턴 순위 (선별형/리뷰 발굴형/공감 직격형/사실 반전형/결과 관찰형 중, 데이터 근거와 함께)
2) 잘 먹힌 소재·각도 2~3개와 다음 주에 시도할 변형 1~2개
3) 게시 시간대·비율 제안 (판매:공감 비율은 1:2 고정 — 바꾸지 않는다)
4) by_ux_grade 데이터가 있으면: 검증형(proven) vs UX 혁신형(novel) 제품 성과 비교 1~2줄과 다음 주 소싱 무게추 제안 (단, 요청 슬롯의 proven:novel 1:1 교대 자체는 코드 고정 — 제안은 후보 선택 기준에만 반영된다)
금지: 고지·가드레일·실화 원칙에 대한 어떤 변경 제안도 하지 않는다. 데이터에 없는 결론을 만들지 않는다.
각 비교군에 실발행 3건 이상이 없으면 순위나 승자를 선언하지 않는다. 게시 시각별 지표가 없으면 시간대를 바꾸지 않는다.
출력: 마크다운 플레이북 본문만."""


def _decision_sample_ready(summary):
    """귀속·클릭/전환이 있는 두 비교군과 실제 성과 차이가 있을 때만 허용한다."""
    comparisons = []
    comparisons.extend((summary.get("by_hook_pattern") or {}).values())
    for arms in (summary.get("by_experiment") or {}).values():
        comparisons.extend(arms.values())
    eligible = [group for group in comparisons
                if (group.get("posts") or 0) >= 3
                and (group.get("attributed_posts") or 0) >= 3
                and ((group.get("clicks_measured_posts") or 0) >= 3
                     or group.get("conversions") is not None)]
    performance = [group.get("ctr") if group.get("ctr") is not None else group.get("conversions")
                   for group in eligible]
    return len(eligible) >= 2 and len(set(performance)) >= 2


def promote_playbook_candidate(cfg, candidate, summary, regressions_passed):
    """검증된 후보만 원본 백업 후 승격한다. 자동 변경 범위는 playbook뿐이다."""
    if not candidate or not regressions_passed or not _decision_sample_ready(summary):
        return False
    playbook = state_path(cfg, "playbook.md")
    previous = state_path(cfg, "playbook.previous.md")
    os.makedirs(os.path.dirname(playbook), exist_ok=True)
    if os.path.exists(playbook):
        shutil.copyfile(playbook, previous)
    else:
        with open(previous, "w", encoding="utf-8") as f:
            f.write("")
    with open(playbook, "w", encoding="utf-8") as f:
        f.write(candidate)
    with open(state_path(cfg, "playbook_promotion.json"), "w", encoding="utf-8") as f:
        json.dump({"promoted_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                   "model": "openrouter/google/gemini-3.7-flash",
                   "rollback_available": True}, f, ensure_ascii=False, indent=2)
    return True


def rollback_playbook(cfg):
    playbook = state_path(cfg, "playbook.md")
    previous = state_path(cfg, "playbook.previous.md")
    if not os.path.exists(previous):
        return False
    shutil.copyfile(previous, playbook)
    return True


def golden_regressions_passed(cfg):
    payload = read_json(state_path(cfg, "viral-goldens.json"), {})
    categories = {case.get("category") for case in payload.get("cases", [])}
    required = {"generic_roundup", "medical_claim_framing", "ai_report_voice",
                "first_plausible_product", "compliance_dominates_copy",
                "unsupported_viral_claim"}
    return required <= categories


def _insufficient_playbook(summary):
    posts = summary.get("posts_total") or 0
    return (
        "**가설**\n"
        "- 현재 병목은 콘텐츠 취향이 아니라 비교 가능한 표본과 클릭 귀속의 부족입니다.\n\n"
        "**지표**\n"
        f"- 실발행 표본 {posts}건. 각 비교군 3건 기준을 충족하지 않아 훅·포맷·시간대 승자를 선언하지 않습니다.\n"
        "- 클릭값이 null이면 0클릭이 아니라 미계측으로 유지합니다.\n\n"
        "**다음 실험 하나**\n"
        "- 기존 게시 비율과 시간대를 유지하고, KR 판매글의 `kr_link_mode`만 direct/site로 교대합니다. "
        "각 arm 실발행 3건과 서브ID 귀속이 쌓이기 전에는 플레이북을 변경하지 않습니다.\n"
    )


def _north_star_lines(cfg, summary):
    """북극성 섹션 — 수익은 소싱 워커가 쿠팡 파트너스 리포트에서 읽어 state/revenue.json에 기록."""
    click_values = [v.get("clicks") for v in (summary.get("by_type") or {}).values()
                    if v.get("clicks") is not None]
    rev = read_json(state_path(cfg, "revenue.json"), {})
    lines = ["## 북극성 — 월 수수료 1,000만원", ""]
    if rev.get("month_krw") is not None:
        pct = rev["month_krw"] / NORTH_STAR_KRW * 100
        lines.append(f"- 이번 달 확인 수익: {rev['month_krw']:,}원 (목표 대비 {pct:.2f}%) — 기준일 {rev.get('as_of', '?')}")
        if rev.get("by_sub_id"):
            top = sorted(rev["by_sub_id"].items(), key=lambda kv: -kv[1])[:5]
            lines.append("- 서브ID별 상위: " + ", ".join(f"{k} {v:,}원" for k, v in top))
    else:
        lines.append("- 확인 수익: 아직 없음 — 소싱 워커의 수익 동기화(주 1회)가 state/revenue.json을 채울 때까지 대기")
    if click_values:
        lines.append(f"- 이번 주 링크 클릭 합계: {sum(click_values)} (계측된 게시물만)")
    else:
        lines.append("- 이번 주 링크 클릭: 미계측 — 0클릭으로 간주하지 않음")
    return lines


def _evidence_lines(cfg):
    """증거 원장 재고 — 마르면 가치글이 story 폴백으로 축소되므로 조기 경보한다."""
    try:
        import evidence
        inv = evidence.inventory(cfg)
        rejects = read_jsonl(state_path(cfg, "evidence_rejects.jsonl"))[-50:]
    except Exception as e:
        record_error(cfg, "evidence_inventory", e)
        return []

    dist = inv.get("by_distance", {})
    unused = inv.get("unused_by_channel", {})
    lines = ["## 증거 원장 (가치글 입력)", "",
             f"- 원자 총 {inv.get('total', 0)}건 — "
             + " / ".join(f"D{d} {dist.get(d, 0)}건" for d in (0, 1, 2, 3))]
    if unused:
        lines.append("- 채널별 미사용 재고: "
                     + ", ".join(f"{k} {v}건" for k, v in sorted(unused.items())))
    for ckey, left in sorted(unused.items()):
        if left == 0:
            lines.append(f"- ⚠️ {ckey} 재고 소진 — 가치글이 story 폴백으로 돌아감. "
                         f"`harvest.py` 실행 확인 필요")
        elif left <= 2:
            lines.append(f"- ⚠️ {ckey} 재고 {left}건 — 수집 워커 점검 권장")
    if rejects:
        reasons = {}
        for r in rejects:
            for reason in (r.get("reasons") or []):
                key = reason.split(":")[0]
                reasons[key] = reasons.get(key, 0) + 1
        top = sorted(reasons.items(), key=lambda kv: -kv[1])[:3]
        lines.append("- 최근 게이트 반려 사유 상위: "
                     + ", ".join(f"{k} {v}건" for k, v in top))
        if len(rejects) >= 20:
            lines.append("- ⚠️ 반려 누적 20건 이상 — 수집 기준이 아니라 "
                         "워커 프롬프트(`aside-evidence-routine.md`)를 고칠 신호")
    return lines


def _ux_audit_lines(audit):
    lines = ["## UX 발굴 감사 (자동)", "",
             f"- 폼팩터: 총 {audit['formfactors_total']} — 활성 proven {audit['proven_active']} / 활성 novel {audit['novel_active']} / 후보 {audit['candidates']}"]
    if audit["new_this_week"]:
        lines.append("- 이번 주 신규 발굴: " + ", ".join(audit["new_this_week"]))
    if audit["retired"]:
        lines.append("- 저성과 은퇴: " + ", ".join(audit["retired"]))
    for a in audit["alerts"]:
        lines.append(f"- ⚠️ {a}")
    return lines


def run(cfg, dry_run=False):
    if dry_run:
        log("주간 개선(dry): 운영 플레이북·리포트·UX 상태 변경 생략")
        return playbook_hint(cfg)

    summary = analytics.weekly_summary(cfg)
    holds = read_jsonl(state_path(cfg, "holdbox.jsonl"))[-20:]
    try:
        ux_audit = sourcing.update_ux_stats(cfg)
    except Exception as e:
        record_error(cfg, "update_ux_stats", e)
        ux_audit = None

    active_playbook = playbook_hint(cfg)
    if not _decision_sample_ready(summary):
        playbook = active_playbook
        report_playbook = _insufficient_playbook(summary)
    else:
        candidate = generate.llm_call(cfg, IMPROVE_PROMPT, summary, json_mode=False, temperature=0.3)
        promoted = promote_playbook_candidate(cfg, candidate, summary, golden_regressions_passed(cfg))
        playbook = playbook_hint(cfg) if promoted else active_playbook
        report_playbook = candidate if promoted else candidate + "\n\n> 승격 보류: 귀속·성과·골든 회귀 게이트 미통과"

    report = [
        f"# heightcue 주간 리포트 — {time.strftime('%Y-%m-%d')}",
        "",
        "## 지표 요약",
        "```json",
        json.dumps(summary, ensure_ascii=False, indent=2),
        "```",
        "",
        f"## 보류함 ({len(holds)}건 — 사람 확인 필요)",
    ]
    for h in holds:
        report.append(f"- [{h.get('why')}] {str(h.get('comment') or h.get('text') or '')[:80]}")
    report += [""] + _north_star_lines(cfg, summary)
    report += [""] + _evidence_lines(cfg)
    if ux_audit:
        report += [""] + _ux_audit_lines(ux_audit)
    report += ["", "## 이번 주 플레이북 분석", "", report_playbook]
    with open(state_path(cfg, "weekly_report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(report))
    log("주간 개선 완료: 승격 게이트 확인 / weekly_report.md 갱신")

    # Threads 장기 토큰 자동 갱신 (60일 만료 예방 — 주 1회 갱신하고 config.json에 저장)
    if not dry_run:
        updates = {}
        for country, key in (("KR", "kr_access_token"), ("US", "us_access_token")):
            try:
                new_token = publish.refresh_token(cfg, country)
                if new_token:
                    updates[key] = new_token
                    cfg["threads"][key] = new_token
                    log(f"Threads 토큰 갱신({country}) 완료")
            except Exception as e:
                record_error(cfg, f"token_refresh_{country}", e)
        if updates:
            save_threads_tokens(updates)
    return playbook


def playbook_hint(cfg):
    try:
        with open(state_path(cfg, "playbook.md"), encoding="utf-8") as f:
            return f.read()[:1200]
    except Exception:
        return ""
