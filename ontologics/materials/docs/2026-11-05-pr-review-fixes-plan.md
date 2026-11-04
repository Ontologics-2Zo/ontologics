# A/B PR 검토 보완 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 공용 재고 입고 중복·관측 시간 선택·계산 저장 재시도·별도 요청 입력창 iframe 보호를 보완하고, 최신 main의 PR #15를 보존한다.

**Architecture:** 기존 공용 품목 매핑과 재고 계산기, 공통 요청 키 재생 처리를 재사용한다. A의 재고 어댑터와 화면, B의 독립 요청 입력 서버만 직접 수정한다. 최신 main 병합과 신규 버그 수정을 구분한다.

**Tech Stack:** Python 3.10+, PostgreSQL/psycopg 3, 브라우저 JavaScript, unittest, Node.js 기반 실제 화면 핸들러 검사.

**Spec:** 사용자가 제공한 PR 리뷰의 네 항목 및 최신 main 보존 요구. 계획 작성 후 사용자가 구현·테스트를 요청해 아래 내용을 수행했다. 최신 main 병합과 로컬 회귀 검증을 마쳤으며, 후속 커밋·푸시 요청에 따라 구현·테스트·문서를 기존 PR #16의 `integration/a-b` 브랜치에 함께 공유한다.

## 확인 기준과 제약

- 확인 시점: 2026-11-05, 현재 `integration/a-b`의 HEAD `76b23f5`, 조회한 `origin/main`은 PR #15 병합 커밋 `f5ebaad`.
- 현재 함수의 읽기 전용 재현에서 105개가 110개로 계산되는 사례, 최신 관측 100개 대신 오래된 200개가 선택되는 사례, 같은 계산 재시도 시 요청 키가 바뀌는 사례를 확인했다. 전체 테스트를 다시 실행한 결과는 아니다.
- 공개 스키마·T-box·DB 테이블·승인/신원 규칙을 새로 변경하지 않는다. 공통 계산기를 복제하거나 자료의 미확인 값을 0으로 바꾸지 않는다.
- 운영 DB·계약 원문·재고 기록·Neo4j를 계획 또는 회귀 검사 목적으로 수정하지 않는다. 데이터 검증은 격리된 시험 저장소에서 한다.
- 로컬은 영향 범위의 회귀 검사, 최신 main과 결합한 최종 전체 검사는 기존 PR CI에서 수행한다. 리뷰 지시대로 같은 전체 검사를 양쪽에서 반복하지 않는다.

## 화면과 파일 지도

아래 경로는 저장소 루트 기준이다.

| 검토 항목 | 사용자가 영향을 받는 화면 | 직접 수정할 구현 파일 |
| --- | --- | --- |
| 1. 공용 품목 입고 대사 | 생산·자재 계획의 계약 검토 / 추가 생산 요청 / 제품별 자재에서 가용량·부족량·RFQ 수량. 재고 등록 경로에서 중복 반영 차단 | `ontologics/materials/library_stock.py`의 `LibraryStock.store()` |
| 2. 최신 재고 선택 | 같은 세 화면의 재고 값, 기존 예약 경로의 가용량 | 같은 파일의 `shared_stock()`와 `LibraryStock.latest()` |
| 3. 계산 저장 재시도 | 계약/추가 요청의 ‘계산하고 결과 확인’, 제품별 자재의 ‘이 제품 검토하고 결과 보기’ | `ontologics/static/planning.js`의 `reviewSave` 및 관련 버튼 핸들러 |
| 4. 독립 요청 입력창 보호 | 기본 포트 8011의 추가 생산 요청 입력창. 계약·납품 메뉴의 B 화면과 별개 | `ontologics/order_fulfillment/request_entry.py`의 `Handler` |

추가·확장할 검사 파일:

- `tests/materials/test_shared_inventory.py`: 입고 대사와 실제 관측 시간 선택.
- `tests/materials/test_planning_retry_ui.py` **신규**: 실제 `planning.js` 핸들러를 Node VM과 최소 DOM으로 실행.
- `tests/materials/test_workspace_http.py`: 같은 키의 계산 재전송 시 검토/RFQ 중복 저장 없음.
- `tests/materials/test_request_workflow.py`: 요청 검토의 동일 키 재생과 새 키의 최신 재고 재계산.
- `ontologics/order_fulfillment/test_request_entry.py`: 독립 HTTP 서버의 정상·오류 응답과 iframe 헤더.

문서 충돌 해결: `PROJECT_STATUS.md`, `docs/TOOLS.md`. A의 기존 화면 계획 문서 `ontologics/materials/docs/2026-11-04-unified-workspace-plan.md`에는 구현 후 재시도 보완 근거만 추가한다. 과거 검증 결과를 이번 검증처럼 덮어쓰지 않는다.

## Task 1 — 최신 main 통합 기준 확보

- [x] 구현 착수 시 원격 main과 작업 트리를 다시 확인한다. 기존 미커밋 변경이 있으면 보존한다.
- [x] main을 현재 통합 브랜치에 일반 병합한다. 현재 사전 병합 검사에서 충돌한 파일은 `PROJECT_STATUS.md`, `docs/TOOLS.md` 두 개다. main이 추가로 바뀌면 다시 확인한다.
- [x] `PROJECT_STATUS.md`: 기존 A/B 병합 기록과 main의 ‘PR #15 C 공통 화면 검수 보완’ 기록을 모두 보존한다.
- [x] `docs/TOOLS.md`: C 조회·파일 기반 초안 계약을 보존하고 A workspace 참조는 `materials/workspace.py`로 유지한다.
- [x] 자동 병합되는 `ontologics/web.py`에서 A의 `MaterialsWorkspace`와 main의 `/api/claims/query` 처리가 함께 남는지 검사한다. main에서 들어오는 공통 변경을 이번 버그 수정으로 다시 작성하지 않는다.

## Task 2 — 다른 부품 ID로 기록된 동일 재고의 입고 중복 차단

현재 저장 검사는 원래 부품 ID끼리 비교하지만 계산은 공용 품목 ID로 합산한다. 예를 들어 별칭 부품의 입고 5개가 이미 포함된 공용 품목 재고 105개를 다시 등록하면 110개가 된다.

- [x] `test_shared_inventory.py`에 재현 검사를 먼저 추가한다. 별칭→공용 ID와 반대 방향, 같은 ID, 관련 없는 ID, 입고 없는 매핑을 포함한다.
- [x] `LibraryStock.store()`에서 `library_inventory_identity.links(svc)`의 기존 매핑으로 스냅샷의 실제 부품 키와 `received_totals(svc)`의 실제 부품 키를 각각 정규화한 후 교집합을 검사한다.
- [x] 해당 입고 원장이 존재하면 기존 `RECONCILIATION_REQUIRED` 정책으로 미대사 스냅샷 등록을 거부한다. 입고 시각만으로 임의 상계하는 새 정책은 만들지 않는다.
- [x] `shared_totals()`의 전체 키를 대사 검사에 쓰지 않는다. 이 함수는 입고 없는 연결 별칭도 0으로 추가하므로 정상 최초 재고까지 차단할 수 있다.
- [x] 실패한 등록이 기존 재고를 변경하지 않으며, 기존 100개+입고 5개가 105개로 유지되는지 확인한다. 기존 입고 원장 경로들의 합격량·보류량 경계도 유지한다.

## Task 3 — 조회 기준일 대신 실제 관측 시각으로 재고 선택

시간을 구분한다: `payload.scope.as_of`는 조회 기준 시각, `selected[].observation.as_of`는 실제 관측 시각, DB `created_at`은 저장 시각이다. 새로운 시간 필드를 추가하지 않는다.

- [x] 11/03 관측 100개(조회 기준 11/03)와 11/02 관측 200개(조회 기준 11/04)에서 100개가 선택되는 검사를 추가한다. 입력 순서를 뒤집어도 같아야 한다.
- [x] `shared_stock()`에서 실제 관측 시각을 기준으로 선택한다. 기존 `available_stock()`의 행/문서 시각 상속, 미래 관측 제외, 단위·회사·창고·로트 범위 검증을 재사용한다.
- [x] `LibraryStock.latest()`의 `created_at` 기준 구성별 선제 1건 선택을 수정한다. 뒤늦게 등록한 오래된 관측 때문에 먼저 등록된 최신 관측이 후보에서 사라지면 안 된다.
- [x] 동일 실제 시각의 상충은 계속 거부한다. 서로 다른 시간대 표기로 같은 순간을 표현한 경우도 포함한다. 결과 근거에는 선택한 실제 관측과 원본을 유지한다.
- [x] 여러 로트는 로트별 관측과 완전한 재고 범위를 함께 판단한다. 한 로트의 최신 시각으로 전체 품목을 최신이라고 취급하거나, 과거/현재 로트의 합집합으로 재고를 부풀리지 않는다. 범위를 대사할 수 없으면 기존 미확인/충돌 원칙을 유지한다.
- [x] 기존 ‘두 제품 100/100 → 30개 예약 → 70/70 → 해제 → 100/100’ 검사와 누락·미확인 수량 검사를 통과시킨다.

## Task 4 — 실패한 계산 재시도와 새 계산 구분

현재 `reviewSave()`는 호출할 때마다 새 UUID를 만든다. 서버에 저장됐으나 응답이 사라진 뒤 같은 버튼을 누르면 다른 요청으로 처리된다. 서버에는 이미 동일 요청 키 재생 기능이 있다.

- [x] 실제 `planning.js`의 세 계산 버튼을 실행하는 Node VM 회귀 검사를 새 파일에 작성한다. 별도 계산 로직을 테스트에 복제하지 않는다.
- [x] A 전용 미완료 계산 키에 기존 `requestKey()`와 사용자·저장소별 저장 상태를 재사용한다. action과 계산 본문으로 동일 시도를 구별하고 성공 응답을 확인한 뒤 해당 키를 해제한다. 공통 `app.js`는 수정하지 않는다.
- [x] 같은 입력의 실패 재시도는 같은 키, 다른 계산 입력은 다른 키, 성공 후 명시적인 새 계산은 새 키를 사용한다. 화면 재진입·새로고침에서도 미완료 키를 유지하고 다른 사용자/저장소와 혼용하지 않는다. 브라우저 저장을 사용할 수 없는 경우의 보장 범위도 검사한다.
- [x] 응답 유실 상태를 단순한 ‘저장 안 됨’으로 표시하지 않는다. 동일 계산을 재시도할 수 있게 하며 성공 후의 새 계산 동작과 구분한다. 화면 단계나 메뉴를 재설계하지 않는다.
- [x] 같은 helper를 사용하는 원문 대조 저장 두 곳도 재시도/성공 후 재저장 동작이 유지되는지 검사한다. RFQ 수정·메일 저장의 기존 키 처리는 보존한다.
- [x] 실제 HTTP/격리 DB에서 첫 저장 후 응답 유실을 모사하고 동일 키로 재요청한다. 검토·RFQ ID가 같고 저장 행이 늘지 않아야 한다.
- [x] 재고를 변경한 뒤에도 같은 키 재시도는 첫 결과를 복구하고, 새 키의 명시적 계산은 최신 재고를 반영하는지 검사한다. 추가 요청의 fingerprint 기반 ID 정책은 유지한다.

## Task 5 — 독립 요청 입력창의 iframe 차단

- [x] `test_request_entry.py`에 실제 loopback HTTP 서버와 `Entry` 대역을 이용한 검사를 추가한다. 외부 LLM·메일·업무 DB 호출은 필요하지 않다.
- [x] `request_entry.py`의 `Handler.end_headers()`에서 `X-Frame-Options: DENY`, `Content-Security-Policy: frame-ancestors 'none'`를 일괄 추가한다.
- [x] HTML/JSON 200, Host/Origin/CSRF 거부 403, 잘못된 요청 400, 없는 경로 404, 처리 오류 500, 기본 미지원 메서드 501의 헤더를 확인한다. 거부된 요청은 업무 메서드를 실행하지 않아야 한다.
- [x] 기존 `no-store`, `nosniff`, Host/Origin/CSRF 검증은 유지한다. `request_entry.html`은 인라인 script/style을 사용하므로 공통 CSP 전체를 복사해 입력창을 망가뜨리지 않는다.
- [x] 독립 창의 정상 요청 입력·해석/연결 기능을 확인하고 iframe 삽입이 차단되는지 확인한다. 공통 Handler를 이미 상속하는 B 계약·납품 서버는 수정하지 않는다.

## Task 6 — 검증·완료 기록

아래는 구현 후 실행할 명령이다. 저장소 루트에서 의존성 설치 및 격리 시험 DB 환경을 준비한 Python을 사용한다. DB 검사가 환경 미설정으로 건너뛰면 통과로 보고하지 않는다.

```sh
python3 -m unittest tests.materials.test_shared_inventory tests.materials.test_materials -v
python3 -m unittest tests.materials.test_planning_retry_ui tests.materials.test_workspace_http tests.materials.test_request_workflow -v
python3 -m unittest ontologics.order_fulfillment.test_request_entry tests.test_web tests.order_fulfillment.test_compact_web -v
node --check ontologics/static/planning.js
git diff --check
```

- [x] 기존 BOM/공정 재고·입고 경로에서 실패나 새로운 영향이 나오면 해당 검사를 추가한다. 단순 UI 표시 검사만으로 재고/저장 오류 해결을 선언하지 않는다.
- [ ] 최신 main 결합 결과의 PR CI에서 전체 `python -m unittest discover -v`, `examples.cq_check`, `examples.agent_tools` 결과를 확인한다. 로컬 전체 반복 실행을 완료 조건에 추가하지 않는다.
- [x] `PROJECT_STATUS.md`와 기존 A 화면 계획 문서에 실제 통과·실패·건너뜀·미검증 및 최종 검증 커밋을 기록한다. 수정 전 재현과 수정 후 성공을 구분한다.
- [x] 사용자에게 네 항목의 화면 변화·수정 파일·회귀 결과를 보고한다. PR 반영/푸시는 후속 요청 범위에 따라 수행한다.

## Review Focus

- 대사 검사 때문에 입고 없는 정상 재고 등록까지 차단되지 않는가.
- 실제 관측 시각을 선택하면서 회사·창고·로트 범위, 상충·미확인 및 예약 30개 공유를 보존했는가.
- 실패 재시도는 하나의 결과를 복구하고, 명시적 새 계산은 같은 수량이어도 최신 재고를 사용하는가.
- iframe 보호를 추가하면서 독립 요청창의 인라인 UI와 기존 요청 검증을 유지했는가.
- PR #15의 C 구현과 공통 경계, A의 `materials` 경로를 함께 보존했는가.

## 2026-11-05 실행 결과

- main PR #15 병합: `3e08091`. 두 문서 충돌 해결, C 구현과 A 경로 보존 확인.
- 재현 검사 실패 후 수정: 입고 별칭 중복, 실제 관측 시간/등록 순서, 동일 시점 상충/시간대/미래 관측, 보고 누락·미확인, 계산 키 재시도, 입력창 iframe 헤더.
- 독립 검토 보완: 다른 검토자의 동일 원문 대조 저장을 자신의 응답 유실로 오인하지 않게 actor 경계를 유지했다. 합쳐진 조회 기준에서 관측 경과 시간을 다시 계산하고 복수 품목·로트 근거를 보존했다. 두 보완 모두 실패 재현→수정 후 성공→재검토 완료.
- 그룹별 검사: 공통/B/C 웹 28개 통과, 인접 workspace/공정/계획 53개 통과, 재고/계약/산술/대체/대당 62개 통과, 최종 경과 시간 보완 후 재고/산술/계획 63개 통과, 최종 결합 HTTP/요청/Node UI/입력창 30개 중 29개 통과·1개 건너뜀. 그룹 간 중복 제거 시 **173개 통과**, 기존 독립 입력창 opt-in 등록 통합 **1개 미실행**이다. 실제 격리 Neo4j 요청 조회·RFQ 검사는 통과했다.
- 초기 sandbox 로컬 접속 제한과 선택한 Python의 Neo4j 드라이버 누락은 환경 문제로 기록하고, 허용된 로컬 접속/설치된 의존성 환경에서 재검사했다. 최종 실패는 없다.
- 실제 브라우저에서 임시 입력창의 정보 로딩·예시 입력·preview 응답과 외부 iframe 차단을 확인했다. 모델/DB가 없는 Entry 대역이며 실제 LLM 해석·요청 등록 검증과 구분한다. 임시 서버와 탭은 종료했다.
- JavaScript 구문 및 diff 공백 검사 통과. 공개 스키마·공통 실행 규칙·운영 DB 변경 없음. 실행 로그는 `/private/tmp/ab-review-main-regression-20261105.log`, `/private/tmp/ab-review-adjacent-regression-20261105.log`, `/private/tmp/ontologics-stock-final.log`, `/private/tmp/ontologics-stock-age-green.log`, `/private/tmp/ab-review-final-linked-20261105.log`에 로컬 보관한다.
- 전체 CI는 PR #16의 최신 커밋 Checks에서 확인한다. 로컬 회귀 완료와 CI 완료·검토자 승인은 구분한다. 보고 누락에는 실제 관측 시각이 없으므로 최신 보고 기준 시각을 적용하며, 값이 존재하는 관측의 최신성은 실제 관측 시각으로 판단한다. 세션 저장 불가 시 새로고침 복구와 대량 재고 이력 성능 평가는 보장 범위 밖이다.
