#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HeightCue 바이럴 UGC 수집 루틴 — 읽기 전용 · 바운드 · 픽스처 dry-run.

이 모듈은 Task 6 의 ``viral_ugc.py`` 패턴 원장에 넣을 **Observation** 을 만든다.
직접 무엇도 관측하지 않는다 — 관측은 Aside CLI(브라우저) 와 agent-reach/yt-dlp
(유튜브 메타데이터) 가 하고, 이 모듈은 그 실행을 **좁게 묶고 검증**한다.

설계 불변식:

1. **읽기 전용** — 메타데이터와 URL 만 수집한다. 크리에이터의 이미지·영상을
   내려받거나 저장하지 않고, 어떤 플랫폼에도 쓰기(게시·좋아요·팔로우·댓글)를
   하지 않는다. ``assert_read_only()`` 가 모든 argv 를 통과 전에 검사한다.
2. **바운드** — 쿼리 수·페이지 수·게시물 수·관측 수·월클럭 예산이 전부 이름
   붙은 상수로 고정돼 있다. 이전 라이브 시도가 300초에서 죽었으므로 예산은
   그보다 확실히 작다. 한도에 닿으면 조용히 멈추고 ``bounds_hit`` 에 남긴다.
3. **추정 금지** — 화면에 없던 지표는 ``None`` 으로 남긴다. 절대 0 으로 채우지
   않는다. 이 규칙은 ``viral_ugc.EngagementSnapshot`` 이 구조적으로 강제한다.
4. **출처 필수** — 모든 관측은 source_url 과 observed_at 을 갖는다.

브라우저 표준: 브라우저가 필요한 단계는 **오직 Aside CLI** 로만 나간다
(``aside --account u0 exec`` / ``repl``). Playwright·Selenium·browser-use·
내장 브라우저 툴은 사용하지 않는다. 유튜브는 agent-reach/yt-dlp 메타데이터만.

의존성: 표준 라이브러리만.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import viral_ugc  # noqa: E402
from viral_ugc import (  # noqa: E402
    FORBIDDEN_MEDIA_KEYS,
    MediaPolicyError,
    Observation,
    ViralUGCError,
)

# ---------------------------------------------------------------------------
# 바운드 상수 (천장값 — CollectionBounds 는 이 위로 올라갈 수 없다)
# ---------------------------------------------------------------------------

#: 한 번의 실행에서 던질 수 있는 검색 쿼리 최대 수.
MAX_QUERIES_PER_RUN = 4

#: 쿼리 하나당 넘길 수 있는 페이지(스크롤 배치) 최대 수.
MAX_PAGES_PER_QUERY = 2

#: 쿼리 하나당 훑을 수 있는 게시물 최대 수.
MAX_POSTS_PER_QUERY = 12

#: 한 번의 실행에서 받아들일 관측 레코드 최대 수.
MAX_OBSERVATIONS_PER_RUN = 24

#: 실행 전체 월클럭 예산(초). 이전 라이브 시도는 300초에서 죽었다.
WALL_CLOCK_BUDGET_SECONDS = 180

#: 외부 명령 하나당 타임아웃(초). 어떤 단계도 이보다 오래 매달리지 않는다.
STEP_TIMEOUT_SECONDS = 45

#: 기본 대상 시장.
DEFAULT_MARKETS: Tuple[str, ...] = ("KR", "US")

#: Aside 계정 (브라우저 표준).
ASIDE_ACCOUNT = "u0"

#: 수집 브리핑 문서 — 라이브 실행 시 Aside 가 따르는 지시서.
COLLECTION_BRIEF = "docs/operations/viral-ugc-collection-brief.md"

# --- 읽기 전용 가드 ---------------------------------------------------------

#: 쓰기·상호작용을 암시하는 토큰. argv 어디든 나오면 거부한다.
WRITE_INTENT_TOKENS = (
    "post", "publish", "reply", "comment", "like", "unlike", "follow",
    "unfollow", "repost", "share", "dm", "message", "subscribe", "upload",
    "delete", "block", "mute", "vote", "submit",
)

#: 미디어 내려받기를 암시하는 토큰.
DOWNLOAD_INTENT_TOKENS = (
    "download", "-o", "--output", "--write-thumbnail", "--write-video",
    "wget", "curl -o",
)

#: 한국어 쓰기·상호작용 동사. QUERY_SEEDS 가 한국어이므로 영어 denylist 만으로는
#: 구멍이 생긴다. 부분 문자열로 매칭한다 (한국어는 공백 단어 경계가 없다).
KOREAN_WRITE_INTENT_TOKENS = (
    "좋아요", "팔로우", "언팔", "게시", "댓글", "답글", "공유", "구독",
    "업로드", "삭제", "차단", "신고", "발행", "작성", "전송", "리포스트",
    "누르", "달기", "쓰기", "저장", "다운로드", "내려받",
)

#: yt-dlp 는 **허용목록**으로만 통과한다 (denylist 는 --write-thumbnails 같은
#: 복수형·신규 플래그를 놓친다). 값을 하나 먹는 플래그는 True.
YTDLP_ALLOWED_FLAGS: Dict[str, bool] = {
    "--skip-download": False,
    "--simulate": False,
    "--dump-json": False,
    "--dump-single-json": False,
    "--no-download": False,
    "--list-subs": False,
    "--list-formats": False,
    "--no-playlist": False,
    "--no-warnings": False,
    "--quiet": False,
    "--ignore-config": False,
    "--no-write-subs": False,
    "--print": True,
    "--socket-timeout": True,
    "--retries": True,
    "--playlist-items": True,
}

#: 메타데이터 전용임을 증명하는 플래그 (최소 하나 필수).
YTDLP_METADATA_ONLY = ("--skip-download", "--dump-json",
                       "--dump-single-json", "--simulate", "--print",
                       "--list-subs")

#: HTTP 쓰기 메서드.
WRITE_HTTP_METHODS = ("POST", "PUT", "PATCH", "DELETE")


# ---------------------------------------------------------------------------
# 예외
# ---------------------------------------------------------------------------


class CollectionError(RuntimeError):
    """수집 루틴 공통 베이스."""


class BoundsError(CollectionError):
    """바운드 설정이 잘못됐다 (0 이하이거나 모듈 천장 초과)."""


class ReadOnlyViolation(CollectionError):
    """읽기 전용 계약 위반 — 쓰기/다운로드성 명령을 막았다."""


class LiveCollectionDisabled(CollectionError):
    """라이브 수집이 명시적으로 활성화되지 않았다."""


class LiveParseError(CollectionError):
    """Aside stdout 이 비어 있지 않은데 관측으로 파싱되지 않았다.

    이 예외가 존재하는 이유: 조용한 ``[]`` 는 "바이럴 게시물이 없다" 와
    "파서가 stdout 모양을 못 알아봤다" 를 구별할 수 없게 만든다. 빈 결과는
    **증명된 빈 결과**여야 한다.
    """


class UnapprovedQuerySeeds(CollectionError):
    """운영자 승인 없는 QUERY_SEEDS 로 라이브를 돌리려 했다."""


# ---------------------------------------------------------------------------
# 바운드
# ---------------------------------------------------------------------------


@dataclass
class CollectionBounds:
    """이번 실행의 실효 한도. 모듈 상수를 넘길 수 없다 (천장 고정)."""

    max_queries_per_run: int = MAX_QUERIES_PER_RUN
    max_pages_per_query: int = MAX_PAGES_PER_QUERY
    max_posts_per_query: int = MAX_POSTS_PER_QUERY
    max_observations_per_run: int = MAX_OBSERVATIONS_PER_RUN
    wall_clock_budget_seconds: float = WALL_CLOCK_BUDGET_SECONDS
    step_timeout_seconds: float = STEP_TIMEOUT_SECONDS

    _CEILINGS = {
        "max_queries_per_run": MAX_QUERIES_PER_RUN,
        "max_pages_per_query": MAX_PAGES_PER_QUERY,
        "max_posts_per_query": MAX_POSTS_PER_QUERY,
        "max_observations_per_run": MAX_OBSERVATIONS_PER_RUN,
        "wall_clock_budget_seconds": WALL_CLOCK_BUDGET_SECONDS,
        "step_timeout_seconds": STEP_TIMEOUT_SECONDS,
    }

    def validate(self) -> "CollectionBounds":
        for name, ceiling in self._CEILINGS.items():
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise BoundsError(f"{name} 는 숫자여야 한다: {value!r}")
            if value <= 0:
                raise BoundsError(f"{name} 는 0 보다 커야 한다: {value!r}")
            if value > ceiling:
                raise BoundsError(
                    f"{name}={value} 가 모듈 천장 {ceiling} 을 넘는다 — "
                    "수집 한도는 완화할 수 없다 (강화만 가능)")
        return self

    def to_dict(self) -> Dict[str, Any]:
        return {name: getattr(self, name) for name in self._CEILINGS}

    def timeout_for(self, elapsed: float) -> Optional[float]:
        """남은 예산에 맞춰 **줄인** 단계 타임아웃.

        이것이 월클럭 예산을 실제 천장으로 만든다. 단계 사이에서만 검사하면
        진행 중인 단계가 step_timeout 만큼 더 달릴 수 있어 최악의 경우가
        예산을 넘는다. 남은 예산이 없으면 ``None``.
        """
        remaining = self.wall_clock_budget_seconds - max(0.0, elapsed)
        if remaining <= 0:
            return None
        return min(self.step_timeout_seconds, remaining)


# ---------------------------------------------------------------------------
# 읽기 전용 가드
# ---------------------------------------------------------------------------


def _normalize(argv: Sequence[str]) -> Tuple[List[str], str]:
    parts = [str(a) for a in argv]
    return parts, " ".join(parts).lower()


def assert_read_only(argv: Sequence[str]) -> Sequence[str]:
    """쓰기·다운로드 의도가 보이면 실행 전에 거부한다.

    보수적으로 판단한다 — 애매하면 막는다. 통과시키지 못해 놓치는 관측보다
    실수로 남의 타임라인에 쓰는 쪽이 비교할 수 없이 나쁘다.
    """
    parts, joined = _normalize(argv)
    if not parts:
        raise ReadOnlyViolation("빈 명령은 실행하지 않는다")

    program = os.path.basename(parts[0]).lower()

    for method in WRITE_HTTP_METHODS:
        if method in parts or f"-x {method.lower()}" in joined:
            raise ReadOnlyViolation(
                f"쓰기 HTTP 메서드 {method} 감지 — 수집은 읽기 전용이다: {parts!r}")

    if program in ("wget", "curl"):
        raise ReadOnlyViolation(
            f"{program} 직접 호출 금지 — 브라우저 단계는 Aside CLI, "
            f"유튜브는 agent-reach/yt-dlp 메타데이터만: {parts!r}")

    if program == "yt-dlp":
        if not any(tok in parts for tok in YTDLP_METADATA_ONLY):
            raise ReadOnlyViolation(
                "yt-dlp 는 메타데이터 전용 플래그"
                f"({', '.join(YTDLP_METADATA_ONLY)}) 없이 쓸 수 없다 — "
                f"미디어 다운로드 금지: {parts!r}")
        # 허용목록: 모르는 플래그는 전부 거부한다. denylist 는 --write-thumbnails
        # (복수형), -P/--paths, 신규 플래그를 구조적으로 놓친다.
        idx = 1
        while idx < len(parts):
            tok = parts[idx]
            if tok.startswith("-"):
                flag = tok.split("=", 1)[0]
                if flag not in YTDLP_ALLOWED_FLAGS:
                    raise ReadOnlyViolation(
                        f"yt-dlp 플래그 {flag!r} 는 허용목록에 없다 — "
                        "메타데이터 전용 플래그만 쓸 수 있다 "
                        f"(파일을 쓰는 --write-*/-P/--paths 포함 전부 금지): "
                        f"{parts!r}")
                if YTDLP_ALLOWED_FLAGS[flag] and "=" not in tok:
                    idx += 1  # 이 플래그의 값은 건너뛴다
            idx += 1
        return argv

    for token in DOWNLOAD_INTENT_TOKENS:
        if token in parts or f" {token} " in f" {joined} ":
            raise ReadOnlyViolation(
                f"다운로드 의도 토큰 {token!r} 감지 — 크리에이터 미디어는 "
                f"저장하지 않는다: {parts!r}")

    for token in WRITE_INTENT_TOKENS:
        if any(token == w.strip(".,:;\"'") for w in joined.split()):
            raise ReadOnlyViolation(
                f"쓰기 의도 토큰 {token!r} 감지 — 게시·좋아요·팔로우·댓글 "
                f"금지: {parts!r}")

    # 한국어는 공백 단어 경계가 없으므로 부분 문자열로 본다.
    for token in KOREAN_WRITE_INTENT_TOKENS:
        if token in joined:
            raise ReadOnlyViolation(
                f"한국어 쓰기 의도 토큰 {token!r} 감지 — 게시·좋아요·팔로우·"
                f"댓글·구독 금지: {parts!r}")

    return argv


# ---------------------------------------------------------------------------
# runner 시임 (codex_image_bridge.py 와 같은 패턴)
# ---------------------------------------------------------------------------


def _subprocess_runner(argv: Sequence[str],
                       timeout: Optional[float] = None) -> Dict[str, Any]:
    """실제 외부 명령 실행기. 테스트에서는 절대 쓰이지 않는다."""
    assert_read_only(argv)
    proc = subprocess.run(list(argv), capture_output=True, text=True,
                          timeout=timeout or STEP_TIMEOUT_SECONDS,
                          check=False)
    return {"argv": list(argv), "returncode": proc.returncode,
            "stdout": proc.stdout, "stderr": proc.stderr}


def _run(runner, argv: Sequence[str], timeout: float) -> Dict[str, Any]:
    assert_read_only(argv)
    return runner(list(argv), timeout=timeout)


# ---------------------------------------------------------------------------
# 결과
# ---------------------------------------------------------------------------


@dataclass
class CollectionResult:
    """한 번의 수집 실행 산출물 — 관측·거부·바운드·전후 점검."""

    source: str
    observations: List[Observation] = field(default_factory=list)
    rejected: List[Dict[str, Any]] = field(default_factory=list)
    bounds_hit: List[str] = field(default_factory=list)
    bounds: Dict[str, Any] = field(default_factory=dict)
    plan: List[Dict[str, Any]] = field(default_factory=list)
    preflight: Optional[Dict[str, Any]] = None
    postflight: Optional[Dict[str, Any]] = None
    started_at: str = ""
    elapsed_seconds: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "observation_count": len(self.observations),
            "observations": [o.to_dict() for o in self.observations],
            "rejected": list(self.rejected),
            "bounds_hit": list(self.bounds_hit),
            "bounds": dict(self.bounds),
            "plan": list(self.plan),
            "preflight": self.preflight,
            "postflight": self.postflight,
            "started_at": self.started_at,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
        }


# ---------------------------------------------------------------------------
# 계획 수립
# ---------------------------------------------------------------------------

#: ⚠️ **운영자 미승인 플레이스홀더.** 아래 쿼리는 구현자가 임시로 적은 것이며
#: 운영자가 검토·승인한 검색어가 **아니다.** SSOT §0 카테고리 하드락 안에는
#: 있지만, 라이브 실행 전에 운영자가 직접 확인하고 아래
#: ``QUERY_SEEDS_APPROVED`` 를 True 로 바꿔야 한다. 그 전까지 라이브 수집은
#: ``UnapprovedQuerySeeds`` 로 거부된다. (docs/operations/
#: viral-ugc-collection-brief.md §3.1 참조)
QUERY_SEEDS: Dict[str, Tuple[str, ...]] = {
    "KR": ("아이 키 성장 수면 루틴", "아이 자세 교정 후기"),
    "US": ("kids growth nutrition routine", "children posture habit"),
}

#: 위 QUERY_SEEDS 를 운영자가 승인했는가. **기본 False** — 승인 없이는 라이브
#: 수집이 시작되지 않는다. 승인 시 이 값을 True 로 바꾸고 커밋한다.
QUERY_SEEDS_APPROVED = False


def assert_query_seeds_approved() -> None:
    """운영자 승인 없는 시드로 라이브를 돌리지 못하게 막는다."""
    if not QUERY_SEEDS_APPROVED:
        raise UnapprovedQuerySeeds(
            "QUERY_SEEDS 는 구현자가 적은 플레이스홀더이며 운영자 승인을 받지 "
            "않았다. 라이브 수집 전에 검색어를 검토하고 "
            "QUERY_SEEDS_APPROVED=True 로 바꿔라 "
            f"(현재 시드: {QUERY_SEEDS})")


def build_plan(markets: Sequence[str] = DEFAULT_MARKETS,
               bounds: Optional[CollectionBounds] = None
               ) -> List[Dict[str, Any]]:
    """실행 전에 **전체 작업량을 확정**한다 — 무한 탐색 경로가 없다."""
    bounds = (bounds or CollectionBounds()).validate()
    steps: List[Dict[str, Any]] = []
    for market in markets:
        for query in QUERY_SEEDS.get(market, ()):
            if len(steps) >= bounds.max_queries_per_run:
                return steps
            steps.append({
                "market": market,
                "query": query,
                "pages": bounds.max_pages_per_query,
                "max_posts": bounds.max_posts_per_query,
                "brief": COLLECTION_BRIEF,
            })
    return steps


def aside_command(step: Dict[str, Any]) -> List[str]:
    """이 단계에 해당하는 Aside CLI argv (브라우저 표준 — 유일한 경로).
    목표문은 **전적으로 기계 생성**이다. 호출자가 자유형 목표문을 넣을 수 있는
    경로는 존재하지 않으며, 쿼리는 ``QUERY_SEEDS`` 표에 실제로 있는 값만
    허용한다. 그래야 ``assert_read_only`` 가 검사할 문자열의 공간이 유한하다.
    마지막에 자기 자신을 가드에 통과시켜, 통과 못 하는 목표문은 만들지 않는다.
    """
    query = step["query"]
    if query not in tuple(QUERY_SEEDS.get(step["market"], ())):
        raise ReadOnlyViolation(
            f"쿼리 {query!r} 가 승인된 QUERY_SEEDS 표에 없다 — 목표문은 "
            "기계 생성만 허용한다 (자유형 목표문 금지)")
    goal = (
        f"Read-only observation task. Obey the brief at {step['brief']}. "
        f"Market {step['market']}. Inspect at most {step['max_posts']} public "
        f"results across at most {step['pages']} scroll batches for the query "
        f"\"{query}\". Return structured JSON observations containing "
        f"only: source_url, observed_at, visible engagement counters that are "
        f"actually on screen, product_id, category. Omit any counter that is "
        f"not visible — never guess or fill in zero. Do not save any image or "
        f"video. Do not interact with any account."
    )
    argv = ["aside", "--account", ASIDE_ACCOUNT, "exec", goal]
    assert_read_only(argv)
    return argv


# ---------------------------------------------------------------------------
# 레코드 정규화
# ---------------------------------------------------------------------------


def _scrub_media(row: Dict[str, Any], strict: bool) -> None:
    """미디어 사본 키가 있으면 **어느 모드에서든** MediaPolicyError 를 던진다.

    strict 와 lenient 의 차이는 여기가 아니라 호출자에 있다 — lenient 는 이
    예외를 잡아 해당 레코드를 ``rejected`` 에 사유와 함께 남기고 계속하고,
    strict 는 그대로 전파해 실행을 세운다. 어느 쪽도 미디어 키를 통과시키지
    않는다.
    """
    present = [k for k in FORBIDDEN_MEDIA_KEYS if k in row]
    if not present:
        return
    if strict:
        raise MediaPolicyError(
            f"수집 레코드에 미디어 사본 필드 {present} 가 있다 — "
            "크리에이터 미디어는 다운로드·저장하지 않는다")
    raise MediaPolicyError(f"미디어 사본 필드 {present} 때문에 레코드를 버렸다")


def normalize_record(row: Dict[str, Any], strict: bool = True) -> Observation:
    """원시 레코드 → 검증된 Observation. 미관측 지표는 손대지 않는다."""
    if not isinstance(row, dict):
        raise ViralUGCError(f"레코드는 dict 여야 한다: {row!r}")
    _scrub_media(row, strict)
    obs = Observation.from_dict(row)
    obs.validate()
    return obs


def _iter_fixture_rows(path: str):
    """픽스처를 (lineno, row) 로 흘린다.

    깨진 JSON 줄은 ``ViralUGCError`` 로 바꿔 던진다 — 원래의
    ``json.JSONDecodeError`` 는 제너레이터 안에서 collect() 의 try 바깥으로
    새어 나가 lenient 모드에서도 main() 을 트레이스백으로 죽였다.
    """
    with open(path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line or line.startswith("//"):
                continue
            try:
                data = json.loads(line)
            except ValueError as exc:
                yield lineno, _MalformedLine(f"{lineno}행 JSON 파싱 실패: {exc}")
                continue
            if data.get("_fixture_note") and "observation_id" not in data:
                continue  # 픽스처 헤더 주석
            yield lineno, data


class _MalformedLine:
    """깨진 픽스처 줄 표식 — collect() 안에서 ViralUGCError 로 승격된다."""

    def __init__(self, message: str) -> None:
        self.message = message


# ---------------------------------------------------------------------------
# 전후 점검 (agent-reach)
# ---------------------------------------------------------------------------


def run_preflight(runner, timeout: float) -> Dict[str, Any]:
    """YouTube 수집 전 ``agent-reach doctor --json``."""
    return _run(runner, ["agent-reach", "doctor", "--json"], timeout)


def run_postflight(runner, timeout: float) -> Dict[str, Any]:
    """실질적 수집 후 ``agent-reach check-update``."""
    return _run(runner, ["agent-reach", "check-update"], timeout)


# ---------------------------------------------------------------------------
# 수집
# ---------------------------------------------------------------------------


def collect(dry_run: bool = True,
            fixture_path: Optional[str] = None,
            markets: Sequence[str] = DEFAULT_MARKETS,
            bounds: Optional[CollectionBounds] = None,
            strict: bool = True,
            runner: Optional[Callable] = None,
            clock: Optional[Callable[[], float]] = None,
            preflight: bool = False,
            postflight: bool = False,
            allow_live: bool = False) -> CollectionResult:
    """바운드 안에서 관측을 수집한다.

    ``dry_run=True`` (기본) 이면 픽스처만 읽는다 — 외부 명령 0건, 네트워크 0건.
    라이브 수집은 ``dry_run=False`` + ``allow_live=True`` 를 둘 다 요구한다.
    """
    bounds = (bounds or CollectionBounds()).validate()
    runner = runner or _subprocess_runner
    clock = clock or time.monotonic

    if not dry_run and not allow_live:
        raise LiveCollectionDisabled(
            "라이브 수집은 allow_live=True 로 명시해야 한다 — "
            "기본 경로는 픽스처 dry-run 이다 (이전 라이브 시도 300초 타임아웃)")
    if not dry_run:
        assert_query_seeds_approved()

    started = clock()
    result = CollectionResult(
        source="fixture" if dry_run else "live",
        bounds=bounds.to_dict(),
        plan=build_plan(markets, bounds),
        started_at=datetime.now(timezone.utc).astimezone().isoformat(),
    )

    def _step_timeout() -> float:
        """남은 예산에 맞춰 줄인 타임아웃. 예산이 끝났으면 실행하지 않는다."""
        remaining = bounds.timeout_for(clock() - started)
        if remaining is None:
            raise _BudgetExhausted()
        return remaining

    if preflight:
        result.preflight = run_preflight(runner, _step_timeout())

    wanted = tuple(markets)

    if dry_run:
        path = fixture_path or os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "fixtures", "viral_ugc_sample.jsonl")
        for lineno, row in _iter_fixture_rows(path):
            if clock() - started >= bounds.wall_clock_budget_seconds:
                result.bounds_hit.append("wall_clock_budget_seconds")
                break
            if len(result.observations) >= bounds.max_observations_per_run:
                result.bounds_hit.append("max_observations_per_run")
                break
            try:
                if isinstance(row, _MalformedLine):
                    raise ViralUGCError(row.message)
                obs = normalize_record(row, strict=strict)
            except ViralUGCError as exc:
                if strict:
                    raise
                result.rejected.append({"line": lineno, "error": str(exc)})
                continue
            if obs.market not in wanted:
                continue
            result.observations.append(obs)
    else:  # pragma: no cover - 라이브는 별도 검증 태스크에서만 실행한다
        for step in result.plan:
            step_timeout = bounds.timeout_for(clock() - started)
            if step_timeout is None:
                result.bounds_hit.append("wall_clock_budget_seconds")
                break
            if len(result.observations) >= bounds.max_observations_per_run:
                result.bounds_hit.append("max_observations_per_run")
                break
            call = _run(runner, aside_command(step), step_timeout)
            try:
                rows = _parse_live_stdout(call.get("stdout", ""))
            except LiveParseError as exc:
                if strict:
                    raise
                result.rejected.append(
                    {"step": step["query"], "reason": "live_parse_failed",
                     "error": str(exc)})
                continue
            for row in rows:
                if len(result.observations) >= bounds.max_observations_per_run:
                    result.bounds_hit.append("max_observations_per_run")
                    break
                try:
                    result.observations.append(
                        normalize_record(row, strict=strict))
                except ViralUGCError as exc:
                    if strict:
                        raise
                    result.rejected.append(
                        {"step": step["query"], "error": str(exc)})

    if postflight:
        try:
            result.postflight = run_postflight(runner, _step_timeout())
        except _BudgetExhausted:
            result.bounds_hit.append("wall_clock_budget_seconds")

    result.elapsed_seconds = max(0.0, clock() - started)
    result.bounds_hit = sorted(set(result.bounds_hit))
    return result


class _BudgetExhausted(CollectionError):
    """월클럭 예산이 끝나 더 이상 외부 명령을 시작하지 않는다."""


def _parse_live_stdout(stdout: str) -> List[Dict[str, Any]]:
    """Aside stdout 에서 관측 레코드를 뽑는다.

    **빈 결과는 증명돼야 한다.** stdout 이 비었으면 ``[]`` 를 돌려주지만,
    비어 있지 않은데 아는 모양(배열 / ``{"observations": [...]}`` / JSONL)
    으로 읽히지 않으면 ``LiveParseError`` 를 던진다. 조용한 ``[]`` 는
    "바이럴 게시물이 없었다" 와 "파서가 Aside 출력 계약을 모른다" 를
    구별할 수 없게 만들고, 그게 정확히 라이브 검증 태스크가 필요로 하는 신호다.
    """
    text = (stdout or "").strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except ValueError:
        rows: List[Dict[str, Any]] = []
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    parsed = json.loads(line)
                except ValueError:
                    continue
                if isinstance(parsed, dict):
                    rows.append(parsed)
        if not rows:
            raise LiveParseError(
                "Aside stdout 이 비어 있지 않은데 관측으로 파싱되지 않았다 — "
                "빈 결과로 조용히 넘기지 않는다. 원문 stdout: "
                f"{text[:2000]!r}")
        return rows
    if isinstance(data, dict):
        if "observations" not in data:
            raise LiveParseError(
                "Aside stdout JSON 에 'observations' 키가 없다 — 출력 계약이 "
                f"바뀌었을 수 있다. 원문 stdout: {text[:2000]!r}")
        data = data.get("observations") or []
    if not isinstance(data, list):
        raise LiveParseError(
            "Aside stdout 의 observations 가 배열이 아니다. 원문 stdout: "
            f"{text[:2000]!r}")
    return [r for r in data if isinstance(r, dict)]


# ---------------------------------------------------------------------------
# 저장
# ---------------------------------------------------------------------------


def write_observations(path: str, observations: Sequence[Observation]) -> str:
    """검증된 관측을 JSONL 에 **덧붙인다** (원자적·크래시 안전).

    덮어쓰기가 아니라 append 인 이유: 출력 경로는
    ``state/viral_ugc/incoming.jsonl`` 이라는 누적 인박스이고, 덮어쓰면 두 번째
    실행이 첫 실행의 관측을 조용히 파괴한다.

    크래시 안전성: 모든 레코드를 **먼저 메모리에서 직렬화·검증**한 뒤 한 번의
    ``write()`` 로 O_APPEND 파일에 붙이고 fsync 한다. 검증 실패는 파일을 건드리기
    전에 터지므로 반쪽 배치가 남지 않는다.
    """
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    lines = []
    for obs in observations:
        obs.validate()
        lines.append(json.dumps(obs.to_dict(), ensure_ascii=False) + "\n")
    if not lines:
        return path
    payload = "".join(lines)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, payload.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    return path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="collect_viral_ugc",
        description="읽기 전용·바운드 바이럴 UGC 관측 수집 (기본: 픽스처 dry-run)")
    p.add_argument("--dry-run", action="store_true",
                   help="픽스처만 읽는다. 외부 명령·네트워크 0건.")
    p.add_argument("--allow-live", action="store_true",
                   help="라이브 수집을 명시적으로 허용 (Aside CLI 경유).")
    p.add_argument("--fixture", default=None, help="dry-run 입력 JSONL 경로")
    p.add_argument("--out", default=None, help="관측 출력 JSONL 경로")
    p.add_argument("--market", action="append", choices=list(viral_ugc.MARKETS),
                   help="대상 시장 (반복 가능). 기본 KR+US")
    p.add_argument("--max-observations", type=int, default=None)
    p.add_argument("--budget-seconds", type=float, default=None)
    p.add_argument("--lenient", action="store_true",
                   help="검증 실패 레코드를 버리고 계속한다 (기본은 즉시 실패)")
    p.add_argument("--preflight", action="store_true",
                   help="agent-reach doctor --json 실행")
    p.add_argument("--postflight", action="store_true",
                   help="agent-reach check-update 실행")
    p.add_argument("--json", action="store_true", help="요약을 JSON 으로 출력")
    return p


def main(argv: Optional[Sequence[str]] = None,
         runner: Optional[Callable] = None) -> int:
    args = _build_parser().parse_args(list(argv) if argv is not None else None)

    if not args.dry_run and not args.allow_live:
        print("오류: --dry-run 또는 --allow-live 중 하나를 명시해야 한다. "
              "라이브 수집은 절대 기본값이 아니다.", file=sys.stderr)
        return 2

    bounds = CollectionBounds()
    if args.max_observations is not None:
        bounds.max_observations_per_run = args.max_observations
    if args.budget_seconds is not None:
        bounds.wall_clock_budget_seconds = args.budget_seconds

    try:
        result = collect(
            dry_run=args.dry_run,
            fixture_path=args.fixture,
            markets=tuple(args.market) if args.market else DEFAULT_MARKETS,
            bounds=bounds,
            strict=not args.lenient,
            runner=runner,
            preflight=args.preflight,
            postflight=args.postflight,
            allow_live=args.allow_live,
        )
    except (CollectionError, ViralUGCError) as exc:
        print(f"수집 실패: {exc}", file=sys.stderr)
        return 1

    if args.out:
        write_observations(args.out, result.observations)

    summary = result.to_dict()
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"source={result.source} "
              f"observations={len(result.observations)} "
              f"rejected={len(result.rejected)} "
              f"bounds_hit={result.bounds_hit or '-'} "
              f"elapsed={result.elapsed_seconds:.2f}s")
        if args.out:
            print(f"wrote: {args.out}")
    return 0


__all__ = [
    "MAX_QUERIES_PER_RUN", "MAX_PAGES_PER_QUERY", "MAX_POSTS_PER_QUERY",
    "MAX_OBSERVATIONS_PER_RUN", "WALL_CLOCK_BUDGET_SECONDS",
    "STEP_TIMEOUT_SECONDS", "DEFAULT_MARKETS", "ASIDE_ACCOUNT",
    "COLLECTION_BRIEF", "QUERY_SEEDS", "QUERY_SEEDS_APPROVED",
    "YTDLP_ALLOWED_FLAGS", "YTDLP_METADATA_ONLY",
    "KOREAN_WRITE_INTENT_TOKENS",
    "CollectionError", "BoundsError", "ReadOnlyViolation",
    "LiveCollectionDisabled", "LiveParseError", "UnapprovedQuerySeeds",
    "CollectionBounds", "CollectionResult",
    "assert_read_only", "build_plan", "aside_command", "normalize_record",
    "assert_query_seeds_approved",
    "run_preflight", "run_postflight", "collect", "write_observations", "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
