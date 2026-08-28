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

**실제 쓰기를 막는 층은 명령 게이트 하나다.** 스키마 게이트와 추정 금지는
수집된 레코드를 검사할 뿐 플랫폼 쓰기를 막지 못한다 — 아래 1번을 "세 겹 중
하나"로 오해하지 말 것.

1. **명령 게이트** — 모든 외부 명령은 `assert_read_only(argv)` 를 통과해야
   실행된다.
   - 영어 쓰기 의도 토큰: `post`/`reply`/`like`/`follow`/`repost`/`share`/
     `dm`/`subscribe`/`delete` 등 (공백 단어 경계 매칭).
   - **한국어 쓰기 의도 토큰**: `좋아요`/`팔로우`/`게시`/`댓글`/`답글`/`공유`/
     `구독`/`업로드`/`삭제`/`차단`/`신고`/`누르`/`저장`/`다운로드` 등
     (한국어는 공백 단어 경계가 없으므로 **부분 문자열**로 매칭). `QUERY_SEEDS`
     가 한국어이므로 영어 목록만으로는 구멍이 생긴다.
   - `POST`/`PUT`/`PATCH`/`DELETE` HTTP 메서드, `download`/`-o`/`--output` 등
     다운로드 의도 토큰.
   - `curl`/`wget` 직접 호출은 무조건 금지.
   - `yt-dlp` 는 **허용목록**(`YTDLP_ALLOWED_FLAGS`)에 있는 플래그만 쓸 수
     있다. 거부목록이 아니라 허용목록인 이유: `--write-thumbnails`(복수형),
     `-P`/`--paths`, 그리고 앞으로 생길 새 플래그를 거부목록은 구조적으로
     놓친다. `--write-*` 는 전부 금지이며 `--write-auto-subs` 는 `.vtt` 파일을
     디스크에 쓰므로 메타데이터 전용 목록에서 **제거**됐다. 메타데이터 전용
     플래그(`--skip-download`/`--dump-json`/`--dump-single-json`/`--simulate`/
     `--print`/`--list-subs`) 가 최소 하나 없으면 실행 자체가 거부된다.

   **한계 (정직하게)**: 이 가드는 `argv` 문자열만 검사한다. `aside ... exec`
   가 일단 실행되면 LLM 주도 에이전트가 브라우저에서 하는 행동을 이 가드가
   제어할 수 없다. 그래서 목표문은 **전적으로 기계 생성**이며
   (`aside_command()`), 쿼리는 `QUERY_SEEDS` 표에 있는 값만 허용하고,
   `collect()` 는 호출자가 자유형 목표문을 넘길 인자를 아예 갖고 있지 않다.
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
| `WALL_CLOCK_BUDGET_SECONDS` | 180 | 실행 전체 **실효** 예산 (300 미만) |
| `STEP_TIMEOUT_SECONDS` | 45 | 외부 명령 하나당 타임아웃 |

`CollectionBounds` 로 실행별 조정이 가능하지만 **위 천장을 넘을 수 없다** —
넘기면 `BoundsError`. 한도를 완화하는 방향은 막혀 있고 강화만 된다.
`build_plan()` 이 실행 **전에** 전체 단계 목록을 확정하므로 무한 탐색 경로가
존재하지 않는다. 한도에 닿으면 조용히 멈추고 사유를 `bounds_hit` 에 남긴다.

`WALL_CLOCK_BUDGET_SECONDS` 는 **진짜 천장이다.** 단계 사이에서만 검사하면
진행 중인 단계가 `STEP_TIMEOUT_SECONDS` 만큼 더 달릴 수 있어 최악의 경우가
예산을 넘는다(4단계 x 45 + preflight 45 + postflight 45 = 270초). 그래서
`CollectionBounds.timeout_for(elapsed)` 가 **남은 예산에 맞춰 각 단계의
타임아웃을 줄이고**, 남은 예산이 0이면 그 명령을 아예 시작하지 않는다. 따라서
외부 명령 시간의 합은 예산을 넘을 수 없다.

### 3.1 ⚠️ `QUERY_SEEDS` 는 운영자 미승인 플레이스홀더다

`collect_viral_ugc.QUERY_SEEDS` 의 검색어(`아이 키 성장 수면 루틴` 등)는
구현자가 임시로 적은 값이며 **운영자가 검토·승인한 검색어가 아니다.** SSOT §0
카테고리 하드락 안에는 있지만 그것만으로 승인된 것은 아니다.

라이브 실행 전에 운영자가 검색어를 직접 확인하고
`collect_viral_ugc.QUERY_SEEDS_APPROVED = True` 로 바꿔 커밋해야 한다. 그
전까지 `collect(dry_run=False, ...)` 는 `UnapprovedQuerySeeds` 로 거부된다.

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

`--out` 은 **덧붙이기(append)** 다. `incoming.jsonl` 은 누적 인박스이며 두 번
실행해도 앞선 실행의 관측이 지워지지 않는다. 배치는 전부 메모리에서 검증·
직렬화한 뒤 한 번의 `O_APPEND` 쓰기 + `fsync` 로 나가므로 반쪽 배치가 남지
않는다.

### 테스트

```bash
cd ~/heightcue-autopilot/autopilot
../.venv/bin/python -m unittest -v test_collect_viral_ugc.py
```

### 라이브 게이트 (아직 실행하지 않음 — 별도 검증 태스크)

브라우저가 필요한 모든 단계는 **오직 Aside CLI** 로 나간다. Playwright·
Selenium·browser-use·내장 브라우저 툴은 이 레포에서 사용하지 않는다.

**`aside repl` 로 임의 JavaScript 를 실행하지 말 것.** 임의 JS 는
`assert_read_only` 가드를 완전히 우회하며 좋아요·팔로우·게시를 그대로 수행할
수 있다. 이 브리핑은 그 경로를 운영 절차로 제공하지 않는다. 브라우저 단계는
`collect_viral_ugc` 가 기계 생성한 `aside ... exec` 목표문만 쓴다.

```bash
# 사전 점검
agent-reach doctor --json

# 한 개의 바운드된 Threads 쿼리 (읽기 전용)
aside --account u0 exec "Read-only: execute the HeightCue viral UGC collection brief in docs/operations/viral-ugc-collection-brief.md for one bounded Threads query and return structured observations only."

# 유튜브는 브라우저 대신 메타데이터 경로
yt-dlp --skip-download --dump-json "<video-url>"

# 사후 점검
agent-reach check-update
```

라이브 실행은 `--allow-live` 를 명시해야만 시작된다. `--dry-run` 도
`--allow-live` 도 없으면 CLI 는 exit 2 로 거부한다. 라이브가 기본값이 되는
경로는 존재하지 않는다.

## 5. Aside 에게 주는 지시 (루틴이 자동 생성하는 목표문)

이 목표문은 **전적으로 기계 생성**이다. 사람이나 호출자가 자유형 목표문을
끼워 넣는 경로는 없고, `<query>` 는 `QUERY_SEEDS` 표에 실제로 있는 값만
허용된다. 생성된 argv 는 반환 직전 자기 자신을 `assert_read_only` 에
통과시킨다.

```
Read-only observation task. Obey the brief at docs/operations/viral-ugc-collection-brief.md.
Market <KR|US>. Inspect at most 12 public results across at most 2 scroll
batches for the query "<query>". Return structured JSON observations containing
only: source_url, observed_at, visible engagement counters that are actually on
screen, product_id, category. Omit any counter that is not visible — never guess
or fill in zero. Do not save any image or video. Do not interact with any account.
```

카테고리는 SSOT §0 하드락(`nutrition`/`sleep`/`posture`/`exercise`)을 벗어날 수
없다 — 벗어나면 `Observation.validate()` 가 거부한다.

## 5.1 라이브 출력 파싱 — 빈 결과는 증명돼야 한다

`_parse_live_stdout()` 은 stdout 이 **비었을 때만** `[]` 를 돌려준다. 비어
있지 않은데 아는 모양(배열 / `{"observations": [...]}` / JSONL) 으로 읽히지
않으면 `LiveParseError` 를 던진다(lenient 모드에서는 원문 stdout 과 함께
`rejected` 에 `reason: live_parse_failed` 로 기록). 조용한 `[]` 는 "바이럴
게시물이 없었다" 와 "파서가 Aside 출력 계약을 모른다" 를 구별할 수 없게 만들고,
브리핑의 검증 기준("출처 연결된 관측 1건 이상 **또는** 근거 있는 명시적 빈
결과")을 어느 쪽으로도 만족시키지 못한다.

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
