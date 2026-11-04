# 계약 자동 검토 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 계약 원문의 근거가 있는 항목을 자동 검토에 사용하고, 현재 작업을 막는 누락·충돌만 질문한다.

**Architecture:** 기존 계약 보관 웹의 LibraryReview → LibraryProductReview → LibraryMaterials 경로에 읽기 전용 자동 검토 경로를 추가한다. 기존 사람이 확인한 기록·제품 연결·자재 검토 저장은 유지하며, 자동 결과를 사람의 승인으로 바꾸지 않는다. 공통 온톨로지의 제품 구성과 BOM 관계를 조회해 주문량 기준 총소요량을 계산한다.

**Tech Stack:** 기존 Python 3.10+, PostgreSQL/psycopg 3, 정적 JavaScript, unittest. 새 의존성 없음.

**Spec:** 이 문서의 범위 및 동작 명세는 현재 대화에서 요청한 계획을 구체화한 제안이다. 아직 구현되지 않았다.

## Global Constraints

- 제품 코드 변경은 ontologics/materials/ 내부로 제한한다.
- 테스트는 tests/materials/ 내부만 추가·수정한다. 본 계획은 materials/docs/에 둔다.
- 공통 model.py, service.py, host.py, database.py, 공통 스키마·승인·권한, order_fulfillment는 변경하지 않는다.
- materials/schema.py와 rules.py도 공통 그래프 의미에 영향을 줄 수 있으므로 이번 변경에서 제외한다.
- DB 마이그레이션 없음. 기존 review_id에 자동 결과 ID를 넣거나 가짜 확인 사건을 생성하지 않는다.
- 원문 supported는 인용·형식 검증 결과이며 법적 합의나 해석 정확도 보증이 아니다. 자동 결과는 검토용이다.
- 미확인 이행량·배정량은 0이 아니다. 주문량은 실제 신규 생산량이 아니다.
- 공통 파일 변경이 필요해지면 우회하거나 범위를 넓히지 않고 계획을 재검토한다.

## 현재 경로와 변경 범위

현재 LibraryProductReview.reviewed_items는 사람의 accepted/corrected 값만 사용하며 inspect는 확인 기록이 없으면 제품 후보 연결 전에 반환한다. 제품 연결은 review_id에 묶이고 LibraryMaterials.calculate는 사람이 입력한 생산 검토 수량과 근거를 저장한다. 후속 구매·예약도 이 저장 결과를 사용한다.

현재 화면도 supported 값을 미리 채우고 전체 검토를 한 번에 저장한다. 이를 신규 절감 효과로 계산하지 않는다. 이번 변경은 사람 확인 전에도 자동 검토와 주문량 기준 총소요량을 볼 수 있게 하고, 같은 화면에서 필요한 수정과 명시적 확인·제품 연결을 마친 뒤 생산 수량 검토로 이어지도록 한다. 누락·충돌은 자동으로 accepted 처리하지 않는다. 최종 저장 확인, 제품 연결 및 실제 생산 검토량 근거 입력은 기존 권한과 검사를 유지한다.

이 범위에서는 모든 중간 클릭이 사라지지 않는다. 줄이는 것은 반복적인 항목 판독·입력과 검토 결과를 보기 전의 사람 확인 의존성이다. 내부 이행·배정 기록의 완전성을 입증해 실제 생산량을 자동 확정하거나, 변경 결과를 전체 업무로 전파하는 기능은 후속 범위다.

## 파일 목록

신규 제품 파일:
- ontologics/materials/contract_readiness.py: 작업별 필수 정보, 자동 사용 가능 필드, 질문 및 결과 의존성 판정.

수정 제품 파일:
- ontologics/materials/library_product_review.py: 기존 inspect/connect를 유지하고 inspect_preview 추가. 최신 원문·해석·카탈로그를 읽어 자동 후보와 작업 가능 여부 반환.
- ontologics/materials/library_materials.py: preview 추가. 확인 기록 없이 주문량 기준 총소요량을 계산하되 저장·예약·구매 입력으로 승격하지 않음.
- ontologics/materials/contract_library_web.py: planning-preview/materials-preview 조회 라우트 추가.
- ontologics/materials/library_static/app.js: 자동 검토 요약·차단 질문·총소요량 표시, 같은 화면에서 기존 review→planning→connect API로 이어지는 명시적 확인·연결 및 실패 후 재개.

테스트:
- 신규 tests/materials/test_contract_readiness.py
- 수정 tests/materials/test_contract_library.py

기존 contract_intake.py, contract_interpretation.py, library_review.py, demand_review.py, demand_confirmation*.py, contracts.py는 읽기·재사용 대상으로 두고 이번에는 수정하지 않는다. 기존 추출의 supported/missing/conflict 상태를 재사용하므로 모델 출력 스키마도 바꾸지 않는다.

## 동작 명세

1. 자동 판정 필드는 원문 supported 및 현재 유효한 사람의 accepted/corrected 결정으로 제한한다. 명시적인 미해결 결정은 원문 값으로 덮어쓰지 않는다.
2. 제품 매칭은 기존 manufacturer/product_model/revision/variant 식별 기준을 유지한다. 정확 후보가 한 개일 때만 자동 검토에 사용한다. 0개·복수 후보는 질문으로 남긴다.
3. 총소요량 작업은 정확 구성, 양의 정수 주문량, 명확한 완제품 단위를 요구한다. 기본 단위 대응은 piece/대이며 원문 단위와 정규화 근거를 함께 표시한다. 다른 단위는 질문한다.
4. 배송지·정확한 도착 시각·이행량·배정량 누락은 주문량 기준 총소요량을 막지 않는다. 미확인 자체는 숨기지 않는다.
5. 결과는 action, ready, blocking_questions, other_unknowns, basis를 반환한다. basis에는 원문 hash, interpretation_id/result_sha256, 사람 결정 ID(있으면), data_version, 구성 ID 및 규칙 버전을 담는다.
6. 원문/해석 무결성 오류나 DB 조회 실패는 미등록이나 정상 빈 결과로 바꾸지 않는다. 근거 변경 시 기존 자동 결과를 재사용하지 않는다.
7. 화면은 “주문량 기준 총소요량”을 명시한다. 신규 생산량·재고 차감 후 부족량·구매 수량으로 표시하지 않는다. 자동 결과에는 confirmed=true나 review_id를 발급하지 않는다.

## Review Focus

- 사람의 수정·미해결 결정과 자동 원문 값이 충돌할 때 이전 자동 값이 되살아나지 않아야 함 → Task 1.
- 같은 이름의 여러 제품 구성에서 임의 선택하지 않아야 함 → Task 2.
- 해석 또는 카탈로그 변경 직후 오래된 결과가 표시·사용되지 않아야 함 → Task 2/3.
- DB 장애를 계약·구성 부재로 보여주지 않아야 함 → Task 2.
- 미확인 재고·이행량에서 총소요량을 신규 생산·구매량으로 오해하지 않아야 함 → Task 3/4.

## Task 1: 자동 검토 규칙

**Files:** contract_readiness.py, test_contract_readiness.py.

**Interfaces:** evaluate_contract_readiness(fields: dict, candidates: list, *, basis: dict) -> dict. fields는 value/state/source를 가진 유효 결정과 원문 필드의 정규화 결과. candidates는 기존 비교 결과의 exact 플래그를 포함한다. 반환은 동작 명세 5의 구조.

- [ ] 정상 구성·주문량 100·단위 대, 배송지 없음 → 총소요량 ready=true 테스트 작성.
- [ ] 수량 충돌, 복수 exact, 알 수 없는 단위 → ready=false 및 해당 항목 질문 테스트 작성.
- [ ] 사람의 corrected와 unresolved가 원문 supported보다 우선함을 테스트.
- [ ] `python3 -m unittest tests.materials.test_contract_readiness -v`로 실패 확인 후 규칙 구현, 같은 명령 통과 확인.

## Task 2: 기존 온톨로지 조회 연결

**Files:** library_product_review.py, test_contract_library.py.

**Interfaces:** LibraryProductReview.inspect_preview(context, contract_id) -> 기존 envelope 형식. data.items 각각에 fields/candidates/readiness/basis를 추가하며 기존 inspect/connect의 반환 계약은 유지.

- [ ] 사람 확인 기록 없이 정확 구성 후보와 readiness를 반환하는 시험 추가.
- [ ] 무결성 실패·조회 실패·복수 후보·새 해석/데이터 버전에서 재평가되는 시험 추가.
- [ ] `python3 -m unittest tests.materials.test_contract_library -v`로 실패 확인.
- [ ] latest/get의 검증과 기존 공개 configuration 조회를 재사용해 inspect_preview 구현. reviewed_items의 기존 의미는 바꾸지 않음.
- [ ] 같은 명령 통과 확인. 기존 connect가 사람 확인을 계속 요구하는 회귀 포함.

## Task 3: 주문량 기준 총소요량 미리보기

**Files:** library_materials.py, contract_library_web.py, test_contract_library.py.

**Interfaces:** LibraryMaterials.preview(context, contract_id, item_index) -> envelope. 서버가 inspect_preview로 최신 basis를 다시 구하고 ready일 때만 기존 materials 계산 함수를 사용. 경로 /api/tools/contracts/planning-preview 입력 contract_id, /api/tools/contracts/materials-preview 입력 contract_id/item_index.

- [ ] 주문량 100 × 제품당 부품 2 = 총소요량 200 테스트. 신규 생산량·구매량은 확정되지 않았음을 별도 상태로 검증.
- [ ] 미확인 재고를 0으로 생성하지 않고, 미확인 BOM 항목을 숨기지 않는 시험 추가.
- [ ] 호출 전후 reading_checks/configuration_links/material_reviews 및 예약·구매 기록이 생성되지 않는 시험 추가.
- [ ] 실패 확인 후 preview/조회 라우트 구현. 기존 calculate/save 경로는 변경하지 않음.
- [ ] `python3 -m unittest tests.materials.test_contract_library tests.materials.test_materials -v` 통과 확인.

## Task 4: 화면과 전체 검증

**Files:** library_static/app.js, test_contract_library.py.

- [ ] 자동 검토가 가능하면 사람 확인 폼 이전에 결과를 표시. 질문은 blocking_questions, 나머지는 접힌 미확인 상세로 표시.
- [ ] 기존 미리 채우기를 유지하고, 충돌/미확인과 사용자 수정은 덮어쓰지 않음. 자동 후보와 근거, 확인 대상으로 저장할 항목 요약을 표시한 뒤 사용자의 ‘내용 확인·제품 연결 후 생산 수량 검토’ 동작만 review→planning→connect를 순서대로 호출. 숨겨진 항목까지 사람이 개별 검토했다고 표시하지 않음.
- [ ] review 성공 뒤 최신 planning을 조회하고 선택 구성·데이터 버전을 대조. 변경이 있으면 자동 연결하지 않고 차이를 표시. 유효한 기존 확인·연결은 재사용하고 showMaterials로 바로 이동. 이미 연결된 재방문에서도 미리보기/확인을 강제하지 않음.
- [ ] review 성공 후 connect 실패 시 ‘확인은 저장됨·연결 미완료’를 표시하고 현재 상태를 재조회해 연결부터 재개. 한 요청의 재전송에는 동일 request_key 사용. 여러 API를 원자적 저장으로 표현하지 않음.
- [ ] 입력을 수정하면 기존 미리보기를 즉시 오래된 결과로 표시. ‘수정 내용 저장·다시 검토’에서 기존 review 저장 후 새 결과 조회. 저장되지 않은 수정값을 서버 검증 완료로 표시하지 않음.
- [ ] 정상 계약은 기존 ‘해석 확인→제품 연결→자재 화면 이동’을 한 명시적 진행 동작으로 연결하는지, 복수 구성은 선택을 요구하는지, 여러 품목 중 미확인 품목 때문에 준비된 품목의 미리보기가 막히지 않는지 검증. 부분 실패·새로고침·재방문 동작도 확인.
- [ ] 정상·수량 충돌·복수 구성·배송지 누락·해석 변경 사례를 브라우저에서 확인. 자동 로딩만으로 확인 API 호출이 발생하지 않는지 검증.
- [ ] 기존 제품 연결→자재 저장→구매/예약 경로가 동일하게 동작하는지 회귀 확인.
- [ ] `python3 -m unittest discover -s tests/materials -v`, 이후 저장소 지침의 `python3 -m unittest discover -v`, `python3 -m ontologics.demo` 실행. DB가 필요한 검사는 격리 DB 사용. 실패·환경 미검증을 구분해 기록.
- [ ] git diff로 제품 코드가 materials 내부이고 테스트도 전용 폴더에 한정되는지 확인. 완료 기록은 이 문서에 추가한다.

## 예상 규모 및 완료 기준

제품 코드 신규 1개 + 수정 4개, 테스트 신규 1개 + 수정 1개, 계획 문서 1개. 추가 CSS/HTML 변경은 현재 DOM 생성 방식으로 필요하지 않을 것으로 예상하며 필요하면 범위를 재산정한다.

완료 기준은 정상 계약에서 항목별 사람 확인 없이 제품 연결 후보와 주문량 기준 총소요량을 보고, 누락·충돌만 해결하면 기존 명시적 확인·저장 절차로 이어지는 것. 기존 승인 기록의 의미·공통 코드·공통 DB 스키마를 유지해야 한다. 자동 검토는 사람 확인이나 생산 승인과 동일하지 않다.

## 최종 흐름 검토 보완 — 2026-11-02

정적 코드 검토 결과, 원안은 미리보기 추가에 치우쳐 기존 화면으로 돌아가는 흐름과 사람 개입 감소를 충분히 정의하지 못했다. 위 Task 4에 같은 화면의 명시적 확인·연결과 부분 실패 재개를 추가했다. 구현·브라우저 실행으로 검증한 결과는 아니다.

목표 흐름: 계약 열기 → 자동 검토와 주문량 기준 총소요량 → 필요한 항목만 수정/후보 선택 → 확인·연결 후 생산 수량 검토 → 실제 생산 검토량과 근거 입력 → 저장된 자재 검토 → 기존 구매·예약 흐름.

미해석/처리 중/실패는 독립 상태로 표시하고 해석 실행·새로고침·재시도 동작을 제공한다. 이를 정보 누락 질문이나 빈 카탈로그로 바꾸지 않는다. 구매·예약 진입은 기존 저장된 자재 검토에서만 허용한다.

추가 필수 검증:
- 사람 수정 수량은 기존 API가 문자열로 저장한다. 규칙에서 양의 십진 정수 문자열을 명시적으로 정규화하고 원문/수정값을 보존한다. 100은 사용 가능, 소수·음수·bool·100대·100,000은 자동 변환하지 않고 질문한다. 신규 생산 검토량의 기존 0 허용 규칙과 혼동하지 않는다.
- BOM이 부분 미확인이면 ‘전체 계산 완료’로 표현하지 않는다. ready는 계산 시도 가능 여부이며 결과에는 완전/부분/오류를 별도로 표시한다. 제품당 부품 수량이 미확인인 행은 총소요량도 미확인이다.
- 조회 preview의 자동 사용 가능 여부와 영속 연결 가능 여부를 구별한다. 정확 후보가 복수이면 자동 선택하지 않는다. 사용자는 기존 exact 후보 중 명시적으로 선택하고 확인·연결할 수 있다.
- 문서 전체 confirmation API는 모든 필드 decisions를 요구한다. 일부 품목의 누락은 unresolved로 보존하고 정상 품목만 다음 단계로 진행한다. 자동 검증과 사람 확인의 출처 표시를 유지한다.

정상 신규 계약에서 절감 목표는 확인·제품 연결·다음 화면 이동을 하나의 명시적 진행 동작으로 묶고 확인 전 계산을 제공하는 것이다. 실제 생산 수량·근거 입력과 자재 저장은 계속 필요하다. 클릭·시간 감소율은 구현 후 같은 샘플 계약으로 측정하며 지금 확정하지 않는다.

## 실행 기록

- 작업 브랜치 refactor/reduce-manual-intervention의 현재 체크아웃을 재사용. 공통 파일 수정 없음.
- Task 1: 신규 규칙 부재 실패를 확인한 뒤 단위 시험 5개 통과.
- Task 2/3: 신규 API ROUTE_NOT_FOUND 실패 후 실제 격리 PG 통합 시험 통과. 초기 테스트 Service context-manager 오류 및 중첩 감사 트랜잭션의 조회 실패를 수정했다.
- 기존 계약 회귀 + 규칙 18개 통과(82.663초). 별도 전체 회귀 실행 중.
- 독립 리뷰의 PDF 원문 삽입 오류, 재해석 callback 누락, 품목별 미리보기 실패 전파, 동시 확인 기록 무조건 채택 문제를 수정. 실제 JS 핸들러를 실행하는 Node DOM 테스트 추가.
- Ruling: 사용자가 실제 웹 테스트 세팅을 추가 요청했으므로 auto_review_demo.py 및 전용 UI 테스트를 추가한다. 기존 계약을 수정·삭제하지 않고 가상 원문과 고정 해석만 기존 저장 API로 추가한다. 실제 모델 품질 시험이 아니며 유료 API 호출 없음.
- 로컬 서버 localhost:51171, 기존 a_contract_library 저장소 사용. 테스트 스키마는 각 시험이 개별 생성·삭제.

## 최종 검증 결과

- 최종 관련 시험 20개 통과(77.689초): 계약 DB 통합 13개, 자동 판정 6개, 실제 JS 핸들러 회귀 1개. 마지막 질문 분류 조정 후 판정/JS 7개도 재검사 통과.
- 실제 브라우저: 정상 계약의 확인 전 총소요량, 명시적 확인·제품 연결→생산 수량 입력, 주문100/생산검토70 구분, 자재 저장→부족 부품·구매 검토 화면 진입 확인. 개정 누락 및 수량 충돌에서는 자동 계산과 진행 보류 확인.
- 독립 리뷰의 4개 문제 수정 후 재검토에서 해당 문제 잔여 없음.
- 공통 demo 격리 DB 실행: 버전1→2→3 성공. JavaScript 구문·git diff 공백 검사 통과.
- 전체 회귀 최초1494개 실행은 실패3/오류15. 원인은 RDF/Neo4j 의존성 누락, macOS TMPDIR 심볼릭 경로, discover의 모듈 이름 차이였다. 임시 의존성 경로와 /private/tmp 및 명시적 테스트 이름으로 실패/미수집 모듈35개를 재검사:30개 통과, Neo4j 서비스 연결 설정 미제공으로 오류5개 잔여. 전체 회귀 통과로 보고하지 않는다. 공통 코드는 변경하지 않았다.
- 로그: /tmp/contract-auto-final.log, /tmp/contract-auto-recovery.log, /tmp/contract-auto-demo.log, /tmp/ontologics-contract-library-results/whole-suite.log.
- 사용자 화면: localhost:51171, 가상 시나리오4건. 01은 브라우저 검증 기록을 보존하고 04는 사람 확인 전 상태로 남겼다. 기존 계약3건 보존.
- 추가된 범위: 제품 신규2개(contract_readiness.py, auto_review_demo.py), 제품 수정4개, 테스트 신규2개/수정1개, materials/docs의 계획 및 웹 시험 안내. 스키마 변경 없음.
