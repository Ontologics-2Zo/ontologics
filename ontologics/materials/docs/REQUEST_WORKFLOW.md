# 그래프 요청 확인 → 자재 검토 → RFQ 초안

## 사용 흐름

A 계약 보관함 상단 **새 요청 확인**을 누르면 공유 그래프에서 order_fulfillment가 등록한 `materials_review_request` SourceDocument/Evidence를 읽는다. 자동 폴링·새 모델 도구·LLM 호출은 추가하지 않았다. 사용자가 명시적으로 실행하는 버튼이 업무 진입점이다.

요청을 펼치면 개인별 확인 시각만 기록한다. **자재 검토 → RFQ 초안**을 누르면 요청에 저장된 생산 수량으로 BOM과 공정 자재를 계산하고 A의 검토 이력과 미발송 문서를 저장한다. 수량 중복 입력은 없다. 공정 여유율은 기본 10%이며 담당자가 바꿀 수 있다. 저장 결과는 당시 재고 기준이다. 발송 전 현재 재고로 다시 검토해야 한다.

- 공통 lookup/configuration 및 기존 order_fulfillment 읽기·문서 검증을 재사용한다. B 코드는 변경하지 않았다.
- 원문 해시, 등록 기준 그래프 버전, 제품, 기존 계획 Evidence, 계약 품목 → 계약, 선택 납품 차수를 확인한다. 등록 당시 참조와 현재 참조가 달라지면 보완한다.
- 요청 개정은 최신 수량으로 대체한다. 30대 → 40대 수정은 40대이며 70대로 합치지 않는다. 최신 개정 오류 시 이전 개정을 대신 처리하지 않는다.
- 계약 품목 미연결, 변경 조건, 참조 충돌은 보완 필요로 표시한다. 동명 고객이나 같은 제품이라는 이유로 계약을 선택하지 않는다.
- BOM 공용 재고·예약 계산, 공정 공용 재고와 대당 사용량 계산을 재사용한다. 계약 회사와 재고 회사가 다르면 차단한다. BOM 창고와 공정 창고는 달라도 되며 각각의 회사·창고·시점을 표시한다.
- 기준 미등록·재고 미확인은 0으로 보지 않는다. RFQ에서 제외한 항목과 이유를 표시한다. 확인된 부족량만 초안에 넣는다.
- RFQ는 구매팀 검토 자료다. 공급사·입고일·가격·MOQ·포장 단위는 미확정이다. 생산 완료일/출고일/고객 도착일을 자재 입고일로 복사하지 않는다.
- 여러 요청은 각각 현재 재고로 검토한다. 예약하지 않으므로 여러 초안의 부족량을 합산해 발주하면 안 된다. 통합 수요 배분·실제 예약·소비 차감·발주·외부 발송·자동 화면 갱신은 이번 범위 밖이다.
- 기존 신규 계약의 접수·해석은 계약 보관함의 기존 흐름을 사용한다. 이 기능은 그래프에 등록된 추가 생산 요청의 입구다.

## 파일과 저장 경계

- `ontologics/materials/request_workflow.py`: 발견, 검증, 최신 개정 선택, A 확인/검토 기록, 계산 및 RFQ 문서.
- `ontologics/materials/contract_library_web.py`: A 전용 HTTP 진입점과 선택적 공유 요청 저장소 설정.
- `ontologics/materials/library_static/request_inbox.js`, `index.html`: 요청별 펼침, 확인·보완 상태, 계산표, 문서 다운로드.
- `tests/materials/test_request_workflow.py`: 실제 격리 PostgreSQL·Neo4j·HTTP 검증.

A 전용 `pp_request_seen`과 `pp_request_reviews` 테이블은 공통 그래프 타입이 아니다. 공통 스키마·T-box·B 등록 데이터는 변경하지 않는다. B 원문에 A 읽음/처리 상태를 덮어쓰지 않는다. 초안 저장은 신뢰된 사용자 `review` 권한과 공통 감사·멱등 경로를 사용한다. 조회만으로 검토/생산 승인 상태를 만들지 않는다.

## 공유 그래프 설정

기본값은 현재 A 저장소다. 개발 시 B 요청 저장소가 별도인 경우 실행 옵션을 지정한다.

```sh
python -m ontologics.materials.contract_library_web \
  --schema A_WORKSPACE_SCHEMA \
  --request-schema SHARED_REQUEST_SCHEMA \
  --request-database-env /absolute/path/request-database.env \
  --request-graph-env /absolute/path/request-graph.env \
  --request-graph-namespace SHARED_GRAPH_NAMESPACE
```

요청 DB 파일은 `DATABASE_URL`, 그래프 파일은 기존 `ONTOLOGICS_GRAPH_BACKEND`, `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`, `ONTOLOGICS_GRAPH_NAMESPACE` 규약을 사용한다. 명시한 요청 파일은 A 작업 저장소의 환경 변수와 분리해 읽는다. 비밀값은 커밋하지 않는다. 현재 버전뿐 아니라 요청 provenance의 reference_data_version도 공통 동기화로 준비되어 있어야 한다. 미동기화 시 빈 요청 목록으로 위장하지 않고 공통 조회 오류를 반환한다.

운영에서는 같은 공유 저장소·인증·재고 소유 회사를 맞춰야 한다. 기존 로컬 개발 신원은 운영 인증 완료를 뜻하지 않는다.

## 검증 기록 / 판단

2026-11-03: 실제 로컬 PostgreSQL 격리 스키마에서 등록·사람 분리 검토·반영 → 발견 → 개인 확인 → BOM/공정 부족 → RFQ 저장을 검증했다. 실제 Neo4j의 기준/현재 버전 동기화 후 같은 경로도 검증했다. 모델 호출은 0회다.

판단: 공통 그래프의 계약 ID와 A 원본 보관함 UUID는 다르다. 보관함 계약을 임의 생성하지 않고 기존 계산 함수를 직접 재사용하는 A 어댑터를 추가했다. 더 풍부한 요청 그래프 타입으로의 리팩토링은 사용자가 보류한 범위다.

독립 검토 지적 3건(회사 재고 혼용, 잘못된 최신 개정의 이전 버전 회귀, 화면 상태 미갱신)을 수정했다. 회사는 일치해야 하지만 서로 다른 BOM/공정 창고를 같은 창고로 강제하지 않는다.

현재 보존된 `MREQ-LIVE-20261103-001` 30대 요청은 계약 품목 참조가 없으므로 보완 필요다. 한빛 계획을 무관한 기존 계약에 연결하거나 기존 요청을 덮어쓰지 않았다. 성공 경로는 계약 품목 참조를 가진 격리된 가상 업무 요청으로 검증했다.

최종 실행 결과:

- 전체 `unittest discover` 1,651개, 실패/오류 0, skip 11. 전체 실행 후 마지막 보완을 포함한 요청 전용 12개를 별도 재실행하여 모두 통과했다.
- skip: 기존 Shapely 2 미설치 4개, Docker sandbox 이미지 미설정 6개, 명시적 B 입력 UI 통합 환경 미설정 1개. 이번 A PostgreSQL/Neo4j/HTTP 테스트는 skip 없이 실행했다.
- 공통 demo 격리 실행: 버전 1→2→3, U104 A `needs_confirmation→excluded`, B `needs_confirmation→included` 유지. T-box/export 파일 변경 없음.
- 실제 브라우저 클릭으로 계약 연결된 200대 검증 요청 → 확인함 → 검토 이력 있음, BOM 부족 63종과 플럭스 45.828g의 RFQ 초안, 미확인 11종 제외를 확인했다. 이 UI 검증용 DB는 종료 시 정리했다.
- 기존 A 화면(51171)을 재시작해 별도 로컬 공유 요청 그래프 v2 조회를 확인했다. 기존 30대 요청은 계약 미연결 보완 필요 상태이며 내용은 변경하지 않았다.
- Python 문법, JavaScript 문법, `git diff --check` 통과. 실행 코드 변경은 materials 내부, 테스트는 tests/materials, 문서는 materials/docs로 한정했다.

## 2026-11-03 — 비용 정책 오류 및 최초 계약 표시 보완

실제 화면의 `COST_POLICY_MISMATCH`를 비용 정책이 있는 A 호스트/없는 공유 조회 호스트 조합으로 재현했다. 이전 테스트는 양쪽 모두 비용 정책이 없어 오류를 잡지 못했다. A 요청 세션이 먼저 공통 예산을 소유하고 공유 조회 세션이 이를 상속하도록 순서를 수정했다. 공통 제한을 완화하거나 새 LLM 호출을 추가하지 않았다.

최초 계약은 ContractItem → Contract → Evidence → SourceDocument를 따라 표시한다. 고객·생산 회사·기존 계약 수량·추가 요청량·근거 위치와 보관 원문을 제공한다. 원문은 저장된 SHA-256과 일치할 때만 표시한다. 과거 `data/production_planning/` 경로는 `data/materials/`의 동일 해시 파일을 읽되 그래프 원본 메타데이터는 변경하지 않는다. 저장소 data 바깥 파일은 읽지 않는다. 원문은 작성 당시 표현이며 현재 그래프 등록 상태와 구분해 안내한다.

요청 테스트 14개 통과. 별도 비용 정책 회귀와 최초 계약 원문 해시 불일치 차단을 포함한다. 사용자 화면의 실제 120대 요청을 HTTP 경로로 재실행해 기존 계약 100대·이번 추가 120대 분리, 원문 해시 일치, RFQ 초안 64종, 미확인 제외 11종을 확인했다. 재고 예약·발주·외부 발송은 없다. 실행 기록은 커밋 제외 `.local/live-budget-fix-result.json`에 있다.

이 보완 후 전체 회귀: 1,658개 실행, 실패/오류 0, 기존 환경 조건 skip 11. 로그는 `/private/tmp/request-fix-full.log`. JavaScript 문법 및 diff 공백 검사도 통과했다. 실제 화면 서버를 재시작하여 변경을 반영했다.
