# order_fulfillment → materials 자재 검토 요청 전달

문서 버전: 3.0 / 2026-11-03

## 1. 확정한 방식과 범위

**기존 공통 `SourceDocument`·`Evidence`를 재사용한다. 새 요청 타입과 공통 코드·스키마·T-box 변경은 없다.** 이전 2.x의 `AdditionalMaterialReviewRequest` 확장 계획을 대체한다.

`order_fulfillment`가 요청 내용을 검증하고 업무 요청서를 생성한 뒤, 기존 공통 초안 → 사람 검토 → 호스트 반영 → Neo4j 동기화 경로를 사용한다. `materials`가 나중에 공통 조회로 읽을 수 있는 문서·근거를 준비하는 것이 이번 범위다. `materials`의 발견·해석·계산·RFQ·접수 상태는 구현하지 않는다.

등록 성공은 A 접수 성공이나 생산 승인, 실제 발주를 뜻하지 않는다. 이번 예제는 가상 운영 기록과 개발용 작성자·검토자 신원을 사용하지만, PostgreSQL 등록·승인과 Neo4j 조회는 실제 구현을 실행한다.

## 2. 구현 위치

| 파일 | 책임 |
| --- | --- |
| `ontologics/order_fulfillment/material_request_document.py` | 요청 항목 검증, 원문 생성, 기존 문서·근거 노드 구성 |
| `ontologics/order_fulfillment/material_request_registration.py` | 신뢰된 호스트에서 기존 조회·초안 등록 호출, 정확 ID·버전으로 조회 |
| `examples/order_fulfillment/b_material_request_registration.py` | 격리 스키마를 사용하는 실제 등록·승인·조회 실행 예제 |
| `data/order_fulfillment/sources/material_requests_demo/` | 기준 계획, 업무형 요청서 예시, 실행용 요청 데이터 |
| `tests/order_fulfillment/test_material_request_document.py` | 요청 내용·출처·원문 검증 |
| `tests/order_fulfillment/test_material_request_registration.py` | 실제 PostgreSQL 승인·등록·재시도·실패 경로 |
| `tests/order_fulfillment/test_material_request_readback.py` | 실제 Neo4j 동기화 전 차단 및 공통 조회 내용 일치 |

공통 `domain_schema.py`·`model.py`·`service.py`·`graph_rdf.py`, `order_fulfillment/schema.py`, `ontology/tbox.ttl`과 `materials` 실행 코드는 수정하지 않는다. 새 모델 도구나 전용 API도 추가하지 않는다.

## 3. 무엇을 그래프에 저장하는가

- `SourceDocument`: 기존 필드에 원문 경로·SHA-256·개정·수집 시각·origin을 기록한다. `kind=materials_review_request`는 기존 문자열 필드에 사용하는 분류 관례이며 새 타입이 아니다.
- `Evidence`: `source_id`로 원문을 연결한다. `description`에는 요청서의 구조화 전사(JSON 문자열)를 저장한다. 제품·수량 등이 독립 그래프 필드나 관계가 되는 것은 아니다.
- 원문은 사람이 읽는 Markdown 요청서와 동일 내용을 담은 시스템 보존 기록으로 구성한다. 읽을 때 전사를 다시 렌더링한 해시가 원문 해시와 같은지도 확인한다.
- 제품·계약·납품 ID는 요청 **내용**이다. 기존 `Configuration`·`ContractItem`·`DeliveryRound`를 조회해 실제 존재와 일치 여부를 검사한다. 기존 노드를 변경하거나 가상 관계를 추가하지 않는다.
- 기준 계획은 기존 `Evidence`로 참조한다. 실제 A 문서를 새 그래프 계획 노드로 만들지 않는다. 가상 근거를 real 요청의 사실로 승격하지 않는다.

`materials-request-document-1`은 문서 내부 형식이다. 공통 T-box 스키마나 공통 데이터 버전이 아니다. 이 업무 형식 검증은 order_fulfillment의 책임이며, 다른 등록 경로의 모든 문서를 통제하는 공통 검증이라고 주장하지 않는다.

## 4. 요청 항목과 재사용

| 항목 | 규칙 |
| --- | --- |
| 요청 번호·개정 | 업무 요청을 식별. 최초 개정 1, 수정은 이전 요청 근거와 다음 개정 연결 |
| 요청자·요청일 | 원래 업무 요청자의 명시적 내용. 실제 실행자·기록 시각과 별도로 보존 |
| 대상 제품 | 기존 `Configuration` ID 확인 |
| 기준 계획 | 등록된 기준 계획 `Evidence` ID 확인 |
| 추가량·단위 | 정상 완제품 양의 정수, `piece`. 기존 물량과 구분 |
| 필요 날짜·기준 | 날짜만 있으면 YYYY-MM-DD 유지. 생산 완료/출고/고객 도착 구분 |
| 사유·수량 근거 | 기준 물량과 추가량의 이유를 설명. 전사 수요 중복을 자동 판별한 것으로 간주하지 않음 |
| 변경 조건 | 변경 없음 또는 변경 내용. 미확인은 보완 후 제출 |
| 계약 품목·납품 차수 | 해당하는 경우 기존 참조를 사용. 제품·계약 소속 일치 검사. 기존 납기와 다르면 요청 날짜로 명시 |
| 도착지 | 고객 도착 기준일 때 필수 |
| 수정/독립 관계 | 수정은 이전 요청 근거, 독립 추가는 관련 요청 근거와 다른 요청 번호 |
| 공통 데이터 기준 | 저장소 식별·조회 버전·실행자·기록 시각을 호스트가 결합. 사용자가 실행 신원을 지정하지 않음 |

미확인 필수 항목은 0이나 임의 기본값으로 채우지 않는다. 이 등록 도우미는 보완된 요청만 제출한다. 미확인 요청용 별도 상태 시스템을 만들지 않는다.

## 5. 등록과 재시도

1. 신뢰된 요청 세션에서 읽기·초안 권한을 확인한다.
2. 기존 `lookup`으로 승인 그래프를 읽고 요청의 제품·기준·관련 정보를 대조한다.
3. `data/receipts/order_fulfillment/material_requests/`에 원문을 변경 불가 파일로 보존한다. 이 위치는 기존 Git 제외 경로이며 운영 원문을 자동 커밋하지 않는다.
4. 원문을 기반으로 기존 두 타입의 `put` 작업을 `prepare_draft`에 전달한다.
5. `validate_draft`, 별도 검토자의 `review`, 호스트 `apply_approved`를 사용한다. 개발 예제의 검토 동작은 운영 사람의 실제 검토를 대신하지 않는다.
6. Neo4j 사용 시 기존 호스트의 동기화와 정확 승인 버전 조회를 확인한다. pending을 성공으로 표시하지 않는다.

같은 저장소·실행자·요청 키는 동일 원문을 재사용한다. 같은 키에 다른 내용을 주면 거부한다. 저장소별 기준 버전을 혼동하지 않도록 원문 키에 저장소 식별을 포함한다. 파일 저장 후 초안 실패 시 파일을 덮어쓰지 않고 같은 키로 재시도한다.

요청 참조는 작성 당시 승인 버전에 고정되어 있다. 공통 초안 기준 버전·승인 후 데이터 버전과 구분한다. 후속 A는 계산 실행 시 최신 상황도 다시 확인해야 한다. 수정은 기존 원문/Evidence를 덮어쓰지 않고 새 개정으로 등록한다. 요청 번호·개정이 같은 다른 내용은 동일 ID의 공통 불변 규칙으로 충돌한다. 전용 업무 상태 머신·자동 최신 요청 선택 기능은 이번에 만들지 않는다.

## 6. 조회 결과와 후속 materials 작업

`read_request(session, evidence_id, version=승인버전)`는 기존 `lookup`을 호출해 근거와 원문 메타데이터를 읽고 내용을 대조한다. PostgreSQL/Neo4j에서 동일하게 사용한다. 정확 버전이 필요하며 조회 자체로 읽음·접수 상태를 쓰지 않는다.

`search_evidence`는 원문 파일 전체 검색이 아니라 노드 메타데이터·근거 설명의 후보 검색이다. 60건 제한이 있으므로 모든 신규 요청을 찾아낸 완전한 수신함으로 간주하지 않는다. 이번 검증은 등록 결과의 정확한 근거 ID를 사용한다.

후속 materials 코드가 할 일은 요청 후보 발견·분류, 원문 내용 재검증, 제품 조회, 수정 요청과 독립 수요 구분, 기존 BOM·공정 자재 계산 연결이다. 근거 설명은 자료이며 명령·권한으로 실행하지 않는다. 원문 전체를 읽을 때는 파일 접근 경로와 해시를 별도로 확인한다.

## 7. 실행과 검증

저장소 루트에서 개발 의존성을 설치하고 PostgreSQL+pgvector 및 Neo4j 연결 환경을 준비한다. 비밀값은 Git에 넣지 않는다.

```bash
python -m examples.order_fulfillment.b_material_request_registration --env-file <개발환경파일> --neo4j
```

실행마다 `request_demo_<UUID>` PostgreSQL 스키마를 만들며 지정된 그래프 namespace는 공통 코드에서 저장소별로 분리된다. 기존 업무 스키마를 초기화하지 않는다. 서버와 원문 보존 경로는 실행 환경 담당자가 관리한다. 명령은 개발 검토자 대역으로 승인까지 실행하는 예제다.

검증 사례: 기존 계획 200대에 추가 30대 → 기존 추가 요청을 40대로 수정 → 별도 예비 단말 10대. 30과 40을 70으로 합산하지 않으며, 계산 연결 자체는 후속 범위다. 자기 승인·미승인 반영·구 초안·원문 변조·다른 내용 재시도·권한 없는 조회·미동기화 그래프를 실패로 확인한다.

신규 기능의 실제 검증 기록은 `docs/order_fulfillment/PROJECT_STATUS.md`를 참조한다. 전체 회귀와 신규 기능 검증의 결과를 구분한다.

## 이전 사전 점검의 의미

`b_material_request_boundary.py`의 UNKNOWN_TYPE 결과는 **새 요청 타입을 추가하려던 이전 방식**에만 해당한다. 현재 SourceDocument/Evidence 재사용 방식의 등록 실패를 뜻하지 않는다. 진단 예제와 테스트는 이 차이를 재현하는 기록으로 유지한다.
