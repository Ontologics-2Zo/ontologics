# 공통 도구 계약 0.4.0

Python 3.10+ 내부 함수가 기준이다. 모든 경로는 저장소 루트 기준이며 별도 서비스 배포가 필요 없다. `Service`는 신뢰된 호스트 애플리케이션, `AgentGateway`는 서비스 Agent에 공개하는 도구 경계다. `examples/agent_tools.py`와 `ontologics/demo.py`는 실행 가능한 예제다.

## 그래프·저장·시간

[model.py](../ontologics/model.py)의 `SCHEMA`·허용 필드·검증 함수가 실행 계약이다. [ontology/tbox.ttl](../ontology/tbox.ttl)은 여기서 생성한 OWL/RDFS 클래스·관계 정의이며 자동 추론 엔진이나 SHACL 구현은 아니다. [data/catalog.json](../data/catalog.json)은 실제 원본 정규화 노드, [data/initial_graph.jsonld](../data/initial_graph.jsonld)는 실제·가상 관계를 ID로 연결한 인계용 JSON-LD A-box(데모 버전 3)다. PostgreSQL의 `snapshots.nodes` JSONB에 정규화 노드 묶음을 불변 데이터 버전으로 저장한다. 작은 기준 데이터에 맞춘 저장이며 대규모 그래프 최적화·SPARQL 서버는 포함하지 않는다.

모든 노드는 `id`, `type`, `origin`, `evidence`를 가진다. `origin`은 `real` 또는 `synthetic`이고 `evidence`는 Evidence ID 목록이다. SourceDocument/Evidence 외의 사실은 근거가 필수다.

| type | 주요 필드·관계 |
| --- | --- |
| SourceDocument | url, path, sha256, version, license, collected_at. 저장소 data/ 안의 파일과 해시 대조 |
| Evidence | source_id → SourceDocument, locator, description. 출처와 origin 일치 |
| ProductModel | manufacturer, name, 확인된 specifications |
| ProductRevision | model_id → ProductModel, revision |
| Configuration | revision_id → ProductRevision, variant, name |
| ComponentModel | name, identity_key, package, identity_scope. 현재 원본 범위의 식별 |
| BOMItem | configuration_id, component_id, quantity, unit=piece, positions, population, raw_value |
| ProductUnit | company, serial, configuration_id. 한 보드의 적용 구성 |
| ComponentLot | company, supplier, component_id, lot_code. 네 값으로 로트 동일성 검사 |
| Installation | unit_id, lot_id, position, installed_at, 선택 removed_at/confirmed_until/conflict |
| HistoryCoverage | unit_id, start, end, complete=true. 시작 상태 포함 전체 장착·제거 이력을 확인했다는 근거 |
| InventoryMovement | lot_id, movement=receipt/issue, quantity, occurred_at. 출고를 장착으로 변환하지 않음 |
| Contract | company(우리 회사), customer_name(고객). synthetic 계약 근거 필수 |
| ContractItem | contract_id → Contract, configuration_id → 기존 Configuration, quantity, unit=piece |
| DeliveryRound | contract_item_id → ContractItem, quantity, unit=piece, due_at, due_basis=arrival, destination |
| FulfillmentSnapshot | company, as_of, round_ids → DeliveryRound 목록, confirmations → 아래 9개 확인 범위 |
| FinishedGoodsStock | snapshot_id, company, configuration_id, observed_at, location, quantity, unit=piece |
| ProductionWork | snapshot_id, company, configuration_id, observed_at, target_quantity, completed_quantity, unit, production_state, 선택 started_at/planned_start_at |
| Allocation | snapshot_id, delivery_round_id, quantity, unit, 선택 stock_id/production_work_id 중 정확히 하나 |
| Shipment | snapshot_id, allocation_id, quantity, unit, destination, 선택 planned_departure_at/departed_at/expected_arrival_at/arrived_at |

실제 설계 다섯 타입은 `real`, 기존 운영 다섯 타입·계약 3타입·현황 5타입은 `synthetic`만 허용한다. 제품 소속·로트 부품·장착 위치가 실장 확인된 BOM과 맞아야 한다. 추가 필드로 검사·고장 원인·가동 정보를 끼워 넣을 수 없다. 새 종류의 사양은 공통 계약의 명시적 확장으로 추가한다.

`graph-0.2`는 기존 필드 의미를 유지하면서 계약 3타입을 추가한다. 계약 항목·회차 수량은 양의 **int**이며 0·음수·float·문자열·불리언은 거부한다. BOM/부품 입출고의 기존 수량 규칙은 그대로다. 계약·회차는 `piece`만 지원하며 회차별 합계는 연결된 항목 수량을 초과할 수 없다. `due_at`은 UTC offset이 있는 시각, `due_basis=arrival`은 고객 도착 기준의 기한이며 그 시각 도착까지 포함한다. 실제 도착 사실이나 출고 기한으로 해석하지 않는다. 계약 유효기간은 원문에 보존하며 납품 기한으로 대신 쓰지 않는다.

시간은 `2026-09-20T10:00:00+09:00`처럼 명시적 시각·UTC offset이 필요하다. 날짜만 있는 원본은 이 필드에 임의로 넣지 않고 보완 대상으로 둔다. 구간은 `[start,end)`다. `confirmed_until`은 그 시각 이전까지 장착을 확인했다는 유한한 근거이며 제거 시각이 아니다. 조회의 이력 완전성은 사건 목록만 있는 상태를 뜻하지 않는다.

대표 CQ의 실제 조회 경로는 다음과 같다. 화살표는 필드 참조 방향이며 역방향은 노드 검색이다. [SERVICE_SPEC의 CQ 표](SERVICE_SPEC.md#cq와-원본검증-연결)에서 원본·미확인·업무 기대 결과를 함께 확인한다.

| CQ | 관계 경로와 실행 위치 |
| --- | --- |
| CQ01·02 식별·구성 | `BOMItem.configuration_id → Configuration.revision_id → ProductRevision.model_id → ProductModel`, `BOMItem.component_id → ComponentModel`. 각 노드의 `evidence → Evidence.source_id → SourceDocument`. `lookup`, `configuration` / `service.py` |
| CQ03 구성 비교 | 두 Configuration의 `BOMItem.positions`를 대응해 원문·패키지·실장 상태 비교. 동일성·호환성 추론 없음. `compare_configurations` / `service.py` |
| CQ05·06 A/B | `Installation.unit_id → ProductUnit.configuration_id → Configuration`, `Installation.lot_id → ComponentLot.component_id → ComponentModel`; `HistoryCoverage.unit_id → ProductUnit`. BOM 위치·부품 연결 검증은 `model.py`, 구간/완전성 판정은 `temporal.py`의 공통 조회 |
| CQ07·08 보완·전후 비교 | 같은 query로 `snapshots`의 버전별 Installation/HistoryCoverage와 근거 비교. 초안·승인은 그래프 관계가 아닌 `drafts`, `review_events` 업무 기록. `preview_investigation`, `compare_investigation`, 호스트 `review/apply` |
| CQ10 사용처 | `ComponentModel ← BOMItem.component_id`, 그 항목의 `configuration_id → Configuration.revision_id → ProductRevision.model_id → ProductModel`. `component_usage`; 정확한 원본 범위 ID만 조회 |

원본 파일·고정 커밋·해시·검증 기록은 [manifest](../data/sources/manifest.json), BOM 행 위치는 catalog의 Evidence `locator`에 있다. L/L1 WROOM ODS 전체의 구조·수량·위치는 검증했고 PDF는 표제/U6 모듈 식별 대조까지만 했다. 미확인 실장, 개정 간 품목 동일성, PDF 전체 148행 대조의 남은 범위는 [PROJECT_STATUS](../PROJECT_STATUS.md#남은-제한결정)에 모은다.

Git 원본의 커밋, `ProductRevision.revision`의 설계 개정, 공통 응답의 `data_version`은 서로 다른 기준이다. 현재 `compare_configurations`는 등록된 구성의 차이를, `component_usage`는 정확한 원본 범위 부품 ID의 사용처를 조회한다. 원격 Git 이력 자동 수집·임의 과거 커밋 복원·개정 간 품목 동일성 판정은 구현된 도구로 제공하지 않는다. 설계와 운영 이력의 업무상 구분은 [PRD](SERVICE_SPEC.md#설계-이력과-운영-이력을-연결할-때)를 따른다.

### PostgreSQL 저장과 pgvector 검색

v0.2.0에서 도입한 PostgreSQL + pgvector 전용 기준은 v0.4.0에서도 유지한다. 후속 RDF/Neo4j 통합은 아래 별도 설계이며 현재 저장 경로나 API를 바꾸지 않는다. 그래프는 계약·현황 확장을 포함한 `graph-0.3`이고 시간 규칙 `temporal-0.1`과 O01~O08의 의미는 유지한다. 계약·현황 검증 규칙은 graph-0.3에서 관리하며 기존 장착 이력 시간 규칙을 바꾸지 않는다. 생성자는 `Service(database=None, *, principals, schema=None)`이며 기존 파일 경로/`:memory:` 입력은 거부한다. `DATABASE_URL`은 필수, `ONTOLOGICS_DB_SCHEMA` 기본값은 `ontologics`다. `.env`→`.env.local`→프로세스 환경 순서로 덮어쓴다. 접속 문자열은 로그·응답에 포함하지 않는다.

`database.py`가 pgvector extension과 전용 schema·테이블을 준비한다. 기존 서버에서는 관리자가 `CREATE EXTENSION vector WITH SCHEMA public`을 먼저 실행할 수 있다. 로컬 예제 역할은 개발용 DB 소유자이며 운영 최소권한 계정 분리는 미완료다. 테이블은 snapshots, drafts, requests, review_events, runs, runtime_runs, runtime_steps, evidence_vectors다. 모델 네트워크 호출 동안 트랜잭션을 열지 않는다. 동시에 실행하는 호스트 작업마다 별도 Service/연결을 사용한다.

도구 호출은 같은 DB/schema의 transaction advisory lock으로 직렬화한다. 조회 본문·반환 버전, 동시 반영의 기준 버전, 요청 키 재생을 한 경계에서 유지한다. 외부에서 연 트랜잭션 안의 Service 호출은 `TRANSACTION_BOUNDARY`로 거부하고 호출자 트랜잭션을 건드리지 않는다. SQL/잠금 제한은 30초/10초, 접속 제한은 10초다. DB 장애로 실패 기록도 저장하지 못하면 `AUDIT_NOT_RECORDED`와 `data_version=null`을 반환한다. DB 전체 장애·프로세스 강제 종료 시 감사 로그의 완전성을 보장하지 않는다.

`EvidenceIndex(service, actor, model=..., dimensions=...)`는 공통 호스트용 검색 접점이다.

| 호출 | 입력 | 출력·경계 |
| --- | --- | --- |
| `documents(evidence_ids, version=...)` | 기존 Evidence ID 목록, 데이터 버전 | 임베딩할 원문 기반 text·content_hash, 응답 data_version. read 권한 |
| `index(entries, version=..., request_key=...)` | 각 entry의 evidence_id/content_hash/embedding | 고정 모델·차원·버전별 불변 벡터 캐시. 별도 index 권한, 동일 요청 키 재생. 그래프 버전 변경 없음 |
| `search(embedding, version=..., origin=..., limit=5)` | 같은 모델의 질문 벡터, 데이터 버전·real/synthetic 필터 | 후보별 Evidence ID·원본·locator·사실·similarity. `candidate_only=true`, 완전 탐색 여부를 주장하지 않음 |

```python
index = EvidenceIndex(service, 'host-indexer', model='chosen-embedding-model', dimensions=1536)
# host-indexer: read/index; 신원은 호스트에서 주입
report = index.documents(['evidence:olimex-L-wroom:row-55'], version=3)
document = report['data']['documents'][0]
# embedding = 호스트의 임베딩 provider(document['text'])  # DB 트랜잭션 밖
# index.index([{'evidence_id': document['evidence_id'],
#   'content_hash': document['content_hash'], 'embedding': embedding}],
#   version=report['data_version'], request_key='index-row55-v3')
# candidates = index.search(question_embedding, version=3, origin='real')
```

실제 실행되는 수치 벡터 예제는 `examples/cq_check.py`에 있다. 반환 예: `data.candidates[0].evidence=["evidence:olimex-L-wroom:row-55"]`, `origin="real"`, `data.candidate_only=true`. 현재는 정확 cosine 검색이며 특정 임베딩 API·자연어 RAG 품질 평가는 미구현이다. 검색 결과는 장착·동일 부품·승인 사실을 확정하지 않는다. index/search 객체 자체나 DB를 LLM에 공개하지 말고, 후속 host retriever가 text→embedding→search를 감싸도록 한다.

### 후속 통합·배포 설계 (미구현)

아래는 [PRD의 별도 목표](SERVICE_SPEC.md#별도-통합-목표-a--rdfowlshaclsparql--neo4j)를 위한 설계 요구다. 현재 공통 API·DB 스키마·Agent 권한을 변경하거나 이미 제공되는 기능으로 설명하지 않는다. 통합 목표 A와 배포 목표 B는 서로 독립적으로 진행·검증하며 현재 1차 서비스의 선행 조건이 아니다.

**통합 목표 A의 데이터 흐름:** 기존 승인·반영 경로의 PostgreSQL 데이터 버전 → RDF/OWL 표현과 SHACL 검증 → SPARQL 조회·Neo4j 조회용 그래프 적재 → 동일 버전의 검증·동기화가 완료된 조회 대상만 공개한다. 스키마 매핑과 저장소별 완료 상태를 관리하는 구현은 후속 작업이다.

- 현재 `model.py`의 SCHEMA 기준과 각 표현의 매핑을 맞추고 공통 노드 ID, SourceDocument/Evidence, locator·hash·origin, 데이터·스키마·규칙 버전과 승인 이력을 보존한다. 설계 개정·초안 버전·데이터 버전을 혼동하지 않는다. SHACL은 구조·관계 검증을 추가하며 기존 시간·동일성·승인 규칙을 임의로 대체하지 않는다.
- Agent는 기존 공통 경로로 초안을 제출한다. 승인·반영된 버전만 동기화 대상으로 삼고, 각 조회에 필요한 저장소의 적재·검증 완료 버전을 확인한다. 미완료 적재·혼합 버전은 정상 결과로 공개하지 않고 해당 대상의 동기화 대기/실패를 알린다. 이전 완료 버전을 제공할 경우에도 실제 버전을 표시하고 요청 버전을 조용히 바꾸지 않는다.
- 동기화 기록에 대상 버전·원본 식별·내용 hash·완료/실패·재시도를 연결하고, 동일 버전 재시도의 중복·부분 적재·중단 후 재개를 검증한다. 현재 PostgreSQL 트랜잭션과 advisory lock은 RDF/Neo4j까지 원자 반영을 보장하지 않는다. 승인 성공과 각 조회 저장소의 공개 가능 상태는 구분한다.
- 동일한 CQ 입력·대상 집합·원본·승인된 데이터 버전·시간 규칙으로 PostgreSQL/SPARQL/Neo4j 결과를 대조한다. 관계·근거·실제/가상 구분과 A/B 경계·포함/제외/미확인·충돌 의미를 검증하며, CQ09 제안 및 CQ10 원본 범위 부분 지원을 임의로 확장하지 않는다.

**배포 목표 B의 구성별 책임:**

| 구성 | 설계·검증 책임 |
| --- | --- |
| CPU EC2 | 사용자 업무 인터페이스, 호스트 인증·권한, Agent·공통 도구 실행, 모델 API 연결, 실행 로그·장애 대응. 외부 모델 API를 사용하며 자체 GPU 서버는 기본 필수가 아님 |
| RDS PostgreSQL·pgvector | 그래프 스냅샷·초안·승인·실행 기록·벡터의 영속 저장, 확장·접속·권한 설정, 백업·복구 후 버전·근거 일관성 검증 |
| S3 | 무변경 원본과 원본 버전·hash·위치·라이선스 보존, 인증된 읽기·접근 제한, 복구 가능한 원본 보관 |
| 외부 LLM·임베딩 API | 기존 provider·retriever 접점 활용, 비밀값 관리, 모델·임베딩 차원·버전 기록, 실패·재시도와 실제 API 호출 검증 |
| RDF/SPARQL·Neo4j 병용 시 | 각 저장소의 배포 위치·접근 권한·백업/복원·동기화 실행 주체·공개 버전 책임을 별도로 명시. AWS 기본 서비스 배포의 필수 선행 구성은 아님 |

현재 SourceDocument 검증은 `model.py`에서 로컬 `data/` 파일을 읽어 SHA-256을 대조한다. S3 도입 시 원본 접근 어댑터와 검증 경로를 보완해 실제 객체 바이트의 hash와 원본 ID·버전·근거 위치를 확인해야 한다. 객체 누락·접근 실패·hash 불일치는 검증 실패로 처리하고, S3 URL이나 ETag만으로 기존 SHA-256 검증을 대신하지 않는다. 이 설계는 현재 `path` 계약을 즉시 바꾸는 것이 아니다.

AWS 완료 검증은 실제 환경의 S1/S2 전체 업무, 인증·권한, 영속성, 외부 API, 로그·장애 복구와 백업 복원을 포함한다. RDF/Neo4j를 병용하면 복구 이후에도 동일 승인 버전의 조회 공개 조건을 다시 검증한다. 엔진·배포 크기, 매핑·동기화 구현, 인증 수단과 백업 주기·복구 목표 등 세부 설정은 해당 구현에서 구체화한다.

## 공통 반환·오류

```json
{
  "ok": true,
  "data": {},
  "evidence": ["evidence:..."],
  "data_version": 3,
  "schema_version": "graph-0.3",
  "rule_version": "temporal-0.1",
  "contract_version": "0.4.0",
  "warnings": [],
  "conflicts": [],
  "errors": [],
  "run_id": "실행 식별자"
}
```

`ok=false`이면 `errors`의 code/message/details를 사용한다. `prepare_draft`가 성공해도 내용이 유효하지 않으면 `status=needs_changes`이고 `data.validation.valid=false`다. 초안을 보관했다는 성공과 등록 가능 판정을 혼동하지 않는다. 미확인 실장 상태는 원문 그대로 등록할 수 있으나 확정 실장·소요량으로 계산할 수 없다.

| 오류·상태 코드 | 처리 |
| --- | --- |
| INVALID_INPUT / INVALID_TOOL_ARGUMENTS / INVALID_VERSION | 입력 형태·시간·함수 계약 수정 |
| VALIDATION_FAILED | details의 근거·참조·필드 오류 보완 |
| FORBIDDEN / HUMAN_REVIEW_REQUIRED / TOOL_NOT_ALLOWED | 신뢰된 호스트의 권한 경로 사용. Agent가 재시도로 우회하지 않음 |
| SELF_APPROVAL_FORBIDDEN | 작성자와 다른 사람 검토자 필요 |
| APPROVAL_REQUIRED | 해당 초안 버전의 사람 승인 대기 |
| SUPERSEDED_DRAFT / DRAFT_CLOSED | 최신 초안 확인 또는 새 변경안 작성 |
| STALE_BASE | 최신 데이터에 대해 새 초안 버전을 만들고 재검토 |
| NO_CHANGES | 동일 내용이 이미 반영됨. 기존 자료 재사용 |
| IDEMPOTENCY_CONFLICT | 같은 키에 다른 내용을 보내지 않음. 변경한 요청에는 새 키 |
| INVALID_REQUEST_KEY | 모든 변경에 비어 있지 않은 요청 키 필요 |
| STORAGE_ERROR | 실행 실패·트랜잭션 롤백. 원인 해결 후 같은 내용·키로 재시도 |

성공 재시도에는 `IDEMPOTENT_REPLAY`, 이미 적용된 초안에는 `ALREADY_APPLIED` 경고가 붙는다. 과거 성공 결과를 재사용할 때 반환 데이터 버전도 그 성공 시점의 버전이다. 실패는 성공 중복 방지 캐시에 저장하지 않으며 `runs`에 남는다.

## B-CQ02 물량 현황 조회

`Service.fulfillment_status(actor, delivery_round_id, as_of, *, version=None)`의 공개 도구 인자는 `delivery_round_id`, `as_of`, `version` 모두 필수다. Python 기본값이 있더라도 버전 생략·불리언·음수는 `INVALID_VERSION`이다. 없는 회차는 `NOT_FOUND`, 잘못된 시각은 `INVALID_TIME`, 잘못된 그래프는 `VALIDATION_FAILED`다. 신뢰된 신원과 읽기 권한, 실행 감사·공통 응답 envelope를 재사용한다. 이 도구는 그래프나 초안·승인 버전을 변경하지 않는다.

`as_of`는 저장된 FulfillmentSnapshot의 정확한 현황 시점과 일치해야 한다. 같은 UTC 순간의 다른 offset은 일치하며 가장 최신 자료를 대신 고르거나 임의 시점 상태를 재구성하지 않는다. `queried_at`은 조회 실행 시각, `observed_at`은 원천 관측 시각, `as_of`는 현황 기준이다. 예정과 실제 시각을 구분한다. 이후 조회해도 원래 관측 시각을 현재 시각으로 덮어쓰지 않는다.

`graph-0.3`은 위 5개 현황 타입을 추가한다. 모든 자료는 synthetic 근거를 가지며 같은 snapshot·회사·구성·piece 단위로 연결한다. Allocation은 stock_id/production_work_id 중 정확히 하나만 가진다. 없는 선택 참조는 허용하되 null/잘못된 타입은 거부한다. round_ids는 중복 없는 비어 있지 않은 DeliveryRound 목록이며, 회사와 UTC 순간이 같은 snapshot은 하나다. JSON-LD/T-box는 선택 관계와 목록 관계도 ID 참조로 내보낸다.

현황 수량은 int만 허용하고 불리언·float·문자열은 거부한다. 재고·생산 완료 수량은 0 이상, 작업 목표·배정·운송은 양수다. 완료 수량은 작업 목표 이하이고 회차 배정 합계는 약속 이하, 모든 회차가 공유하는 원천별 배정 합계는 재고/작업 목표 이하다. **대상 회차를 걸러내기 전에 전체 원천 상한을 검사한다.** 원천과 배정의 구성·회사·단위가 일치해야 한다.

생산 상태는 in_progress/ready/waiting이며 in_progress에는 관측 시점 이전 또는 같은 실제 started_at이 필수다. 준비/대기에는 실제 시작이 없어야 한다. completed_quantity>0은 기록할 수 있지만 첫 조회 형식의 부분 완료/재고 전환이 미지원이므로 합계를 보류한다. Shipment는 재고 배정 전량에 대응하는 한 건만 지원한다. 실제 도착에는 실제 출발이 있어야 하고 출발≤도착≤현황 기준이며, 출발은 해당 재고 관측 이후 또는 같은 시각이어야 한다. 실제 미래 사건·역순·목적지 불일치·중복/부분 출고·생산 작업 직접 출고는 거부한다. 부분/취소/반품/재배정의 임의 보완은 하지 않는다.

확인 범위는 snapshot.confirmations의 알려진 키와 boolean 값으로 표현한다. 이 주장은 근거 원문을 사람이 검토해 승인하는 것이며 코드가 원문 의미의 진실성을 자동 판독하는 기능은 아니다.

| 확인 키 | 원문에서 확인할 내용 |
| --- | --- |
| allocations_complete | 조회 회차들의 배정 목록이 완전함 |
| source_allocations_complete | 공유 원천의 다른 회차 배정도 포함함 |
| allocations_disjoint | 배정 물량이 서로 겹치지 않음 |
| stock_events_complete | 관측 이후 해당 시점까지 재고 사건을 확인함 |
| stock_observations_before_departures | 과거 재고와 실제 출고의 시간 기준이 일치함 |
| shipment_events_complete | 출발·도착 사건의 확인 범위가 완전함 |
| production_state_complete | 미완성 작업의 상태를 확인함 |
| material_disjoint | 생산·완성 재고 물량이 서로 겹치지 않음 |
| no_reallocation | 첫 형식이 다루지 않는 재배정이 없음 |

누락/false는 유효한 미확인 자료로 저장할 수 있으나 최종 합계는 내지 않는다. 잘못된 키/값은 검증 실패다. 기존 장착 이력의 HistoryCoverage와 별개다. `data.status=complete`는 이 형식의 현황 확인이 끝났다는 뜻이며 납품 완료/미래 이행 가능성을 의미하지 않는다. `needs_confirmation`이면 `states`, `allocated_quantity`, `remaining_quantity`, `unallocated_quantity`는 null이고 `unknowns`로 필요한 확인을 반환한다. 확인 범위가 완전한 빈 배정 목록은 합계 0으로 구별한다. 원천/사건은 `items`·`stocks`, 같은 버전의 약속 사슬은 `promise`, 원문 추적은 `evidence`로 제공한다. 확인 부족 시 항목 상태도 classification_confirmed=false다.

완전한 현황의 `states`는 delivered/in_transit/warehouse/in_progress/ready/waiting이다. 실제 도착→납품 완료, 실제 출발만→운송 중, 아직 출발하지 않은 재고 배정→창고로 구분한다. 재고 관측량에서 실제 전량 출고를 빼므로 창고·운송을 중복 합산하지 않는다. 예정 도착이 지나도 실제 도착이 없으면 운송 중이며 `EXPECTED_ARRIVAL_ELAPSED` 경고를 준다. 잔여 약속은 약속−실제 납품 완료, 미배정은 약속−배정 총합이다. 모든 응답은 `fulfillment_assessed=false`·`FULFILLMENT_NOT_ASSESSED`로 예측 미수행을 표시한다.

| 대표 자료·실행 | 내용 |
| --- | --- |
| [원문](../data/sources/b_demo_fulfillment_01.md) | 고정된 가상 B-01 현황. 원본 바이트 무변경 보존 |
| [후보](../data/b_fulfillment_fixture.json) | 45개 신규 노드, 원문 SHA-256·23개 근거와 5개 확인 범위 근거 연결 |
| [독립 정답](../data/b_fulfillment_expected.json) | 완료100/운송150/창고250/생산300/준비100/대기100, 배정1000·잔여900·미배정0 |
| [대표 그래프](../data/b_fulfillment_graph.jsonld) | 현황의 참조·근거 의존성을 포함한 별도 JSON-LD. 기존 대표 그래프 보존 |
| [예제](../examples/b_fulfillment.py) | `python -m examples.b_fulfillment`: 승인 전후·같은 버전·근거·14개 검사. LLM 없음 |
| [순수 검사](../tests/test_fulfillment.py)·[DB 검사](../tests/test_fulfillment_service.py) | 27+8개. 전체 회차 초과·중복·미확인/0·시각/버전·승인 경계 |

공통 버전은 0.4.0/graph-0.3이며 temporal-0.1은 유지한다. 기존 모델 역할별 도구 목록은 유지하고 Task 09의 별도 현황 역할만 아래 두 도구를 사용한다. 새 원본을 바꿀 때는 새 SourceDocument/Evidence ID와 해시·기대 답을 검토해야 하며 기존 원문 ID의 바이트를 덮어쓰지 않는다.

## B-CQ02 Agent 연결과 평가

[b_fulfillment_agent.py](../examples/b_fulfillment_agent.py)는 기존 ModelRuntime/OpenAIProvider/AgentGateway를 재사용한다. 고정된 LLM 업무 흐름이며 새 프레임워크·라우터·서버는 없다. 입력은 질문·delivery_round_id·as_of·data_version이며 정답 수량이나 기대 답 파일을 모델에 전달하지 않는다. 전용 `fulfillment-agent` 신원은 read만 가지며 B_FULFILLMENT_TOOLS=(fulfillment_status,lookup)로 런타임의 도구 목록을 제한한다. 기존 B_INTAKE_TOOLS/B_PROMISE_TOOLS/PRODUCT_TOOLS/QUALITY_TOOLS는 바꾸지 않는다.

정상 흐름은 현황 조회→snapshot 근거 하나 조회→그 원본 조회→최종 답이다. snapshot이 없으면 근거 조회를 생략하고 미확인을 답한다. 최종 JSON의 정확한 키는 status, delivery_round_id, data_version, as_of, unit, states, allocated_quantity, remaining_quantity, unallocated_quantity, fulfillment_assessed, synthetic, evidence, unknowns, summary, next_action이다. 수량·시각·상태·unknowns는 도구 결과와 같아야 한다. 수량은 int 또는 미확인의 null, synthetic=true·fulfillment_assessed=false다.

Task08 현황 도구의 전체 evidence는 원천 상한·다른 회차 배정·계약/구성 등 검증 문맥을 보존한다. Task09 최종 답의 evidence는 이 전체 목록과 구분한다. snapshot이 있으면 `snapshot.evidence ∪ {item.evidence[0] for item in items}`와 정확히 같은 중복 없는 집합이어야 한다. 목록 순서는 무관하며 추가·누락·유효한 다른 근거로의 교체도 실패다. snapshot이 없는 미확인은 조회 envelope의 전체 evidence를 유지한다. 고정 정상 예제의11개는 결과 값이며 개수나 ID를 하드코딩하지 않는다.

역할 전용 연결은 마지막 모델 요청에만 실제 도구 결과에서 추린 정확한 인용 목록을 system 데이터로 추가한다. 그 전 history prefix와 provider call ID는 보존하고 마지막 단계의 도구 호출은 거부한다. 추가 모델 요청이나 자동 인용 치환은 없다. 모델의 원래 최종 text·기대 목록·수락 여부는 `final_submission`에 남기며 목록이 다르면 런타임이 실패한다. 독립 평가기에도 `exact_answer_evidence`를 추가해 같은 명세를 별도로 대조한다. 인용 정책은 `snapshot-and-item-first-exact-v1`, 연결 guard는 `cq02-provenance-v2`로 보고서에 기록한다. 공통 Service/runtime/provider·조회 응답·그래프 계약은 바꾸지 않는다.

모델이 개별 Evidence 전체를 모두 반복 조회하지 않도록 전체 Evidence→SourceDocument 사슬은 호스트가 같은 버전에서 로컬 검증하고 보고서에 보존한다. 실제 모델은 별도로 대표 snapshot 근거와 해당 SourceDocument를 읽어야 한다. 이를 도구 호출 기록으로 검사하며, SourceDocument 노드 조회를 원문 전체 열람으로 계산하지 않는다.

기본 실행은 scripted 대역이다. normal/missing/unknown_time/coverage_missing 사례는 실제 DB에서 재현하며, 실제 OpenAI 실행은 normal 하나만 지원한다. 유료 실행 제한은 모델 snapshot `gpt-5.4-mini-2026-03-17`, 최대 요청4회, 자동 재시도 없음, 한 응답당 도구1개, 출력1024토큰, reasoning none이다. 이전 출력 경로가 존재하면 키/DB/API 실행 전에 중단한다. 비용 추정은 실행 전 입력 크기와 공식 요금으로 알리고, 실행 후 각 요청 usage를 합산한다. 검증에서 거부한 응답도 사용량을 보존하며 사용량이 누락된 요청이 있으면 usage_incomplete=true·비용 null이다. 토큰 기반 표준 요금 추정이며 청구서 확인은 아니다.

평가는 독립 정답과 같은 버전의 도구 결과, 실제 호출 인자/근거 조회, null·정수 형식, 다른 회차 제외, 확정 버전 불변과 권한을 검사한다. 자유문 summary/next_action의 의미는 별도 검토 대상이며 모델을 평가자로 다시 호출하지 않는다. 미확인 자료를 0으로, 잔여 약속을 추가 생산 필요량으로, 현황을 기한 내 납품 보장으로 설명하지 않아야 한다. [대역·적대적 검사](../tests/test_b_fulfillment_agent.py)와 [provider 비용 회귀](../tests/test_openai_provider.py)를 따른다. 범용 해석·실제 사람 승인 통합·반복 신뢰도·생산 예측은 이 연결의 성공 범위가 아니다.

## Agent에 공개하는 호출

호스트가 `gateway = AgentGateway(service, 'product-agent')`를 한 번 만든다. Agent는 `gateway.call(tool, arguments)`만 호출하고 `actor`를 전달하지 않는다. 아래 version은 조회 데이터 버전, 초안 관련 version은 초안 버전이다.

| tool | arguments | 결과 |
| --- | --- | --- |
| lookup | node_id?, kind?, version? | 원본·근거·품목·로트 등 노드 목록 |
| configuration | configuration_id, version? | 모델·개정·구성·BOM·부품 |
| delivery_round | delivery_round_id, version(필수) | 같은 버전의 회차→항목→계약/구성/개정/모델과 근거. 납품 약속 조회만 수행 |
| fulfillment_status | delivery_round_id, as_of, version(모두 필수) | 정확한 현황 시점·같은 버전의 6상태 물량·배정/잔여/미배정·근거·미확인. 예측 없음 |
| compare_configurations | left_id, right_id, version? 또는 draft_id/draft_version | 위치별 원문·패키지·실장 표기 차이. 미반영 등록안도 기존 구성과 비교 가능하며 is_preview 표시. 전기적 동일성 판정 아님 |
| component_usage | component_id, version? | 정확한 부품 ID의 BOM 사용처. 다른 원본의 유사 표기를 병합하지 않음 |
| investigate | query, version? | A/B의 제품별 판정·이유·근거·미해결 구간·이력 완전성 |
| compare_investigation | query, before_version, after_version | 같은 query와 규칙으로 과거·새 버전 결과 비교 |
| prepare_draft | operations, request_key, draft_id?, expected_version? | 초안 ID·불변 버전·hash·기준 버전·변경 전후·검증 |
| get_draft | draft_id, version | 초안 내용·상태·검토 이력. 과거 버전도 열람 가능 |
| validate_draft | draft_id, version | 검증·현재 기준과의 충돌·변경점 |
| preview_investigation | draft_id, version, query | is_preview=true, 미반영 초안의 예상 결과. 확정 데이터는 바꾸지 않음 |
| export_graph | version? | JSON-LD 그래프 |

### B-CQ01 계약 접수와 약속 조회

호스트용 `contracts.extract_contract(text, configurations, *, evidence_ids)`는 한 개의 고정 형식 Markdown 계약 표를 읽는다. configurations에는 같은 승인 스냅샷의 모델·개정·구성 노드를 전달한다. 정확한 모델/개정/variant 하나에 연결하며 유사 이름으로 대체하지 않는다. evidence_ids는 호스트가 준비한 계약/항목/회차 Evidence ID다. 파일 시스템·DB 쓰기·승인을 수행하지 않으며 Agent 공개 도구도 아니다.

결과는 `status/nodes/issues`다. `ready`만 신규 3개 노드 후보를 제공한다. `needs_confirmation`(필수 정보 누락·제품/기한 모호·원문 충돌·첫 범위 밖)과 `invalid`(수량/단위/표 형식 오류 등)는 `nodes=[]`이고 각 issue에 field/code/message/locator를 제공한다. 후보가 준비돼도 공통 그래프 검증·초안·사람 검토·반영이 필요하다. 첫 표 형식은 항목 1·회차 1·수량 전량을 지원한다. 공통 그래프 검증은 여러 회차의 합계 상한도 검사하지만 이 파서가 다회차 계약을 지원한다는 뜻은 아니다.

`delivery_round`는 read 권한과 명시적인 nonnegative integer 버전을 요구한다(불리언 제외). 없는 버전은 `VERSION_NOT_FOUND`, 없는/다른 타입의 회차 ID는 `NOT_FOUND`, 잘못된 ID 형식은 `INVALID_INPUT`, 누락/잘못된 버전은 `INVALID_VERSION`이다. 다른 회차·최신 버전으로 자동 대체하지 않는다. 실패 응답의 data_version은 기존 공통 계약에 따라 실패 기록 시 현재 버전일 수 있으며 요청 버전의 조회 성공을 뜻하지 않는다.

성공 `data`는 `contract`, `contract_item`, `delivery_round`, `configuration`, `revision`, `product`, `fulfillment_assessed=false`다. 각 노드의 evidence를 공통 envelope가 수집하며 계약 근거와 실제 설계 근거를 분리해서 조회할 수 있다. `FULFILLMENT_NOT_ASSESSED` 경고는 재고·생산·운송 판단을 아직 하지 않았다는 뜻이다. 운영 관측 시각·예상 완료/도착 시각은 반환하지 않는다.

공유 원문은 [b_demo_contract_01.md](../data/sources/b_demo_contract_01.md), 해시·SourceDocument/Evidence는 [b_contract_fixture.json](../data/b_contract_fixture.json), 독립 정답은 [b_contract_expected.json](../data/b_contract_expected.json)이다. `load_demo_contract()`는 고정 해시를 검증하고 후보 7개를 만든다. 기존 OLIMEX manifest/원본은 유지하며 새 원문 내용은 새 SourceDocument/Evidence 버전으로 관리한다. [B 대표 JSON-LD](../data/b_contract_graph.jsonld)는 새 계약과 필요한 실제 설계 근거의 최소 부분 그래프이며 기존 전체 이력 그래프와 별개다.

실행은 `python -m examples.b_contract`다. 개발용 구성 시드 버전 1 → 계약 초안/검증 → 승인 전 반영 차단 → 개발용 사람 역할 검토 → 버전 2 반영 → 같은 버전 약속/근거 조회를 확인한다. 이 예제는 공통 Gateway를 직접 호출하며 실제 모델을 호출하지 않는다. 기존 PRODUCT_TOOLS/QUALITY_TOOLS 목록은 유지한다.

### B Agent 연결 예제 (Task 09)

`examples/b_agent.py`는 B 역할 하나의 두 모드를 기존 `ModelRuntime`·`OpenAIProvider`에 연결한다. 새 공개 Service 함수·그래프 타입·별도 라우터는 추가하지 않는다. provider와 runtime에 동일한 도구 목록을 지정한다.

| 모드 | 호스트 입력·시드 | 모델 도구 | 최종 결과 |
| --- | --- | --- | --- |
| intake | source_text 원문, 등록된 SourceDocument ID/hash, 3개 Evidence ID, data_version, request_key, 노드 필드 계약. 확정 계약은 없는 개발 시드 | B_INTAKE_TOOLS: lookup, prepare_draft, get_draft, validate_draft | status, draft_id/draft_version(또는 null), data_version, evidence, unknowns(field/code), needs_human_review=true, synthetic_contract=true, summary, next_action |
| promise | delivery_round_id, data_version, not_assessed. 모델 시작 전에 기존 승인 경로로 준비한 개발용 약속 자료 | B_PROMISE_TOOLS: lookup, delivery_round | status=promise_only, data_version, promise(기존 예제 promise_values 필드), evidence, unknowns, synthetic_contract=true, summary, next_action |

intake는 추출 정답 노드를 모델에 주지 않고 원문을 제공한다. 공통 모델·개정·구성 lookup으로 정확한 기존 구성을 연결하고 새 계약 3노드만 미승인 초안화한다. 사람 검토 전 확정 버전은 1이다. 누락·모호함·충돌이면 초안 없이 확인 필요다. `extract_contract`는 호스트 평가의 결정적 기준이며 모델 공개 도구가 아니다. 이 첫 입력 범위는 고정 Markdown 표이고 임의 PDF/OCR 또는 여러 항목·회차의 계약을 지원한다는 뜻은 아니다.

promise는 버전 2 약속에서 고객·구성·계약/회차 수량·단위·도착 기한·장소를 읽고 근거 및 SourceDocument를 조회한다. `fulfillment_assessed=false`, 미확인 inventory/allocation/production/transport/actual_arrival을 유지한다. 운영 관측 시각은 없고 due_at은 약속 기한이다. 두 모드는 독립 DB 스키마를 쓰며 모델의 intake 초안을 자동 승인해서 promise로 넘기지 않는다. 예제의 scripted reviewer는 실제 업무 승인이 아니다.

`ModelRuntime(..., allowed_tools=도구목록)`는 호스트가 지정한 고유 list/tuple을 고정한다. 전체 호출 묶음을 실행 전에 검사하고 역할 밖 도구는 `TOOL_NOT_ALLOWED`로 중단한다. 생략 시 기존 Gateway 전체 허용 도구를 사용해 기존 호출자를 보존한다. 모드별 권한 목록을 실행 설정에도 기록한다. Agent 신원 변경·review/apply는 계속 금지한다. ModelConfig는 B 예제에서 max_steps=16, 기본 step당 max_tool_calls=4, max_attempts=2 및 기존 byte 제한을 사용한다. API 시간/토큰 한도는 기존 provider 설정이며 총 실행 시간/비용 강제 상한은 없다.

자동 평가는 성공 trace·저장된 초안·원문 해시·정확한 버전과 구성·독립 정답을 대조한다. `runtime.ok`는 최종 텍스트 생성일 뿐 업무 평가 통과를 뜻하지 않는다. 잘못된 JSON/근거/수량/기한/승인 상태는 평가 실패다. 자유문 전체의 의미 검토는 별도로 남기며 도구 경로의 대역 성공을 실제 모델 해석 품질로 계산하지 않는다. `--provider openai` 설정 실패는 DB 시드·API 호출 전에 구조화한 실패 보고서를 남긴다. 실행 로그는 Git 제외 `artifacts/local/task09/`에 둔다.

### 기존 A/B 조사와 공통 변경 초안

A query: `{mode:'A', lot_id, unit_ids:[...], at, exclude_unit_id?}`.
B query: `{mode:'B', lot_id, unit_ids:[...], start, end, period_meaning:'installation_overlap', exclude_unit_id?}`. period_meaning은 생략 가능하나 다른 의미는 거부한다. 대상 집합은 필수이며 제외할 기준 제품은 그 집합에 포함시킨 후 제외 ID로 지정한다.

제품별 `status`는 `included`, `excluded`, `needs_confirmation`이다. `reasons`, `evidence`, `matched_intervals`, `unresolved_intervals`, `coverage_complete`를 함께 해석한다. 알려진 일치가 하나라도 있으면 포함될 수 있지만 다른 미확인 구간도 숨기지 않는다. 입증된 슬롯 충돌은 관련 설치 기록을 보수적으로 미확정 처리한다. 충돌 구간만 잘라 확정하는 고급 처리는 없다.

등록·정정은 `operations=[{'op':'put','node':{...}}]`다. 새 ID는 생성, 기존 ID는 해당 사실의 새 데이터 버전이다. 삭제는 제공하지 않는다. 소스·근거 노드는 같은 ID로 덮어쓸 수 없으며 수정 근거는 새 ID로 만든다. 등록·보완하는 노드들 사이의 참조를 한 묶음으로 검증한다.

## 사람 검토·반영 접점

호스트가 인증된 사용자로 호출한다. 아래 호출은 Agent 도구 목록에 포함하지 않는다.

```python
service.review('reviewer', draft_id, draft_version,
    decision='request_changes', note='제거 이후 재장착 여부 확인 필요',
    request_key='review-request-001')
# 작성자는 새 operations로 prepare_draft(..., draft_id=..., expected_version=...) 호출
service.review('reviewer', draft_id, revised_version,
    decision='approve', note='근거와 변경 전후 값 확인', request_key='review-002')
service.apply('workflow', draft_id, revised_version, request_key='apply-002')
```

검토 decision은 approve/request_changes/reject다. 작성자와 승인자가 달라야 하고 승인자는 `kind=human`, `roles`에 review가 있어야 한다. `apply`는 별도 apply 권한이며 승인된 버전·hash·기준 버전·현재 검증을 다시 검사한다. 의존 변경과 스냅샷·승인 상태·중복 방지 결과는 한 PostgreSQL 트랜잭션으로 반영한다. 중간 실패는 전부 롤백한다.

초안 상태는 awaiting_review / needs_changes / approved / applied / superseded / rejected다. 실행 상태 succeeded/failed는 별도 `runs`에 기록한다. `review_events`는 검토 결정을 추가 기록하며 새 검토나 초안 수정으로 이전 의견을 지우지 않는다. `run_history`는 audit 권한이 있는 호스트만 사용한다.

UI는 초안 버전·변경 전후·근거 위치·검증·미확인과 승인/보완/거절 동작을 연결하면 된다. 사용자가 보고 승인한 정확한 버전을 호출해야 한다. 로그인·세션·CSRF·프로세스 격리는 실제 UI/API 통합 단계의 과제다. 예제 principals를 네트워크 API 입력으로 받으면 안 된다. 로컬 Python 객체나 DB 파일을 가진 개발자를 격리하는 권한 모델은 아니다.

`bootstrap`은 seed 권한과 비어 있는 DB가 필요한 개발 전용 적재다. 운영 승인 흐름이나 Agent 도구로 사용하지 않는다.

## 모델 호출 접점

`runtime.py`의 `ModelRuntime`과 기존 gateway·PostgreSQL 실행 기록을 재사용한다. `openai_provider.py`는 OpenAI Responses API 고정 endpoint와 `gpt-5.4-mini-2026-03-17`만 사용한다. `parallel_tool_calls=false`로 순차 도구 실행을 요청한다. 원래 API `call_id`와 출력 항목(있는 경우 암호화된 reasoning 상태)을 보존해 후속 tool 결과를 연결한다. 도구 스키마는 `tool_specs.py`에 있으며 optional·이종 노드 필드를 위해 `strict=false`로 전달하고 공통 코드가 실제 입력을 검증한다. [공식 function calling 계약](https://developers.openai.com/api/docs/guides/function-calling)을 따른다.

`OpenAISettings.from_environment(환경파일)` → `OpenAIProvider(settings, PRODUCT_TOOLS 또는 QUALITY_TOOLS)` → `ModelRuntime(service, 호스트가 정한 신원, provider, config=ModelConfig(model=settings.model, max_steps=12))` 순서다. 실행마다 새 provider를 만든다. 예제 실행·모델·DB 환경변수·설정 우선순위는 [README](../README.md#실제-llm-실행과-동료-개발-시작)에 있다. 기본 timeout 30초, 응답 한도 4096토큰이며 설정 가능 범위는 각각 1~60초, 256~8192토큰이다. 실행 전체 wall-clock 제한이나 강제 종료 복구는 없다.

Product에는 lookup/configuration/compare_configurations/prepare_draft/get_draft/validate_draft, Quality에는 lookup/component_usage/investigate/compare_investigation만 노출한다. 승인·반영·bootstrap·신원 선택은 두 목록 모두에 없다. 문서 안 지시문을 데이터로 처리하라는 역할 지침과 별개로, 실제 권한은 공통 코드가 강제한다.

`runtime_runs`, `runtime_steps`에는 공개 모델 설정·해시·상태·공통 도구 run ID가 기록된다. provider의 `calls`에는 HTTP 상태·요청/응답 ID·토큰·경과 시간이 있으며 키·HTTP 오류 본문은 없다. 기본 런타임은 원문 trace를 저장하지 않는다. 예제만 `capture_tool_trace=True`로 로컬 보고서에 도구 입력/출력·최종 답을 보관하므로 다른 업무 자료에 적용할 때 공유 전에 내용을 검토한다.

| provider 오류 | 처리 |
| --- | --- |
| OPENAI_KEY_MISSING / OPENAI_CONFIG_INVALID / OPENAI_MODEL_UNSUPPORTED | 로컬 설정 수정. 키 값은 로그에 넣지 않음 |
| OPENAI_AUTH_FAILED / OPENAI_ACCESS_DENIED / OPENAI_MODEL_UNAVAILABLE | 계정 키·접근 확인 후 별도 실행. 자동 반복 안 함 |
| OPENAI_RESPONSE_INCOMPLETE / OPENAI_RESPONSE_INVALID / OPENAI_ARGUMENTS_INVALID | 불완전·잘못된 응답의 부분 tool call을 실행하지 않고 종료 |
| OPENAI_MODEL_MISMATCH / TOOL_NOT_ALLOWED / OPENAI_HISTORY_INVALID | 모델·역할·호출 연결 위반을 종료 |
| OPENAI_TIMEOUT / OPENAI_CONNECTION_FAILED / OPENAI_TRANSIENT_HTTP | provider 기록에 상세 코드, 런타임에서 최대 2회 시도 후 TRANSIENT_MODEL_ERROR. 초안 쓰기는 기존 요청 키 중복 방지 유지 |

## 실모델 예제의 입출력

`python3 -m examples.llm_agents --role both`는 다음 두 사례만 실행한다. 실제 실행의 전체 모델 도구 arguments와 최종 JSON은 [검증 요약](../artifacts/llm_verification.json), 모든 로컬 원문 출력은 해당 실행의 ignored `report.json`에 있다. 아래 응답은 핵심 필드만 발췌했다.

Product는 ODS에서 이미 파싱한 U4(L1 54행)·R50(L1 69행)을 입력으로 받는다. `prepare_draft`에는 두 개의 `put`을 묶어 전달한다. R50 항목은 다음 형태이며 수량·위치·ID도 공통 파서 값에서 온다.

```json
{"op":"put","node":{"id":"bom:esp32-poe:L1:WROOM:row-69","type":"BOMItem","origin":"real","evidence":["evidence:olimex-L1-wroom:row-69"],"configuration_id":"config:esp32-poe:L1:WROOM","component_id":"component:olimex-L1-wroom:03aec1d5d26a4854","quantity":1,"unit":"piece","positions":["R50"],"population":"unknown","raw_value":"NA(130k/R0402)"}}
```

응답의 `data.draft_id`, `data.draft_version`을 `validate_draft`의 `draft_id`, `version`으로 전달한다. 기대 값은 `status=awaiting_review`, `validation.valid=true`, 확정 `data_version=1` 유지다. 최종 설명은 실장 미확인·근거 ID·사람 검토 필요를 포함한다. 신규 전체 BOM 추출·등록 완료를 뜻하지 않는다.

Quality의 B 입력은 다음과 같다. A는 같은 로트·대상·버전과 `query={mode:"A", lot_id, unit_ids, at:"2026-09-20T10:00:00+09:00"}`를 쓴다.

```json
{"query":{"mode":"B","lot_id":"lot:demo:lan8710:L07","unit_ids":["unit:U104"],"start":"2026-09-01T00:00:00+09:00","end":"2026-09-21T00:00:00+09:00","period_meaning":"installation_overlap"},"before_version":2,"after_version":3}
```

`compare_investigation`의 `data.before.results[0].status`는 A/B 모두 `needs_confirmation`, `data.after.results[0].status`는 A=`excluded`, B=`included`다. 두 결과의 가상 운영 근거는 `evidence:demo:initial`과 `evidence:demo:supplement`, 실제 설계 근거는 `component_usage`의 `evidence:olimex-L-wroom:row-55`다. 호스트의 `evidence_details`가 각 Evidence를 SourceDocument의 URL·path·sha256·version과 locator로 연결한다. 실제 Quality 응답은 근거 ID를 인용했지만 Evidence를 개별 조회하지 않았으므로 원본 문서 직접 열람 성공으로 계산하지 않는다. UI 연결 시에는 문장 속 날짜 축약 대신 보고서 task/tool_trace의 정확한 경계와 offset을 사용자 화면에 표시해야 한다.

`evaluation.automatic_checks`는 해당 입력·버전·근거·구조화 답·미확인 상태·불변성을 검사한다. `narrative_review`는 별도 검토 전에는 `not_yet_reviewed`이며 자동 성공과 다르다. 실제 2개 답의 자유문 검토 결과는 공유용 검증 요약에만 기록했다. 테스트 대역·실제 API 통신·좁은 사례 품질 검토·미수행한 범용 품질 평가를 구별한다.

## 계약 변경

새 원본·변형은 manifest와 parser 매핑, 근거, CQ 기대 결과를 함께 추가한다. 원본 간 같은 부품을 합치려면 검토된 대응표와 ID 이전 영향을 먼저 설계한다. 시간 의미·제품 단위·승인 권한·공통 schema 변경은 명시적 버전 변경과 회귀 검증 대상이다. 공통 도구에 필요한 기능부터 추가하며 별도 Agent·서버·Skill 생성을 전제하지 않는다.
