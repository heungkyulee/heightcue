# -*- coding: utf-8 -*-
"""KR 제품 랜딩 페이지 생성·배포 (사이트 경유 A/B의 'site' 암 지원).

렌더링은 sitegen_lt(링크트리 스타일)가 담당한다. 이 모듈은 파일 쓰기·카탈로그/허브
갱신·git 배포·라이브 검증·폴백 신호만 책임진다.
- 배포 실패 시 None 반환 → 호출부가 직링크(고지 포함) 모드로 폴백 (fail-safe).
"""
import os
import re
import subprocess
import time

import requests

import sitegen_lt
from common import log

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE_BASE = sitegen_lt.SITE_BASE


def _slug(product_key):
    s = re.sub(r"[^a-z0-9\-]", "-", str(product_key).lower())
    return re.sub(r"-{2,}", "-", s).strip("-") or f"p-{int(time.time())}"


def _git(args):
    return subprocess.run(["git"] + args, cwd=REPO, capture_output=True, text=True, timeout=120)


def us_page(cfg, product, master, deploy=True):
    """Build and optionally deploy a US approved-product landing page."""
    slug = _slug(product.get("product_key"))
    rel = f"us/p/{slug}.html"
    url = f"{SITE_BASE}/{rel}"
    contract_master = dict(master or {})
    for key in ("mechanism", "failure_mode", "skip_if"):
        if product.get(key):
            contract_master[key] = product[key]
    if isinstance(contract_master.get("skip_if"), str):
        contract_master["skip_if"] = [contract_master["skip_if"]]
    html_text = sitegen_lt.render_product_us(
        {**product, "_slug": slug}, contract_master
    )

    if not deploy:
        prev_dir = os.path.join(cfg["paths"]["state_dir"], "preview-pages")
        os.makedirs(prev_dir, exist_ok=True)
        with open(os.path.join(prev_dir, f"us-{slug}.html"), "w", encoding="utf-8") as f:
            f.write(html_text)
        log(f"US page(rehearsal): state/preview-pages/us-{slug}.html (no deploy)")
        return url

    path = os.path.join(REPO, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    already_live = False
    try:
        already_live = requests.get(url, timeout=10).status_code == 200
    except Exception:
        pass
    with open(path, "w", encoding="utf-8") as f:
        f.write(html_text)

    r = _git(["add", rel])
    if r.returncode != 0:
        log(f"US page deploy failed(git add): {r.stderr.strip()[:120]}")
        return None
    if _git(["diff", "--cached", "--quiet"]).returncode == 0:
        log(f"US page: no changes → {url}")
        return url
    r = _git(["commit", "-m", f"page: US guide {slug}"])
    if r.returncode != 0:
        log(f"US page deploy failed(commit): {r.stderr.strip()[:120]}")
        return None
    r = _git(["push", "origin", "main"])
    if r.returncode != 0:
        _git(["pull", "--rebase", "origin", "main"])
        r = _git(["push", "origin", "main"])
    if r.returncode != 0:
        log(f"US page deploy failed(push): {r.stderr.strip()[:120]}")
        return None

    if already_live:
        return url
    for _ in range(10):
        time.sleep(15)
        try:
            if requests.get(url, timeout=10).status_code == 200:
                log(f"US page live: {url}")
                return url
        except Exception:
            pass
    log(f"US page propagation check failed after push: {url}")
    return None


def kr_page(cfg, product, master, deploy=True):
    """페이지 생성(+허브 갱신+배포·검증). 반환: 라이브 URL 또는 None(폴백 신호)."""
    slug = _slug(product.get("product_key"))
    rel = f"kr/p/{slug}.html"
    url = f"{SITE_BASE}/{rel}"
    html_text = sitegen_lt.render_product({**product, "_slug": slug}, master)

    if not deploy:
        prev_dir = os.path.join(cfg["paths"]["state_dir"], "preview-pages")
        os.makedirs(prev_dir, exist_ok=True)
        with open(os.path.join(prev_dir, f"{slug}.html"), "w", encoding="utf-8") as f:
            f.write(html_text)
        log(f"페이지(리허설): state/preview-pages/{slug}.html (배포 없음)")
        return url

    path = os.path.join(REPO, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    already_live = False
    try:
        already_live = requests.get(url, timeout=10).status_code == 200
    except Exception:
        pass
    with open(path, "w", encoding="utf-8") as f:
        f.write(html_text)
    sitegen_lt.update_catalog(REPO, product, slug)

    r = _git(["add", "kr", "assets/brand/heightcue-pfp.jpg"])
    if r.returncode != 0:
        log(f"페이지 배포 실패(git add): {r.stderr.strip()[:120]}")
        return None
    if _git(["diff", "--cached", "--quiet"]).returncode == 0:
        log(f"페이지: 변경 없음 (이미 최신) → {url}")
        return url
    r = _git(["commit", "-m", f"page: KR guide {slug} + hub refresh"])
    if r.returncode != 0:
        log(f"페이지 배포 실패(commit): {r.stderr.strip()[:120]}")
        return None
    r = _git(["push", "origin", "main"])
    if r.returncode != 0:
        _git(["pull", "--rebase", "origin", "main"])
        r = _git(["push", "origin", "main"])
    if r.returncode != 0:
        log(f"페이지 배포 실패(push): {r.stderr.strip()[:120]} → 직링크 폴백")
        return None

    if already_live:  # 갱신이면 전파 대기 불필요
        return url
    for _ in range(10):  # 신규 페이지: 최대 ~150초 전파 대기
        time.sleep(15)
        try:
            if requests.get(url, timeout=10).status_code == 200:
                log(f"페이지 라이브 확인: {url}")
                return url
        except Exception:
            pass
    log(f"페이지 전파 확인 실패(푸시는 성공): {url} → 직링크 폴백")
    return None
