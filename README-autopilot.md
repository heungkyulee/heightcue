# heightcue 오토파일럿 — 셋업 가이드

> 파일 맵(어느 파일이 소싱/생성/발행/분석 담당인지)은 `AGENTS.md` 참조 — 새 세션은 그것부터 읽는다.

> 목표: 아래 일회성 셋업(약 1.5시간)만 끝내면, 이후는 스케줄 자동화가 소싱→생성→검사→발행→댓글→지표→주간 자가개선을 전부 수행합니다. 운영자의 몫은 주 10~15분(주간 리포트 + 보류함 확인)입니다.

## 0. 구성 요소

```
autopilot/
├── run.py          # 오케스트레이터 (daily / post / comments / weekly / dryrun)
├── common.py       # 설정·상태·스킬/스토리뱅크 파서
├── sourcing.py     # 상품 자동 소싱 (쿠팡 Open API + 수동 대기열 모드)
├── generate.py     # Gemini 호출 (A2/A3/V1/A5 스킬 체인)
├── post_check.py   # 바이럴 포맷 검사 + 리스크 메모
├── publish.py      # Threads API 발행·답글·인사이트·토큰 갱신
├── comments.py     # 댓글 분류·자동 답글·보류 (답글은 반드시 '댓글 id'에 단다 — 원글 id 금지)
├── analytics.py    # 지표 적재·주간 집계 (URL별 클릭 = 게시물별 클릭)
├── improve.py      # 주간 플레이북 자가개선 (철칙·가드레일 불가침)
├── validate.py     # 자격 정보 일괄 검증 (네이티브 실행)
├── test_queue.py   # 브라우저 큐 E2E 테스트 · test_posts.json 회귀 테스트셋
├── config.example.json
└── state/          # 발행 로그, 지표, 보류함, 플레이북, 주간 리포트, browser-queue/, us_products.json
```

검증: `../.venv/bin/python run.py dryrun` — API 키 없이 전체 사이클을 모의 실행합니다.

## 1. 일회성 셋업 체크리스트 (사람만 할 수 있는 것들)

**① KR/US Threads 계정** — ✅ 완료: 계정·장기 토큰·KR 바이오 설정 완료. 남은 것은 **US 바이오**(SSOT §2 문안)와 프로필 사진(본인 사진 권장 — 실화 컨셉의 신뢰 자산)뿐.

**② 쿠팡 파트너스** — ✅ 활성 상태 (2026-08-26 확인): `hklee@lifoli.co.kr` 기존 계정으로 로그인·대시보드·링크 생성 권한을 확인했습니다. 등록 채널에 Threads `@heightcue`가 포함되어 있어 아래 브라우저 큐로 KR 판매글 소싱을 진행할 수 있습니다.
※ 최종 승인(누적 매출 15만 원) 전에는 API 키가 없으므로 **브라우저 큐 모드**로 동작합니다: 오토파일럿이 `autopilot/state/browser-queue/requests.json`에 소싱 요청을 자동으로 쌓고, **Aside 반복 루틴**(`aside-sourcing-routine.md` 프롬프트로 등록, 30분~1시간 주기)이 로그인된 파트너스 브라우저로 공식 링크·리뷰 데이터를 수집해 `results.json`에 채우면 다음 실행이 소비합니다. 소싱 우선순위: 브라우저 큐 → 수동 대기열(manual_products.json) → 쿠팡 API(승인 후 키 입력 시 완전 자동 전환 — 인터페이스 동일).

```json
[
  {"product_key": "고유ID", "country": "KR", "category": "exercise",
   "product_name": "상품명", "is_food": false, "is_certified_health_food": false,
   "approved_claims": [], "price_info": "23,900원",
   "review_count": 4200, "review_quotes": ["실제 리뷰에서 복사한 문장"],
   "spec_facts": ["상세페이지에서 확인한 사실"],
   "link": "https://link.coupang.com/...", "sub_id": "hc-20260826-ex"}
]
```

**③ Meta 개발자 앱 + Threads 토큰** — ✅ 완료 (KR/US 장기 토큰 발급·검증 통과. 60일 만료 전 갱신은 weekly 실행이 자동 수행).

**④ LLM 키 (OpenRouter)** — ✅ 완료: `config.json`의 `openrouter.api_key`, 모델은 SSOT 원칙대로 Gemini 계열(`google/gemini-2.5-flash`).

**⑤ 스토리 뱅크 확정 (10분) — 발행 전 필수**
`story-bank.md`의 **E6(성장기 습관)은 AI가 넣은 자리표시자**입니다. 실제 습관으로 교체하거나 삭제해 주세요. V-0 고정핀 글과 판매글 예시 1의 훅("우유 도망")도 같은 확인 대상입니다. E1~E5는 말씀 주신 실화 그대로 정리되어 있으니 훑어보고 틀린 부분만 고치면 됩니다.

**⑥ 리허설 → 실전 전환 (원커맨드)**
`../.venv/bin/python run.py rehearsal` — validate 재검증 → 실제 LLM 생성·검사(발행 없음) → 미리보기 출력. 미리보기를 점검하고, 만족 시 `../.venv/bin/python run.py golive`(dry_run 해제 + publish 켜짐 + crontab 안내) → crontab 등록으로 가동. 그 외: `../.venv/bin/python run.py status`(상태 요약).

## 2. 실행 환경과 스케줄 (중요 — 확인된 사실)

**Claude 클라우드 세션에서는 외부 API(OpenRouter·Threads·쿠팡)가 네트워크 정책으로 차단되어 있음을 실측으로 확인했습니다** (2026-08-25, 3개 호스트 모두 프록시 403). 따라서 실행은 **운영자의 맥(또는 아무 소형 서버)** 에서 합니다. 코드는 python3 + requests만 있으면 됩니다.

**맥 셋업 (5분):**

```bash
# 1) zip을 풀어 원하는 위치에 둔다 (운영 폴더: ~/heightcue-autopilot — 배포 완료)
# 2) 격리된 실행 환경과 의존성
cd ~/heightcue-autopilot
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
# 3) config.json 을 autopilot/ 안에 넣는다 (전달받은 파일)
# 4) 검증 — 필수 항목이 전부 ✓ 인지 확인
cd autopilot && ../.venv/bin/python validate.py
# 5) 리허설: config.json 의 "dry_run": false 로 변경 → 실제 LLM 생성·검사가 돌지만
#    발행은 state/preview.jsonl 기록만 (publish 플래그가 없거나 false인 동안은 절대 실발행 없음)
# 6) 실전: 미리보기가 만족스러우면 config.json 의 mode 에 "publish": true 추가
```

**crontab 등록 (`crontab ~/heightcue-autopilot/crontab.txt`):**

```
30 9  * * * cd $HOME/heightcue-autopilot/autopilot && ../.venv/bin/python run.py daily >> state/cron.log 2>&1
30 12 * * * cd $HOME/heightcue-autopilot/autopilot && ../.venv/bin/python run.py post KR value >> state/cron.log 2>&1 && ../.venv/bin/python run.py post US value >> state/cron.log 2>&1
0  14 * * * cd $HOME/heightcue-autopilot/autopilot && ../.venv/bin/python run.py comments >> state/cron.log 2>&1
0  16 * * * cd $HOME/heightcue-autopilot/autopilot && ../.venv/bin/python run.py post KR sales >> state/cron.log 2>&1 && ../.venv/bin/python run.py post US value >> state/cron.log 2>&1
30 19 * * * cd $HOME/heightcue-autopilot/autopilot && ../.venv/bin/python run.py post KR value >> state/cron.log 2>&1 && ../.venv/bin/python run.py post US value >> state/cron.log 2>&1 && ../.venv/bin/python run.py comments >> state/cron.log 2>&1
0  21 * * 0 cd $HOME/heightcue-autopilot/autopilot && ../.venv/bin/python run.py weekly >> state/cron.log 2>&1
```

주의: 맥이 잠자기/꺼짐 상태면 해당 회차는 건너뜁니다(다음 회차에 이어짐). 완전 무인을 원하면 월 몇천 원짜리 VPS에 같은 crontab을 걸면 됩니다.

**Claude(Cowork)의 역할 분담:** 실행은 맥이, **전략·개선·코드 업데이트는 Claude 세션이** 맡습니다. 데스크톱 앱에서 `heightcue-autopilot` 폴더를 연결해 두면(완료), 제가 `state/weekly_report.md`·보류함·지표를 직접 읽고 플레이북 개선이나 코드 수정을 이어서 해드릴 수 있습니다.

## 3. 운영 정책 요약 (SSOT §8)

* 리스크 메모 0건 → 자동 발행 / 메모 있음 → 보류함(발행 안 됨, 주간 리포트에 목록)
* 댓글: 자동 답글, 의료 상담은 "소아과 상담" 고정 원칙, 분쟁·판단곤란은 보류
* 자가개선: 플레이북 경로로만. 철칙·가드레일·고지·스토리뱅크 사실·1:2 비율은 불가침
* 킬스위치: 스케줄 작업 OFF = 전체 정지

## 4. US 트랙 — 구현 상태

* **구현 완료 (둘 다 코드에 존재, 기본 ON):**
  * 가치·스토리 글: 매일 @heightcue_us에 링크 없는 영어 글 1건 (SKILL V1 country=US).
  * 판매글 — **사이트 가이드 경유**: `state/us_products.json` 레지스트리를 로테이션(재사용 간격 7일)해 사이트 가이드 페이지 링크로 판매글 발행. 아마존 태그 직링크는 쓰지 않는다(사이트가 Associates 등록 앵커). 소재가 없으면 자동 건너뜀 — 새 가이드 페이지를 사이트에 추가하고 레지스트리에 항목을 넣으면 소재가 늘어난다(Claude가 제작 가능).
* **남은 인간 단계:** 아마존 정산 수단 선택 (LAUNCH-STATUS 참조).

## 5. 영상(I2V UGC) 워크플로 — `run.py video`

텍스트 파이프라인과 **완전히 분리된** 별도 명령이다. `daily`/`post`/`comments`/`weekly`
는 영상 코드를 전혀 부르지 않는다 — 영상 기능이 고장나도 매일 돌아가는 텍스트 발행은
영향을 받지 않는다.

```bash
cd ~/heightcue-autopilot/autopilot
../.venv/bin/python run.py video rehearsal --market KR --fixture fixtures/viral_ugc_sample.jsonl
../.venv/bin/python run.py video status            # 원장 집계 + 게이트 상태 (--json 지원)
../.venv/bin/python run.py video enqueue --job-file <잡문서.json>
../.venv/bin/python run.py video process           # 유료 — 기본은 거부한다
```

### 5-1. ⚠️ 배포 전제조건 — 전사 **백엔드** (없으면 실행 금지)

QA 게이트(`video_qa.py`)는 **fail-closed** 다. 돌지 못한 검사는 통과가 아니라 **실패**로
집계된다. 따라서 전사가 돌지 못하면 `spoken_content` 검사가 돌지 못하고
**모든 실영상이 예외 없이 QA 실패한다.**

**필요한 것은 `transcriber.py` 파일이 아니라 전사 백엔드다.**
`tools/analysis/transcriber.py` 는 그 자체로 아무것도 하지 못한다 — 내부에서
`faster_whisper` 를 import 하고(대안으로 `whisperx`), 없으면
`"faster-whisper is not installed"` 로 즉시 실패한다. **파일 존재는 실행 가능을 뜻하지
않는다.** 파일만 확인하는 프로브는 백엔드가 하나도 없는 머신에서도 `[충족]` 을 찍어
유료 실행을 승인해버린다(2026-08-28 실제로 발생한 결함).

**어느 인터프리터인지도 중요하다.** `video_qa.default_transcriber` 는 OpenMontage 루트로
셸아웃한다. 그래서 프로브는 `video_qa._openmontage_call` 과 **같은 인터프리터·같은 cwd
·같은 sys.path** 로 백엔드를 실제 import 해 본다. 루트는 `OPENMONTAGE_ROOT` 로 지정하며
기본은 `~/OpenMontage`.

운영자가 실행할 설치 명령:

```bash
cd ~/OpenMontage && .venv/bin/python -m pip install faster-whisper
# 반드시 OpenMontage 자기 venv 다. QA 전사는 이 인터프리터로만 돈다
# (autopilot venv 에 설치해도 프로브·QA 어느 쪽도 보지 않는다).
export OPENMONTAGE_ROOT=/path/to/OpenMontage      # 다른 위치에 있다면
```

판정 규칙은 fail-closed 다: 백엔드가 하나도 import 되지 않으면 물론이고, 프로브가
비정상 종료하거나 타임아웃하거나 출력이 해석되지 않아도 **미충족**이다. 확인하지 못한
것을 충족으로 세지 않는다.

**남은 한계.** 프로브는 백엔드 import 까지만 확인하고 모델 가중치 내려받기나 실제 전사
품질은 검증하지 않는다. `[충족]` 은 "전사가 돌 수 있다"는 뜻이지 "QA 가 통과한다"는
뜻이 아니다.

이건 버그가 아니라 의도된 설계다(검사를 건너뛰고 발행하느니 막는 게 맞다). 다만 그 결과
**전사 백엔드 없이 유료 생성을 돌리면 영상은 전량 탈락하고 비용만 나간다.** 그래서
`run.py video rehearsal` 이 전제조건 충족 여부를 먼저 보고하고, 미충족이면 **exit 1** 로
끝난다. 유료 실행 중에 발견하게 두지 않는다.

### 5-2. 유료 생성은 기본 꺼짐 — 켜는 절차

`video process` 는 실제 돈을 쓴다(fal.ai MiniMax H3 Max, 5초 컷당 약 **$0.20**).
이 파이프라인은 **아직 단 한 번도 유료 호출을 한 적이 없다.** 게이트는 3중이다.

| config 키 | 기본값 | 역할 |
|---|---|---|
| `video.production_generation_enabled` | `false` | **비용을 여는 유일한 스위치.** 꺼져 있으면 `process` 가 잡을 claim 조차 하지 않는다 |
| `video.enabled` | `false` | 파이프라인 전체 on/off |
| `video.kill_switch` | `false` | `true` 면 `enqueue`·`process`(`--dry-run` 포함) 를 즉시 차단(진행 중인 잡은 건드리지 않는다) |

**세 플래그는 JSON 불리언만 받는다.** `"false"`·`"off"`·`"no"`·`1` 같은 문자열/숫자는
불리언이 아니므로 **안전한 쪽으로 떨어지고 경고를 찍는다** — `production_generation_enabled`
와 `enabled` 는 꺼짐으로, `kill_switch` 는 **걸림**으로 간다. 예전 `bool()` 강제는
fail-open 이어서 `"false"` 가 True 로 읽혔다(= 돈 게이트가 열렸다).

거부할 때 잡을 claim 하지 않는 것이 중요하다 — 리스를 잡았다 놓으면 `attempts` 가 축나고
반복 거부만으로 멀쩡한 잡이 `dead_letter` 로 굴러떨어진다.

순서: ① `rehearsal` 로 전제조건·게이트 확인(무료) → ② OpenMontage transcriber 확보 →
③ 라이브 종단 게이트 통과 → ④ 그때 비로소 두 플래그를 켠다(리터럴 `true`).

### 5-3. 현재 한계 — `process` 는 아직 끝까지 돌지 않는다

게이트를 모두 통과해도 `video process` 는 **exit 5 로 멈춘다.** 스토리보드→첫프레임→컷
→합성→QA→핸드오프를 한 프로세스로 잇는 종단 오케스트레이터가 아직 배선되지 않았기
때문이다. 지금은 모듈을 순서대로 직접 호출해야 한다. 조용히 성공을 반환해서 "돌았는데
아무 일도 안 일어났다"가 되지 않도록 명시적으로 실패시킨다.