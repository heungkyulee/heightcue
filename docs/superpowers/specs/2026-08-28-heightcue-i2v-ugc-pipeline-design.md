---
meta:
  title: "HeightCue에서 실제 상품 자산으로 바이럴 UGC 영상을 어떻게 자동 생성할까?"
  navLabel: "I2V UGC Pipeline"
  category: "HeightCue"
  contentType: "Conceptual"
status: approved-design
approved_on: 2026-08-28
---

# HeightCue에서 실제 상품 자산으로 바이럴 UGC 영상을 어떻게 자동 생성할까?

이 문서는 HeightCue가 실제 판매 상품의 공식 사진과 영상을 기준으로 5–15초 세로형 사용자 제작 콘텐츠(User-Generated Content, UGC) 스타일 영상을 자동 생성하고, 검증된 결과를 콘텐츠 퍼블리싱 팀에 넘기는 구조를 정의한다. 기본 생성 경로는 `gpt-image-gen-2` 첫 프레임 편집과 fal.ai의 `minimax/h3-max/image-to-video`이며, 제품 일관성·음성·주장·권리 출처 검사를 모두 통과한 자산만 발행 단계로 이동한다.

## 문서 계획

- 목표: HeightCue의 기존 상품 소싱·콘텐츠 생성·퍼블리싱·성과 분석 흐름에 이미지 기반 영상 생성 파이프라인을 추가한다.
- 대상: HeightCue 운영 봇, 콘텐츠 퍼블리싱 팀, 구현·검증 담당자.
- 범위: 상품 자산 수집, 바이럴 레퍼런스 분석, 스토리보드, 첫 프레임, H3 Max 이미지-투-비디오(Image-to-Video, I2V), 자동 품질 검사, 퍼블리싱 인계, 성과 학습.
- 범위 밖: 텍스트-투-비디오(Text-to-Video, T2V), 가짜 사용자 후기, AI 부모 캐릭터, 다른 영상 모델로의 자동 폴백, 새 소셜 채널의 계정 개설.
- 승인된 결정: 손·제품 중심 시점(Point of View, POV), 공식 상품 페이지 자산 사용과 출처 기록, 완전 자동 발행, H3 Max 네이티브 보이스오버, 기본 10초·2컷.
- 열린 질문: 없음. 구현 중 발견되는 제공자 제약은 설계 변경 제안으로 올리고 임의 대체하지 않는다.

## 목표와 성공 기준

이 파이프라인의 목표는 예쁜 광고 영상을 만드는 것이 아니라, 실제 상품의 한 가지 효용을 모바일 피드에서 즉시 이해시키는 짧은 영상을 반복 생산하고 클릭·주문 데이터로 영상 문법을 개선하는 것이다.

성공 기준은 다음과 같다.

1. 모든 최종 영상이 정확한 상품·옵션의 공식 사진 또는 영상을 포함한다.
2. 생성 컷의 제품 외형·로고·색상·부속품이 원본과 일치한다.
3. H3 Max 네이티브 보이스오버의 전사문이 승인된 사실 범위를 벗어나지 않는다.
4. 한 컷은 한 행동과 한 효용만 전달한다.
5. 컴플라이언스·기술·권리 출처 검사 실패 자산은 자동 발행되지 않는다.
6. 게시물별 영상 패턴·상품·추적 링크·비용·성과를 연결할 수 있다.
7. 외부 바이럴 반응은 가설 생성에만 쓰고, HeightCue의 클릭·주문·수익으로 패턴을 승격한다.

## 확정 아키텍처

파이프라인은 실제 상품을 진실 기준으로 유지하면서 생성 모델은 생활 맥락과 움직임을 담당하는 `Product-Truth I2V` 구조를 사용한다.

```text
상품 소싱 완료
  → 공식 제품 사진·영상 수집과 출처 기록
  → 소싱 봇·콘텐츠 생성 봇의 근거 패킷 결합
  → Threads·YouTube Shorts 바이럴 패턴 매칭
  → 5–15초 마이크로 스토리보드 생성
  → `gpt-image-gen-2`로 컷별 첫 프레임 편집
  → fal.ai MiniMax H3 Max I2V와 네이티브 보이스오버 생성
  → 제품·음성·주장·기술 품질 자동 검사
  → 콘텐츠 퍼블리싱 팀 인계
  → 발행과 24시간·72시간·7일 성과 회수
  → UGC 패턴 상태 갱신
```

실제 상품 사진·영상은 `truth layer`다. `gpt-image-gen-2`와 H3 Max가 만드는 손, 배경, 조명, 카메라 동작은 `motion layer`다. 생성 모델이 실제 상품의 모양이나 기능을 새로 정의할 수 없다.

## 구성 요소와 책임

각 구성 요소는 하나의 책임과 명시적 입출력을 가진다.

### Product asset collector

공식 Coupang·Amazon 상품 페이지에서 정확한 옵션의 사진과 영상을 수집한다. 브라우저가 필요한 탐색·다운로드는 Aside `u0`만 사용한다.

수집 항목:

- 상품 ID, 옵션 ID, 모델명, 판매자, 브랜드
- 정면 또는 3/4 제품 사진
- 로고·패키지 문구가 보이는 사진
- 사용 상태 공식 이미지
- 가능한 경우 실제 작동 영상
- 원본 URL, 수집 시각, 파일 해시, 파일 유형
- 권리 삭제 키와 파생 자산 목록

비슷한 상품, 다른 색상, 다른 용량, 다른 판매자의 이미지를 섞지 않는다. 자산 사용 정책은 공식 상품 페이지 자산을 출처 기록 후 사용하는 방식이다. 신고·요청 또는 정책 위반 확인 시 해당 원본과 모든 파생 영상을 즉시 비공개·삭제하고 재사용 금지 목록에 넣는다.

### Product evidence packet builder

상품 소싱 봇과 콘텐츠 생성 봇의 산출물을 하나의 근거 패킷으로 정규화한다.

```json
{
  "product_truth": {
    "product_id": "kr_example_123",
    "exact_model": "verified_model_name",
    "visual_identity": ["verified_color", "verified_logo"],
    "verified_features": ["verified_feature"],
    "verified_limits": ["verified_limit"],
    "real_review_patterns": ["verified_review_pattern"],
    "prohibited_claims": ["unsupported_claim"]
  },
  "content_angle": {
    "human_friction": "one_visible_problem",
    "single_payoff": "one_visible_result",
    "hook_family": "problem_demo_payoff",
    "voiceover_facts": ["approved_fact"],
    "skip_if": "verified_purchase_limit"
  }
}
```

스토리보드와 보이스오버는 이 패킷에 없는 기능·효능·수치·체험을 추가할 수 없다.

### Viral UGC intelligence

이 구성 요소는 외부 성공 사례에서 문장이나 영상을 복사하지 않고 재사용 가능한 영상 문법을 추출한다.

Threads 탐색은 Aside `u0`로 실제 피드·검색 화면을 읽는다. YouTube Shorts의 검색·메타데이터·자막 수집은 agent-reach와 `yt-dlp`를 사용하며, 첫 프레임·컷 전환·제품 노출 시점의 시각 검증은 Aside `u0`로 수행한다. 한국과 미국 패턴은 별도 원장에 저장한다.

외부 레퍼런스에서 수집하는 값:

- 게시물 URL과 계정
- 업로드 시각과 보이는 반응 수
- 영상 길이와 컷 수
- 첫 0–2초의 시각 훅
- 제품 노출 시점
- 손·얼굴·제품의 비중
- 카메라·조명·자막·보이스오버 구조
- 문제·동작·결과의 순서
- 고지와 행동 유도 문구
- 관찰 사실과 분석자의 추론 구분

공개 반응은 매출 증거가 아니다. 공유·리포스트·구매 질문 댓글은 패턴 후보의 우선순위를 정할 뿐이다.

### Storyboard planner

스토리보드 플래너는 근거 패킷과 UGC 패턴 카드를 결합해 1–3컷 계획을 만든다.

길이 정책:

| 분류 | 조건 | 출력 |
|---|---|---|
| 단순 | 한 동작으로 효용이 보임 | 5초·1컷 |
| 기본 | 문제와 해결을 분리해야 함 | 10초·2컷 |
| 복잡 | 준비·사용·결과가 각각 필요함 | 15초·3컷 |

기본값은 10초·2컷이다. 한 컷에는 하나의 행동과 하나의 효용만 배치한다. 제품명, 가격, 후기, 기능, 행동 유도 문구, 고지를 한 이미지에 넣지 않는다. 생성 이미지 속 텍스트는 사용하지 않고 정확한 텍스트는 퍼블리싱 단계에서 처리한다.

권장 영상 문법:

- 정체가 궁금한 물건
- 늦게 발견한 생활 해킹
- 작동 자체가 만족스러운 도구
- 생활의 혼란에서 통제로 전환
- 기대를 낮춘 뒤 실제 작동으로 반전

### First-frame generator

`gpt-image-gen-2`는 실제 제품 사진을 이미지 편집 입력으로 사용해 컷의 첫 순간을 만든다. 텍스트만으로 제품을 다시 그리지 않는다.

프롬프트는 다음만 정의한다.

- 한 가지 손동작
- 한 가지 생활 공간
- 카메라 거리와 각도
- 자연스러운 휴대전화 촬영 느낌
- 조명과 감정 톤

제품 영역은 가능한 한 원본 컷아웃을 보존한다. 손, 배경, 광원, 주변 소품만 생성한다. 첫 프레임은 처음부터 9:16으로 만든다.

### H3 Max I2V generator

영상 생성기는 fal.ai의 `minimax/h3-max/image-to-video`만 사용한다.

고정 값:

- operation: `image_to_video`
- resolution: `768P`
- duration: 컷당 5초
- aspect ratio: 첫 프레임의 9:16 비율을 따름
- audio: H3 Max 네이티브 보이스오버 사용
- generation type: 첫 프레임 기반 I2V

동작 프롬프트는 제품의 외관보다 손·카메라·주변 환경의 움직임을 기술한다. 과격한 회전, 제품 분해, 장시간 가림, 화면 밖으로 나갔다 다시 등장하는 동작은 금지한다.

현재 OpenMontage의 `tools/video/minimax_fal_video.py`는 T2V만 H3 Max로 보내고 I2V는 구형 `fal-ai/minimax/hailuo-03/image-to-video`로 보낸다. 구현의 첫 기술 변경은 I2V 경로를 `minimax/h3-max/image-to-video`로 교체하고 요청 스키마·가격 추정·계약 테스트를 갱신하는 것이다. 다른 모델이나 T2V로 자동 대체하지 않는다.

### Quality gate

품질 게이트는 창의적 톤을 평준화하지 않고 사실·제품·음성·기술 오류만 차단한다.

1. 원본 자산: 정확한 상품 ID·옵션·공식 URL·사진·해시가 있어야 한다.
2. 스토리보드: 모든 주장과 동작이 근거 패킷에 있어야 한다.
3. 첫 프레임: 색상·실루엣·로고·버튼·부속품·패키지 문구가 원본과 일치해야 한다.
4. 영상 프레임: 영상 전체에서 제품이 같은 물건으로 유지돼야 한다.
5. 보이스오버: 자동 전사문이 승인 대본의 사실 범위를 벗어나지 않아야 한다.
6. 기술 품질: 9:16, 768P, 길이, 오디오, 프레임, 인코딩 검사를 통과해야 한다.
7. 퍼블리싱: 고지·추적 링크·상품 옵션·본문 일관성을 확인해야 한다.

H3 보이스오버는 자연스러운 어순 변경을 허용한다. 숫자·가격·함량·리뷰 수·효능·체험·비교 우위를 추가하거나 바꾸면 실패다. 여러 컷을 연결할 때는 화자 음색의 일관성도 검사한다.

### Publishing handoff

검사를 통과한 영상은 파일만 넘기지 않고 다음 패킷으로 콘텐츠 퍼블리싱 팀에 이관한다.

```json
{
  "video_path": "projects/heightcue_example/renders/final.mp4",
  "product_id": "kr_example_123",
  "source_asset_urls": ["https://official_product_page.example/item"],
  "storyboard_id": "storyboard_example_123",
  "ugc_pattern_id": "problem_demo_payoff_v1",
  "verified_voiceover_transcript": "approved_transcript",
  "required_disclosure": "market_specific_disclosure",
  "caption_brief": "one_claim_caption_brief",
  "cta": "verified_cta",
  "tracking_id": "post_specific_tracking_id",
  "rights_takedown_key": "rights_example_123",
  "qa_report": {
    "verdict": "PASS"
  }
}
```

퍼블리싱 팀은 패킷 밖의 사실을 추측하지 않는다. 한국 게시물은 쿠팡 표준 고지를 사용하고, 미국 게시물은 첫 부분의 `#ad`와 링크 옆 `(paid link)`를 유지한다. 실제 발행 후 Threads media ID를 읽어 원장에 연결한다.

## 바이럴 패턴의 상태와 학습

외부 패턴은 `candidate`, `active`, `fatigued`, `retired` 상태를 가진다. 외부 반응만으로 `active`가 되지 않는다.

패턴 승격에는 다음 내부 증거가 필요하다.

- 서로 다른 상품 3개 이상
- 카테고리 2개 이상
- 비교 가능한 다른 UGC 패턴 또는 텍스트 게시물 기준선
- 24시간·72시간·7일 성과
- 2초 유지율, 완주율, 공유, 프로필 이동, 링크 클릭, 주문, EPC, RPM, 환불 반영 수익

한 게시물의 바이럴은 전략 변경 근거가 아니다. 패턴 성과가 하락하면 `fatigued`, 재사용 가치가 없으면 `retired`로 바꾼다. 카피·고지·규정 준수 규칙은 성과 학습이 자동 완화할 수 없다.

## 실패 처리와 비용 통제

실패는 발행 슬롯을 비우는 결과를 허용한다. 품질이 낮은 자산을 억지로 발행하지 않는다.

| 실패 | 처리 |
|---|---|
| 첫 프레임 제품 불일치 | 최대 2회 재생성 후 보류 |
| 영상 중 제품 왜곡 | 모션 강도를 낮춰 1회 재생성 후 보류 |
| 보이스오버 사실 불일치 | 대본 축약 후 1회 재생성 후 보류 |
| H3 Max API 일시 오류 | 지수 백오프 후 다음 슬롯으로 이월 |
| 고지·추적 링크·상품 옵션 실패 | 즉시 발행 차단 |
| 권리 삭제 요청 | 원본과 모든 파생물 비공개·삭제·재사용 금지 |

비용 통제 규칙:

- 상품당 첫 프레임 후보는 최대 3장이다.
- 컷당 H3 Max 생성은 최대 2회다.
- 10초 기본형은 5초 클립 2개다.
- 일일 영상 생성 예산을 설정한다.
- 같은 상품이 연속 실패하면 7일 동안 재시도하지 않는다.
- 생성 전에 예상 비용을 예약하고 성공·실패 비용을 상품·패턴 ID에 귀속한다.

## OpenMontage 통합

영상 제작은 OpenMontage 파이프라인과 체크포인트를 통과한다. HeightCue 전용 파이프라인 정의는 조사, 제안, 스토리보드, 자산, 생성, 검수, 인계 단계를 명시한다.

예상 통합 경계:

- HeightCue는 상품·근거·패턴·퍼블리싱 패킷을 소유한다.
- OpenMontage는 프로젝트 작업공간, 첫 프레임·영상 자산, 비용, 체크포인트, 검수 결과를 소유한다.
- 퍼블리싱 팀은 검사를 통과한 인계 패킷만 소비한다.
- 생성 자산은 OpenMontage의 `projects/heightcue_<run_id>/` 아래에 저장한다.
- 모든 유료 생성 전에 도구·제공자·모델·예상 비용을 결정 원장에 기록한다.

최종 영상의 컷 결합·정확한 자막·고지 오버레이가 필요하면 Remotion을 기본 합성 런타임으로 사용한다. Remotion은 다중 영상과 음성 타이밍을 프레임 단위로 다루기 적합하다. HyperFrames는 훅 카드·행동 유도 문구 변형을 빠르게 만드는 보조 런타임으로 유지한다. 두 런타임의 사용 가능 여부는 OpenMontage preflight에서 매번 확인한다.

## 테스트 전략

테스트는 제공자 계약, 단위 검증, 오프라인 조립, 제한된 실 API 생성, 발행 전 종단 간 흐름을 분리한다.

### 단위 테스트

- 정확한 상품 옵션과 자산이 연결되는지 검사한다.
- 근거 패킷 밖의 주장과 수치를 거부한다.
- 한 컷에 여러 효용이 들어가면 거부한다.
- 길이 분류가 5초·10초·15초 정책을 따르는지 검사한다.
- 권리 삭제 키가 모든 파생 자산을 찾는지 검사한다.

### 제공자 계약 테스트

- `image_to_video`가 `minimax/h3-max/image-to-video`로 요청되는지 검사한다.
- `image_url`, `duration`, `resolution`, 안전 검사, 프롬프트 확장 값이 정확한지 검사한다.
- 768P I2V 가격 추정이 fal.ai 공개 계약과 일치하는지 검사한다.
- H3 Max 실패 시 Hailuo 03 또는 T2V로 폴백하지 않는지 검사한다.

### 미디어 검사 테스트

- 원본과 다른 색상·로고·부속품 샘플을 실패시킨다.
- 중간 프레임에서 제품이 변형되는 샘플을 실패시킨다.
- 승인 대본에 없는 효능을 말하는 음성을 실패시킨다.
- 잘린 음성, 무음, 검은 프레임, 잘못된 화면비를 실패시킨다.

### 종단 간 테스트

1. 검증된 저위험 일반 용품 1개를 선택한다.
2. 공식 자산과 근거 패킷을 만든다.
3. 10초·2컷 스토리보드와 첫 프레임을 생성한다.
4. H3 Max I2V를 컷별로 생성한다.
5. 모든 QA 결과를 저장한다.
6. 퍼블리싱 인계 패킷을 생성한다.
7. dry-run에서는 실제 게시하지 않고 소비 가능 여부만 검사한다.
8. production 검증에서는 실제 게시 후 media ID·영상 재생·본문·고지·추적 링크를 읽어 확인한다.

## 관찰 근거와 기술 출처

2026-08-28에 Aside `u0`로 Threads의 `amazon finds` 검색 결과를 읽었다. 공개 화면에서 다음과 같은 반응을 관찰했으며, 매출이나 실제 제휴 전환은 확인하지 않았다.

- [Jake의 램프 영상](https://www.threads.com/@jake_finds_/post/DVvk24gjtJE): 좋아요 5.3천, 답글 1.3천, 리포스트 274, 공유 1.0만이 화면에 표시됐다. 정체가 궁금한 물건을 먼저 보여주는 구조다.
- [ByHannah05의 생활 해킹 영상](https://www.threads.com/@byhannah05/post/DX1r-QhiCLb): 좋아요 6.7천, 답글 83, 리포스트 503, 공유 1.5천이 화면에 표시됐다. 늦게 발견한 해킹이라는 훅을 사용한다.
- [My Cute Gadgets의 양파 도구 영상](https://www.threads.com/@my_cute_gadgets/post/DVwpuE2Dkk2): 좋아요 1.3천, 답글 59, 리포스트 18, 공유 739가 화면에 표시됐다. 손동작과 즉시 보이는 결과가 중심이다.
- [Deal Hunter AZ의 바닥 복원 영상](https://www.threads.com/@dealhunteraz/post/DV1pqguidBT): 좋아요 980, 답글 11, 리포스트 31, 공유 798이 화면에 표시됐다. 손상 상태와 한 번의 동작으로 보이는 결과를 대비한다.

MiniMax H3 Max의 현재 fal.ai 문서는 [H3 Max I2V API](https://fal.ai/models/minimax/h3-max/image-to-video/api)에서 5–15초, 768P, 첫 프레임 `image_url`, 네이티브 영상 출력을 설명한다. 표준 H3의 다중 이미지·영상·오디오 참조 기능은 별도 [H3 reference-to-video API](https://fal.ai/models/minimax/h3/reference-to-video/api)이며, 이 설계의 기본 H3 Max I2V 경로와 혼용하지 않는다.

## 구현 승인 게이트

이 문서는 파이프라인의 승인된 설계다. 구현은 별도 구현 계획이 승인된 뒤 시작한다. 구현 계획은 OpenMontage 어댑터 수정, HeightCue 데이터 계약, 바이럴 수집기, 스토리보드, `gpt-image-gen-2` 첫 프레임, H3 Max 생성, QA, 퍼블리싱 인계, 종단 간 검증을 독립 작업으로 나눠야 한다.
