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

#: 이 토큰이 있으면 yt-dlp 는 메타데이터 전용으로 간주한다.
YTDLP_METADATA_ONLY = ("--skip-download", "--dump-json", "--print",
                       "--list-subs", "--write-auto-subs")

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
        for tok in ("-o", "--output", "--write-thumbnail", "--write-video"):
            if tok in parts:
                raise ReadOnlyViolation(
                    f"yt-dlp 출력 플래그 {tok} 감지 — 미디어 저장 금지: {parts!r}")
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

#: 시장별 좁은 쿼리 (SSOT §0 카테고리 하드락 안에서만).
QUERY_SEEDS: Dict[str, Tuple[str, ...]] = {
    "KR": ("아이 키 성장 수면 루틴", "아이 자세 교정 후기"),
    "US": ("kids growth nutrition routine", "children posture habit"),
}


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
    """이 단계에 해당하는 Aside CLI argv (브라우저 표준 — 유일한 경로)."""
    goal = (
        f"Read-only observation task. Follow {step['brief']}. "
        f"Market {step['market']}. Inspect at most {step['max_posts']} public "
        f"results across at most {step['pages']} scroll batches for the query "
        f"\"{step['query']}\". Return structured JSON observations containing "
        f"only: source_url, observed_at, visible engagement counters that are "
        f"actually on screen, product_id, category. Omit any counter that is "
        f"not visible — never guess or fill in zero. Do not save any image or "
        f"video. Do not interact with any account."
    )
    return ["aside", "--account", ASIDE_ACCOUNT, "exec", goal]


# ---------------------------------------------------------------------------
# 레코드 정규화
# ---------------------------------------------------------------------------


def _scrub_media(row: Dict[str, Any], strict: bool) -> Dict[str, Any]:
    """미디어 사본 키 처리 — strict 면 거부, 아니면 드롭 후 사유를 남긴다."""
    present = [k for k in FORBIDDEN_MEDIA_KEYS if k in row]
    if not present:
        return row
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
    with open(path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line or line.startswith("//"):
                continue
            data = json.loads(line)
            if data.get("_fixture_note") and "observation_id" not in data:
                continue  # 픽스처 헤더 주석
            yield lineno, data


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

    started = clock()
    result = CollectionResult(
        source="fixture" if dry_run else "live",
        bounds=bounds.to_dict(),
        plan=build_plan(markets, bounds),
        started_at=datetime.now(timezone.utc).astimezone().isoformat(),
    )

    if preflight:
        result.preflight = run_preflight(runner, bounds.step_timeout_seconds)

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
            if clock() - started >= bounds.wall_clock_budget_seconds:
                result.bounds_hit.append("wall_clock_budget_seconds")
                break
            if len(result.observations) >= bounds.max_observations_per_run:
                result.bounds_hit.append("max_observations_per_run")
                break
            call = _run(runner, aside_command(step),
                        bounds.step_timeout_seconds)
            for row in _parse_live_stdout(call.get("stdout", "")):
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
        result.postflight = run_postflight(runner, bounds.step_timeout_seconds)

    result.elapsed_seconds = max(0.0, clock() - started)
    result.bounds_hit = sorted(set(result.bounds_hit))
    return result


def _parse_live_stdout(stdout: str) -> List[Dict[str, Any]]:
    """Aside stdout 에서 관측 레코드를 뽑는다. 파싱 실패는 빈 리스트."""
    text = (stdout or "").strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except ValueError:
        rows = []
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
        return rows
    if isinstance(data, dict):
        data = data.get("observations") or []
    return [r for r in data if isinstance(r, dict)]


# ---------------------------------------------------------------------------
# 저장
# ---------------------------------------------------------------------------


def write_observations(path: str, observations: Sequence[Observation]) -> str:
    """검증된 관측을 JSONL 로 원자적으로 쓴다 (미디어 키는 구조상 없다)."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            for obs in observations:
                obs.validate()
                fh.write(json.dumps(obs.to_dict(), ensure_ascii=False) + "\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
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
    "COLLECTION_BRIEF", "QUERY_SEEDS",
    "CollectionError", "BoundsError", "ReadOnlyViolation",
    "LiveCollectionDisabled",
    "CollectionBounds", "CollectionResult",
    "assert_read_only", "build_plan", "aside_command", "normalize_record",
    "run_preflight", "run_postflight", "collect", "write_observations", "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
