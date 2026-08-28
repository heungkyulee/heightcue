# -*- coding: utf-8 -*-
"""파이프라인 C-1: 지표 수집과 주간 요약.

- collect(): 최근 발행 글의 조회·좋아요 등 + 계정 clicks(URL별)를 metrics.jsonl에 적재.
- weekly_summary(): 훅 패턴·글 종류별 성과를 집계해 개선 파이프라인(improve)의 입력을 만든다.
- 판매 전환·수익은 쿠팡 파트너스 리포트(서브ID 기준)와 대조 — API 리포트 연동 전에는
  주간 다이제스트에 '대조 필요' 항목으로 표시.
"""
from collections import defaultdict
import re
import time

import publish
from common import append_jsonl, log, read_jsonl, state_path


REQUIRED_ATTRIBUTION_FIELDS = (
    "hook_family", "angle_id", "product_id", "formfactor_id",
    "ux_grade", "country", "post_type", "writer_variant",
)


# --- 영상(UGC) 행 인식 — 추가만 하고 텍스트 경로는 건드리지 않는다 -------------
# 영상 발행 근거는 video_handoff 가 원장 옆 publish_evidence.jsonl 에 쓴다.
# 스키마가 텍스트 글과 다르므로(훅 패턴·writer_variant 가 없고, 대신 잡/QA 계보가
# 있다) 같은 필수 필드표로 재면 영상 행은 전부 '귀속 불완전'로 잘못 집계된다.
VIDEO_POST_TYPE = "video_ugc"

REQUIRED_VIDEO_ATTRIBUTION_FIELDS = (
    "post_type", "country", "product_id",
    "video_job_id", "video_run_id", "qa_report_ref", "media_id",
)


#: post_type 가 빠져도 영상 행임을 알아볼 수 있는 표식. 영상 행을 텍스트
#: 필수 필드표로 재면 없던 '귀속 불완전'이 무더기로 잡힌다.
VIDEO_ROW_MARKERS = ("video_job_id", "video_run_id", "video_sha256")


def is_video_row(row):
    row = row or {}
    if row.get("post_type") == VIDEO_POST_TYPE:
        return True
    return any(row.get(marker) for marker in VIDEO_ROW_MARKERS)


def attribution_gaps(row):
    fields = (REQUIRED_VIDEO_ATTRIBUTION_FIELDS if is_video_row(row)
              else REQUIRED_ATTRIBUTION_FIELDS)
    return [field for field in fields if not row.get(field)]


def _is_dry(row):
    """리허설 산출물이 실험 표본에 섞이지 않게 판별한다."""
    return str(row.get("media_id") or "").startswith("DRY-") or bool(row.get("dry_run"))


def _post_url(post):
    """신규 레코드의 link를 우선하고, 초기 수동 발행분은 본문 URL을 복구한다."""
    if post.get("link"):
        return post["link"]
    match = re.search(r"https?://[^\s]+", post.get("text") or "")
    return match.group(0).rstrip(".,)") if match else None


def analysis_excluded_ids(cfg):
    """삭제 성공 여부와 무관하게 컴플라이언스 교체 요청 글은 성과 학습에서 제외한다."""
    rows = read_jsonl(state_path(cfg, "deletions.jsonl"))
    return {
        str(row.get("media_id")) for row in rows
        if row.get("media_id") and "compliance replacement" in str(row.get("reason") or "").lower()
    }


def collect(cfg, dry_run=False):
    # dry-run Insights는 고정 모의값이다. 이를 운영 metrics에 기록하면 실제 성과처럼
    # 보이므로 리허설에서는 네트워크 조회와 파일 쓰기를 모두 생략한다.
    if dry_run:
        log("지표 수집(dry): 운영 metrics 기록 생략")
        return 0
    published = read_jsonl(state_path(cfg, "published.jsonl"))
    posts = [p for p in published if p.get("media_id") and not _is_dry(p)
             and p.get("meta", {}).get("kind") != "reply"][-40:]
    clicks_by_url = {}
    for country in {p["country"] for p in posts}:
        try:
            clicks_by_url.update(publish.fetch_link_clicks(cfg, country, dry_run=dry_run))
        except Exception as e:
            log(f"clicks 조회 실패({country}): {e}")
    n = 0
    for p in posts:
        try:
            ins = publish.fetch_insights(cfg, p["country"], p["media_id"], dry_run=dry_run)
        except Exception as e:
            log(f"insights 실패 {p['media_id']}: {e}")
            continue
        meta = p.get("meta", {})
        url = _post_url(p)
        metrics_row = {
            "media_id": p["media_id"], "country": p["country"],
            "post_type": meta.get("post_type"),
            "hook_pattern": meta.get("hook_pattern"),
            "hook_family": meta.get("hook_family") or meta.get("hook_pattern"),
            "angle_id": meta.get("angle_id"),
            "product_id": meta.get("product_id"),
            "category": meta.get("category"),
            "formfactor_id": meta.get("formfactor_id"),
            "ux_grade": meta.get("ux_grade"),
            "writer_variant": meta.get("writer_variant"),
            "experiment_id": meta.get("experiment_id"),
            "experiment_arm": meta.get("experiment_arm"),
            "link_mode": meta.get("link_mode"),
            "sub_id": meta.get("sub_id"),
            "url": url,
            "hook": p.get("text", "").splitlines()[0][:60] if p.get("text") else "",
            "insights": ins,
            "link_clicks": clicks_by_url.get(url, None),
            "views_24h": meta.get("views_24h"),
            "views_72h": meta.get("views_72h"),
            "likes": ins.get("likes") if isinstance(ins, dict) else None,
            "replies": ins.get("replies") if isinstance(ins, dict) else None,
            "reposts": ins.get("reposts") if isinstance(ins, dict) else None,
            "saves": ins.get("saves") if isinstance(ins, dict) else None,
            "conversions": meta.get("conversions"),
            "commission": meta.get("commission"),
            "collected_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        metrics_row["attribution_complete"] = not attribution_gaps(metrics_row)
        append_jsonl(state_path(cfg, "metrics.jsonl"), metrics_row)
        n += 1
    log(f"지표 수집: {n}건")
    return n


def weekly_summary(cfg):
    rows = [r for r in read_jsonl(state_path(cfg, "metrics.jsonl")) if not _is_dry(r)]
    if not rows:
        return {"note": "지표 없음"}
    latest = {}
    for r in rows:  # 게시물별 최신 스냅샷만
        latest[r["media_id"]] = r
    excluded_ids = analysis_excluded_ids(cfg)
    excluded = [latest.pop(media_id) for media_id in list(latest) if media_id in excluded_ids]
    by_pattern = defaultdict(list)
    by_type = defaultdict(list)
    by_grade = defaultdict(list)
    by_experiment = defaultdict(lambda: defaultdict(list))
    for r in latest.values():
        views = (r.get("insights") or {}).get("views") or 0
        clicks = r.get("link_clicks")
        attributed = not attribution_gaps(r)
        conversions = r.get("conversions")
        sample = (views, clicks, attributed, conversions)
        by_type[r.get("post_type") or "?"].append(sample)
        if r.get("hook_pattern"):
            by_pattern[r["hook_pattern"]].append(sample)
        if r.get("ux_grade"):
            by_grade[r["ux_grade"]].append(sample)
        if r.get("experiment_id") and r.get("experiment_arm"):
            by_experiment[r["experiment_id"]][r["experiment_arm"]].append(sample)

    def agg(samples):
        v = sum(p[0] for p in samples)
        measured = [(views, clicks) for views, clicks, _, _ in samples if clicks is not None]
        measured_views = sum(views for views, _ in measured)
        c = sum(clicks for _, clicks in measured) if measured else None
        conversions = [value for _, _, _, value in samples if value is not None]
        return {"posts": len(samples), "views": v, "clicks": c,
                "attributed_posts": sum(1 for _, _, attributed, _ in samples if attributed),
                "clicks_measured_posts": len(measured),
                "conversions": sum(conversions) if conversions else None,
                "ctr": round(c / measured_views, 4) if measured_views and c is not None else None}

    summary = {
        "posts_total": len(latest),
        "analysis_excluded": [
            {"media_id": r.get("media_id"), "reason": "compliance_replacement"} for r in excluded
        ],
        "by_type": {k: agg(v) for k, v in by_type.items()},
        "by_hook_pattern": {k: agg(v) for k, v in by_pattern.items()},
        "by_ux_grade": {k: agg(v) for k, v in by_grade.items()},  # proven vs novel 성과 비교",
        "by_experiment": {exp: {arm: agg(values) for arm, values in arms.items()}
                          for exp, arms in by_experiment.items()},
        "top_posts": sorted(
            [{"hook": r.get("hook"), "views": (r.get("insights") or {}).get("views", 0),
              "clicks": r.get("link_clicks")} for r in latest.values()],
            key=lambda x: x["views"], reverse=True)[:5],
        "todo_human": ["쿠팡 파트너스 리포트에서 서브ID별 전환·수익 대조"],
    }
    return summary
