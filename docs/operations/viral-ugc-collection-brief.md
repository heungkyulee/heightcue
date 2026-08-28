# 바이럴 UGC 수집 브리핑 (읽기 전용 · 바운드)

> 대상: `autopilot/collect_viral_ugc.py`
> 소비처: Task 6 패턴 원장 `autopilot/viral_ugc.py` (`PatternLedger`)
> 상태: **dry-run 경로만 검증 완료.** 라이브 수집은 별도 검증 태스크로 미뤘다.

## 1. 이 루틴이 하는 일

공개 게시물에서 **우리가 실제로 본 것**만 모아 `Observation` 레코드로 만든다.
그 레코드를 `viral_ugc.PatternLedger` 가 그대로 받아 KR/US 원장에 적재한다.

수집하는 것: 출처 URL, 관측 시각, 플랫폼, 시장, 상품 id, 카테고리,
그 시각 화면에 **보였던** 참여 지표.

수집하지 않는 것: 크리에이터의 이미지·영상(사본·썸네일·바이너리 전부),
우리 해석(훅/문법 판단 — 그건 `Inference` 쪽이며 별도 단계다),
그리고 **화면에 없던 숫자**.

## 2. 읽기 전용 보장

세 겹으로 강제한다.

1. **명령 게이트** — 모든 외부 명령은 `assert_read_only(argv)` 를 통과해야
   실행된다. `post`/`reply`/`like`/`follow`/`repost`/`share`/`dm`/`subscribe`/
   `delete` 등 쓰기 의도 토큰, `POST`/`PUT`/`PATCH`/`DELETE` HTTP 메서드,
   `download`/`-o`/`--output`/`--write-thumbnail` 등 다운로드 의도 토큰이
   보이면 **실행 전에** `ReadOnlyViolation` 으로 죽는다.
   `curl`/`wget` 직접 호출은 무조건 금지.
   `yt-dlp` 는 `--skip-download` / `--dump-json` / `--print` / `--list-subs` /
   `--write-auto-subs` 중 하나가 없으면 실행 자체가 거부된다.
2. **스키마 게이트** — `viral_ugc.FORBIDDEN_MEDIA_KEYS`(`media_path`,
   `video_bytes`, `thumbnail_path`, `local_copy` …) 가 레코드에 하나라도 있으면
   strict 모드에서 `MediaPolicyError` 로 실패하고, lenient 모드에서는 그 레코드를
   버린다. 저장 경로에는 미디어 키가 들어갈 구조가 없다.
3. **추정 금지** — 관측되지 않은 지표는 `None` 으로 남는다. 절대 0 으로
   채우거나 보간하지 않는다. `EngagementSnapshot.observed_metrics()` 는 실제로
   본 키만 돌려주고, 직렬화 결과에도 미관측 키는 **아예 없다**.

어떤 계정에도 게시·좋아요·팔로우·댓글·구독을 하지 않는다.

## 3. 바운드 (이전 300초 타임아웃 재발 방지)

이전 라이브 시도는 300초에서 죽었다. 이제 실행량은 시작 전에 확정된다.

| 상수 | 값 | 의미 |
|---|---|---|
| `MAX_QUERIES_PER_RUN` | 4 | 실행당 검색 쿼리 수 |
| `MAX_PAGES_PER_QUERY` | 2 | 쿼리당 스크롤 배치 수 |
| `MAX_POSTS_PER_QUERY` | 12 | 쿼리당 훑는 게시물 수 |
| `MAX_OBSERVATIONS_PER_RUN` | 24 | 실행당 채택 관측 수 |
| `WALL_CLOCK_BUDGET_SECONDS` | 180 | 실행 전체 예산 (300 미만) |
| `STEP_TIMEOUT_SECONDS` | 45 | 외부 명령 하나당 타임아웃 |

`CollectionBounds` 로 실행별 조정이 가능하지만 **위 천장을 넘을 수 없다** —
넘기면 `BoundsError`. 한도를 완화하는 방향은 막혀 있고 강화만 된다.
`build_plan()` 이 실행 **전에** 전체 단계 목록을 확정하므로 무한 탐색 경로가
존재하지 않는다. 한도에 닿으면 조용히 멈추고 사유를 `bounds_hit` 에 남긴다.

## 4. 명령

### dry-run (기본 · CI 경로 · 네트워크 0건)

```bash
cd ~/heightcue-autopilot/autopilot
../.venv/bin/python collect_viral_ugc.py --dry-run --fixture fixtures/viral_ugc_sample.jsonl
```

출력 저장 + JSON 요약:

```bash
../.venv/bin/python collect_viral_ugc.py --dry-run \
  --fixture fixtures/viral_ugc_sample.jsonl \
  --out state/viral_ugc/incoming.jsonl --json
```

### 테스트

```bash
cd ~/heightcue-autopilot/autopilot
../.venv/bin/python -m unittest -v test_collect_viral_ugc.py
```

### 라이브 게이트 (아직 실행하지 않음 — 별도 검증 태스크)

브라우저가 필요한 모든 단계는 **오직 Aside CLI** 로 나간다. Playwright·
Selenium·browser-use·내장 브라우저 툴은 이 레포에서 사용하지 않는다.

```bash
# 사전 점검
agent-reach doctor --json

# 한 개의 바운드된 Threads 쿼리 (읽기 전용)
aside --account u0 exec "Read-only: execute the HeightCue viral UGC collection brief in docs/operations/viral-ugc-collection-brief.md for one bounded Threads query and return structured observations only."

# 정밀 조작이 필요할 때만
aside --account u0 repl "<JavaScript>"

# 유튜브는 브라우저 대신 메타데이터 경로
yt-dlp --skip-download --dump-json "<video-url>"

# 사후 점검
agent-reach check-update
```

라이브 실행은 `--allow-live` 를 명시해야만 시작된다. `--dry-run` 도
`--allow-live` 도 없으면 CLI 는 exit 2 로 거부한다. 라이브가 기본값이 되는
경로는 존재하지 않는다.

## 5. Aside 에게 주는 지시 (루틴이 자동 생성하는 목표문)

```
Read-only observation task. Follow docs/operations/viral-ugc-collection-brief.md.
Market <KR|US>. Inspect at most 12 public results across at most 2 scroll
batches for the query "<query>". Return structured JSON observations containing
only: source_url, observed_at, visible engagement counters that are actually on
screen, product_id, category. Omit any counter that is not visible — never guess
or fill in zero. Do not save any image or video. Do not interact with any account.
```

카테고리는 SSOT §0 하드락(`nutrition`/`sleep`/`posture`/`exercise`)을 벗어날 수
없다 — 벗어나면 `Observation.validate()` 가 거부한다.

## 6. 전후 점검

- **preflight** (`--preflight`): `agent-reach doctor --json`
- **postflight** (`--postflight`): `agent-reach check-update`

둘 다 기본 꺼짐이며 `runner=` 시임으로 주입 가능하므로 테스트에서는 실제
명령이 절대 실행되지 않는다.

## 7. runner 시임

`collect(runner=...)` 는 `codex_image_bridge.py` 와 같은 주입 패턴이다.
프로덕션은 `_subprocess_runner`(읽기 전용 검사 후 `subprocess.run`), 테스트는
호출을 기록하거나 호출 시 즉시 실패하는 가짜 runner 를 넣는다. 그래서
"dry-run 경로에서 외부 명령 0건" 이 주장이 아니라 **테스트로 증명**된다.

## 8. 시장 격리

KR 과 US 관측은 파일부터 분리된 원장으로 들어간다
(`PatternLedger(base_dir, "KR")` / `"US"`). `--market KR` 로 수집 단계에서도
좁힐 수 있다. KR 관측이 US 원장에 들어가려 하면 `MarketIsolationError`.
