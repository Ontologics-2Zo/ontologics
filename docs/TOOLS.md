# 공통 도구 계약 0.3.0

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

실제 설계 다섯 타입은 `real`, 운영 다섯 타입은 현재 기준 데이터 정책에 따라 `synthetic`만 허용한다. 제품 소속·로트 부품·장착 위치가 실장 확인된 BOM과 맞아야 한다. 추가 필드로 검사·고장 원인·가동 정보를 끼워 넣을 수 없다. 새 종류의 사양은 공통 계약의 명시적 확장으로 추가한다.

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

사용자 요청으로 v0.2.0의 현재 구현과 1차 서비스는 PostgreSQL + pgvector만 사용한다. 후속 RDF/Neo4j 통합은 아래 별도 설계이며 현재 저장 경로나 API를 바꾸지 않는다. 그래프 `graph-0.1`과 시간 `temporal-0.1`, O01~O08의 의미는 유지한다. 생성자는 `Service(database=None, *, principals, schema=None)`이며 기존 파일 경로/`:memory:` 입력은 거부한다. `DATABASE_URL`은 필수, `ONTOLOGICS_DB_SCHEMA` 기본값은 `ontologics`다. `.env`→`.env.local`→프로세스 환경 순서로 덮어쓴다. 접속 문자열은 로그·응답에 포함하지 않는다.

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

실제 실행되는 수치 벡터 예제는 `examples/cq_check.py`에 있다. 반환 예: `data.candidates[0].evidence=["evidence:olimex-L-wroom:row-55"]`, `origin="real"`, `data.candidate_only=true`. 정확 cosine 검색이며 `embeddings.py`의 고정 text-embedding-3-small/1536과 `Service.semantic_search`가 자연어 질문을 연결한다. 검색 결과는 장착·동일 부품·승인 사실을 확정하지 않는다. index/search 객체·DB·색인 권한은 LLM에 공개하지 않는다. 실제 모델 사례 평가는 PROJECT_STATUS의 범위만 완료이며 범용 RAG 품질 평가와 구별한다.

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
  "schema_version": "graph-0.1",
  "rule_version": "temporal-0.1",
  "contract_version": "0.3.0",
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

## Agent에 공개하는 호출

호스트가 `gateway = AgentGateway(service, 'product-agent')`를 한 번 만든다. Agent는 `gateway.call(tool, arguments)`만 호출하고 `actor`를 전달하지 않는다. 아래 version은 조회 데이터 버전, 초안 관련 version은 초안 버전이다.

| tool | arguments | 결과 |
| --- | --- | --- |
| lookup | node_id?, kind?, version? | 원본·근거·품목·로트 등 노드 목록 |
| configuration | configuration_id, version? | 모델·개정·구성·BOM·부품 |
| compare_configurations | left_id, right_id, version? 또는 draft_id/draft_version | 위치별 원문·패키지·실장 표기 차이. 미반영 등록안도 기존 구성과 비교 가능하며 is_preview 표시. 전기적 동일성 판정 아님 |
| component_usage | component_id, version? | 정확한 부품 ID의 BOM 사용처. 다른 원본의 유사 표기를 병합하지 않음 |
| investigate | query, version? | A/B의 제품별 판정·이유·근거·미해결 구간·이력 완전성 |
| compare_investigation | query, before_version, after_version | 같은 query와 규칙으로 과거·새 버전 결과 비교 |
| prepare_draft | operations, request_key, draft_id?, expected_version? | 초안 ID·불변 버전·hash·기준 버전·변경 전후·검증 |
| get_draft | draft_id, version | 초안 내용·상태·검토 이력. 과거 버전도 열람 가능 |
| validate_draft | draft_id, version | 검증·현재 기준과의 충돌·변경점 |
| preview_investigation | draft_id, version, query | is_preview=true, 미반영 초안의 예상 결과. 확정 데이터는 바꾸지 않음 |
| export_graph | version? | JSON-LD 그래프 |
| get_source_receipt | receipt_id | 요청자 소유 접수 후보·원본·Evidence/locator. operations 쓰기 입력은 노출하지 않음 |
| prepare_source_draft | receipt_id, request_key, draft_id?, expected_version? | 기존 source_draft 경로와 동일 후보·작성자·검증·멱등 키. 승인/반영 아님 |
| search_evidence | query, origin=all/real/synthetic, version? | 승인 그래프의 문자열 후보 최대60건. matches/total/limit/mode=lexical/candidate_only |
| semantic_search | query, origin=all/real/synthetic, version? | 질문 임베딩→같은 모델·정확 버전 pgvector 후보 최대5건. candidates/source/locator/facts/content_hash/index_coverage, mode=semantic/candidate_only |

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

UI는 초안 버전·변경 전후·근거 위치·검증·미확인과 승인/보완/거절 동작을 연결한다. 사용자가 보고 승인한 정확한 버전을 호출해야 한다. 아래 개발 HTTP 호스트는 고정 개발 신원과 Origin/CSRF 방어를 연결했으며 실제 로그인·인증 세션·운영 프로세스 격리는 미완료다. 예제 principals를 네트워크 API 입력으로 받으면 안 된다. 로컬 Python 객체나 DB를 가진 개발자를 격리하는 권한 모델은 아니다.

`bootstrap`은 seed 권한과 비어 있는 DB가 필요한 개발 전용 적재다. 운영 승인 흐름이나 Agent 도구로 사용하지 않는다.

## 개발용 사용자 호스트

`ontologics/host.py`의 `CommonHost`는 기존 Service·AgentGateway·ModelRuntime을 연결하는 내부 함수 경로다. 이 모듈 자체에는 HTTP·로그인·새 DB 테이블이 없으며 아래 `web.py`가 HTTP 경계를 제공한다. `ontologics/identity.py`의 `DevelopmentIdentityProvider(..., enabled=True)`에 호스트가 등록한 개발 사용자만 `DevelopmentContext(subject)`로 선택한다. 개발 모드는 명시적으로 켜야 하며 환경변수·도구 인자에서 자동으로 사용자나 권한을 받지 않는다.

```python
from ontologics.host import CommonHost
from ontologics.identity import DevelopmentContext, DevelopmentIdentityProvider, UserIdentity

identities = DevelopmentIdentityProvider([
    UserIdentity('dev-alice', frozenset({'read', 'draft', 'review'})),
    UserIdentity('dev-bob', frozenset({'read', 'review'})),
], enabled=True)
host = CommonHost(identities)  # 기존 DATABASE_URL / ONTOLOGICS_DB_SCHEMA
with host.request(DevelopmentContext('dev-alice')) as request:
    result = request.agent('product').call('prepare_draft', {
        'operations': operations,  # 기존 원본·파서에서 준비한 put 묶음
        'request_key': 'registration-001',
    })
    context = request.context  # subject, actor=user:dev-alice, identity_mode=development, permissions
# result가 ok이고 validation.valid인 초안의 ID/버전을 사람 검토 화면으로 전달한다.
```

전체 실행은 [examples/host_workflow.py](../examples/host_workflow.py)와 README의 `python3 -m examples.host_workflow`를 사용한다. 개발 사용자에게 draft·review 권한이 모두 있어도 요청 사용자가 작성한 초안의 자기 승인은 공통 `SELF_APPROVAL_FORBIDDEN`으로 차단한다. 작성자와 검토자는 표시 이름·Agent 역할명이 아닌 안정적인 `user:<subject>`로 기록한다.

| 호스트 접점 | 권한·결과 |
| --- | --- |
| `host.request(context)` | 공급자의 신원을 한 번 고정하고 해당 요청 전용 Service/DB 연결을 열고 닫음. 이후 다른 사용자 선택이 기존 요청을 바꾸지 않음 |
| `request.call(tool, arguments)` | 사람 요청의 조회·초안 경로. 기존 AgentGateway 공개 도구만 허용하며 신원·승인·반영·seed 입력 불가 |
| `request.agent(role).call(...)` / `request.run_agent(role, provider, messages, config=...)` | Product/Quality/Knowledge/Supervisor 또는 호스트에 등록된 확장 역할의 도구 목록으로 추가 제한. 사용자의 review·draft 권한이 있어도 Agent 승인이나 Quality 쓰기를 허용하지 않음 |
| `request.review(draft_id, version, decision=..., note=..., request_key=...)` | 고정된 요청 사용자의 명시적 호스트 동작. 기존 자기 승인·권한·버전·검증 규칙 재사용 |
| `host.apply_approved(draft_id, version, request_key=...)` | Agent에 공개하지 않는 서버 워크플로. 저장된 승인자의 현재 권한을 공급자에서 다시 조회한 뒤 `system:host-workflow`로 기존 apply 호출. 최신 초안으로 자동 치환하지 않음 |

도구 반환·오류·`request_key` 의미는 그대로다. 호스트에서 자동 키를 새로 만들지 않는다. 같은 사람·키·내용은 이전 결과를 재사용하며 다른 사람의 같은 키는 독립적이다. 반영 키는 워크플로 신원에서 공유하므로 초안·버전별 업무 키를 사용한다. 승인 이전 실패는 성공 캐시에 남지 않아 같은 내용·키로 재시도할 수 있다. 이미 성공한 요청의 재생은 그때의 결과이며 새로운 승인·반영을 뜻하지 않는다.

`runs`·`drafts.author`·`review_events`·`runtime_runs.actor`에는 요청 사용자 ID가 남고 반영 실행은 워크플로 ID로 남는다. `AgentGateway`와 `ModelRuntime`의 선택적 `allowed_tools`는 기존 목록의 부분집합만 허용하며 런타임 설정에도 기록한다. 도구 인자의 `actor`·`principals` 및 계약에 없는 작성자·검토자 필드는 거부한다. 사용자 신원에는 read/draft/review/audit만 설정할 수 있고 apply/seed/index는 이 호스트의 사용자 권한으로 부여하지 않는다.

인증 교체 시 `IdentityProvider.resolve(context)`는 검증된 인증 문맥을 안정적인 내부 subject·권한으로 변환하고, `lookup(subject)`는 기존 승인자의 현재 권한을 반환해야 한다. 같은 사람에게 요청마다 새 subject를 발급하면 자기 승인·초안 소유권 검사가 깨지므로 고정 대응을 유지한다. 알 수 없는 개발 신원·비활성 모드·잘못된 문맥은 DB 요청 생성 전에 `IdentityError`로 거부하며 아직 인증 감사 로그는 제공하지 않는다. 승인자 삭제·권한 철회는 기존 apply 권한 검사로 차단하고 공급자 장애는 `IDENTITY_PROVIDER_UNAVAILABLE`로 실행 실패를 남긴다. 조회한 승인자 신원은 해당 반영 요청 시점의 권한 스냅샷이다.

이 경로는 **개발용 신원·권한 검증**이다. 프로세스의 Python 객체나 개발 사용자 선택 권한을 가진 사람을 격리하지 않으며, 실제 로그인·인증 세션·외부 인증 공급자 검증은 미완료다. 개발 공급자를 운영 인증 실패 시 fallback으로 사용하지 않는다. 기존 호스트 시험은 scripted provider로 수행했으며 아래 웹의 실모델 검증과 구별한다. 당시 호스트 검증에는 Supervisor/Knowledge 실모델 실행이 포함되지 않았다. 이번 화면 연동 범위는 아래 HTTP 절을 따르며 실제 인증·전체 대화 품질은 잔여 작업이다.


확장 역할은 `AgentRegistration(name, instructions, allowed_tools)`를 서버 시작 코드의 `CommonHost(..., agent_registrations=(...))`에만 등록한다. 내장 이름 덮어쓰기·중복·미등록 도구·Agent 금지 도구는 거부한다. 등록 지침은 system 메시지에 연결되고 요청 사용자의 권한을 추가하지 않는다. 읽기 계약 예제는 [examples/extension_agent.py](../examples/extension_agent.py), 경계 검증은 `tests/test_agent_extensions.py`다. 역할 등록과 웹 Supervisor 라우팅 연결은 별도이며 자동 라우팅은 하지 않는다.

## 개발용 HTTP API와 화면

[web.py](../ontologics/web.py)가 CommonHost를 감싸고 [static](../ontologics/static/index.html)의 HTML/CSS/JavaScript를 같은 origin에서 제공한다. Python 표준 라이브러리 HTTP 서버를 사용한 **로컬 개발 통합**이다. v0.3.0은 기존 입력과 호환되는 접수 후보·문자열/의미 검색 도구 추가이며 그래프·시간 규칙·DB DDL은 그대로다. `web-0.3`은 개발 사용자 선택 및 명시적 AI 초안·조건 제안·검색을 포함한 HTTP 어댑터 버전이며 `X-Ontologics-HTTP-Contract` 헤더로 제공한다. 실행은 [README](../README.md#s1s2-웹-통합--개발-신원으로-실행)를 따른다.

서버가 생성한 DevelopmentContext와 IdentityProvider를 요청마다 CommonHost에 전달한다. 명시적 development 모드의 서버가 Alice/Bob/reader 허용 목록을 제공하고 상단 선택 박스는 그 목록만 선택한다. --user는 새 브라우저의 최초 기본 사용자다. 선택 요청 이외의 도구 body·신원 헤더·Agent 주장을 사용자나 권한으로 사용하지 않는다. 기존 초안의 작성자는 전환 후에도 유지한다. 요청 한 개당 별도 DB 연결, 기존 작성자·검토자 분리와 승인자 현재 권한 확인을 재사용한다. 실제 인증 공급자를 도입할 때는 검증된 요청 문맥→안정적인 subject 연결을 교체하고 이 개발 진입점을 운영용으로 노출하지 않는다.

| 경로 | 입력·응답과 권한 |
| --- | --- |
| `GET /api/context` | 현재 개발 신원/서버 권한·선택 가능한 development_users·CSRF 토큰·예시 A/B·model/embeddings 설정 여부·데이터 버전. 최초 호출은 개발 세션 쿠키를 설정하며 만료 후 재접속은 session_reset=true로 기본 사용자를 알림. `read` 검사. 모델 configured는 실제 통신 성공이 아님 |
| `POST /api/development-user` | `{user:"alice" 또는 "bob" 또는 "reader"}`만 허용. 현재 개발 세션+CSRF 필수. 서버 허용 목록→DevelopmentContext→IdentityProvider로 신원을 확인하고 cookie/CSRF를 회전·이전 세션 폐기. 응답은 context와 같은 형태. 권한/actor 입력과 비개발 모드는 거부하며 Agent 도구가 아님 |
| `POST /api/tools/{tool}` | JSON body는 기존 도구 arguments. 도구의 공통 envelope를 그대로 반환. actor/principals/작성자 주입·review/apply/seed는 불허 |
| `POST /api/review` | `{draft_id,version,decision,note,request_key}`. `decision=approve/request_changes/reject`, 의견 필수. 요청 사용자로 review하고 승인 성공이면 내부 `apply_approved`를 같은 정확 버전으로 호출. 결과 `data.review`·`data.application`은 각각 공통 envelope; 승인 성공·반영 실패를 보존 |
| `GET /api/demo-supplement` | 보존 `data/demo_records.json`의 U104 제거·완전 이력과 출처 설명을 읽음. 초안 생성·승인 아님. 기존 예시 경로이며 새 가상 근거 접수는 history/intake를 사용 |
| `GET /api/source?source_id=...&version=...` | 지정 버전의 SourceDocument를 조회한 뒤 `data/` 경로와 실제 바이트 SHA-256 확인 후 attachment로 제공. 임의 경로·환경 파일·해시 불일치 원본 제공 불가 |
| `POST /api/quality` | `{queries:{A,B},version,question,before_version?}`. 조회 조건·버전 필수. 비교 때 before_version과 version을 각각 이전/이후 데이터 버전으로 사용. 실제 Quality 도구 실행·동일 조건 직접 결과를 대조해 `data.consistency=matched`인 설명만 성공 반환 |
| `GET /api/workspace` | read 권한. 최신 버전의 초안 최대 100건을 `data.drafts`로 반환(draft_id/version/status/author/reviewer/base_version/applied_data_version). 영속 DB의 업무 재개 목록이며 조사 대화 기록 목록은 아님 |
| `POST /api/sources/intake` | `{filename,content_base64}`. draft 권한. 실제 바이트 해시를 manifest의 L/L1 WROOM ODS와 대조하고 제출 바이트를 파싱. `data.receipt_id/source/revision/configuration_id/rows/unknown_count/operations_count/base_version/verification` 반환. 미승인 후보 |
| `POST /api/sources/compare` | `{receipt_id,left_id}`. 동일 사용자·현재 기준 버전의 접수 후보와 기존 구성 비교. 기존 구성 비교 함수를 재사용하고 is_preview=true. 초안·확정 데이터 생성 없음 |
| `POST /api/sources/draft` | `{receipt_id,request_key,draft_id?,expected_version?}`. draft 권한. 서버에 보관한 정확한 후보 operations만 기존 초안 저장/검증 로직으로 처리. 응답은 prepare_draft와 같은 형태. 사용자 제공 operations/actor 불허 |
| `POST /api/history/intake` | `{filename,content_base64}`. draft 권한. 200 KB 이하의 가상 운영 기록 JSON을 무변경 보존. receipt_id/source/records/validation/operations_count/base_version 반환. 허용 필드·관계·시간은 기존 SCHEMA 검증. 확정은 별도 검토·승인 필요 |
| `GET /api/draft-source?draft_id=...&version=...&source_id=...` | 정확한 초안의 새 원본을 read 권한으로 내려받음. `data/` 내부 경로·바이트 해시 확인. 승인된 원본 조회와 구분 |
| `POST /api/assistant` | `{question,version,screen,context,action?,request_key?}`. context: configuration_id/node_id/kind/draft_id/draft_version/queries/before_version/receipt_id/search_query/origin/search_mode(lexical/semantic). action=explain 기본, prepare_draft는 명시 요청 키 필수. 응답 role/runtime/results/tool_calls/consistency/conditions, Supervisor 인계 시 delegation. Agent 승인·신원·대상/버전 이탈 불허 |


AI 초안 경로는 `get_source_receipt → prepare_source_draft → validate_draft`의 정확한 인자·순서를 강제한다. 직접 source_draft와 같은 요청 키를 사용한다. 성공은 `action=draft_prepared`와 draft/draft_record를 반환하며 승인 상태와 구별한다. 저장 후 모델·검증 기록 실패는 HTTP 오류와 `draft_saved_model_failed`에 저장된 ID/버전을 함께 반환한다. 같은 요청을 재시도해도 중복 초안을 만들지 않는다.

누락 조건은 `needs_input`의 required_fields/questions/target_screen, 완전한 명시 조건은 `confirm_conditions`의 proposed_queries를 반환한다. 두 상태 모두 `consistency=not_executed`이며 완료된 조사 답이 아니다. 사용자 질문에 없는 보드·로트·시간대를 채우지 않는다. 화면에서 조건 적용 후 기존 investigate를 호출한다. `consistency=matched`는 직접/Agent 도구의 반환 일치이며 자유문 전체의 사실성 보증이 아니다.

의미 검색의 read 권한·선택 버전 색인을 먼저 검사한 뒤 DB 트랜잭션 밖에서 임베딩한다. 같은 요청 안의 직접/Agent 대조에는 질문 벡터를 재사용하고 다음 요청과 공유하지 않는다. Knowledge 의미 검색은 `{data_version,answerable,selected_evidence_ids}`가 실제 반환 후보와 일치하는지 검사한다. `verified_selection`·`explanation_mode=verified_evidence_template`와 서버 display_text로 원본 버전/정수 데이터 버전·BOM 실장 표기를 구분한다. 근거 미선택은 부재·안전 판정이 아니다. `index_coverage={indexed,total,complete}`로 실제/가상 필터 안의 색인 범위를 표시한다. 미설정/미색인은 EMBEDDINGS_NOT_CONFIGURED/EVIDENCE_INDEX_NOT_READY(503), 부분 색인은 PARTIAL_EVIDENCE_INDEX 경고다. 다른 버전으로 자동 fallback하지 않는다. `examples.index_evidence`의 별도 host index 권한·실패 재개·무절단 한도와 실행 명령은 README를 따른다.


HTTP 클라이언트는 먼저 GET /api/context를 호출하고 반환된 쿠키를 후속 요청에 보낸다. POST는 Content-Type: application/json과 현재 /api/context의 X-CSRF-Token이 필요하다. payload는 1 MB 이하 유한 JSON 객체다. 서버는 127.0.0.1만 bind하고 정확한 Host·same-origin 요청을 확인하며 CORS는 열지 않는다. CSP·프레임 금지·no-store를 적용하고 HTML 원문이나 모델 응답을 실행하지 않는다. 쿠키 이름은 포트별 ontologics_dev_<port>이며 HttpOnly·SameSite=Strict·Path=/를 사용한다. 개발 세션은 서버 메모리에 최대 128개·8시간 유지되고 재시작하면 만료된다. 토큰은 로그인 증명이 아니다. 전환 후 이전 쿠키는 DEVELOPMENT_SESSION_EXPIRED, 현재 쿠키+이전 CSRF는 CSRF_REJECTED로 거부하며 변경 요청을 기본 사용자로 자동 대체하지 않는다. GET /api/context만 세션 복구를 허용한다. 이미 시작된 요청은 시작 당시 신원으로 끝나고 전환은 그 요청의 작성자를 바꾸지 않는다. X-Ontologics-Development-Subject 응답 헤더가 화면 신원과 다르면 프런트엔드는 결과를 적용하지 않고 새로고침을 요구한다. 실제 인증 도입 시 이 개발 선택 경로는 활성화하지 않는다. 요청마다 `X-Request-ID`를 발급한다. 원문 payload·환경 비밀값을 HTTP 로그에 기록하지 않으며 실제 업무 감사는 기존 runs/review_events/runtime 기록을 사용한다. DB 연결 이전 HTTP 실패의 영속 감사 기록은 아직 없다.

공통 성공은 HTTP 200이다. 입력/검증 오류는 400, 신원/권한·CSRF/Origin·개발 세션 만료는 403, 없는 대상/경로는 404, 오래된 초안/기준·중복 키 충돌은 409, 본문 초과는 413, 모델 실패는 502, DB/신원 공급자 장애·모델 미설정/처리 중은 503이다. HTTP 경계 실패도 공통 필드를 반환하되 확정할 수 없는 data_version/run_id는 null이다. `prepare_draft.ok=true`와 `validation.valid=true`는 별도 판단이다.

```json
POST /api/tools/investigate
{"query":{"mode":"A","lot_id":"lot:demo:lan8710:L07","unit_ids":["unit:U104"],"at":"2026-09-20T10:00:00+09:00"},"version":1}

POST /api/review
{"draft_id":"실제 반환 ID","version":2,"decision":"approve","note":"원본과 변경 전후 검토","request_key":"review-업무별-고정키"}
```

브라우저는 작업 내용별 request_key와 초안 ID·버전을 sessionStorage에 유지한다. 응답 유실 뒤 같은 내용은 같은 키로 재시도하고, 변경된 내용은 새 키를 쓴다. 반려 뒤 별도 안을 제출하려면 **별도의 새 보완안 시작**으로 새 업무 키를 발급한다. 데이터는 서버가 결정하며 브라우저 캐시는 DB schema와 사용자별로 분리한다. 서버 반영 키는 초안 ID·정확 버전에서 생성한다. 별도 public apply endpoint는 없다. 승인 이후 반영 실패는 같은 검토 요청으로 복구하며, 승인 재생이 있어도 오래된 초안/STALE_BASE를 우회하지 않는다. 승인과 반영을 하나의 DB transaction으로 묶지 않으므로 승인만 저장된 중간 상태를 명확히 표시한다. 브라우저 기록 삭제·다른 기기에서 이전 조사 재개는 영속 업무 기능으로 구현하지 않았다.

직접 조회·AI 모두 동일한 공통 조회와 버전 의미를 사용한다. A/B를 순서대로 읽을 때 첫 조회의 데이터 버전으로 두 번째를 고정하고, 보완 전후 비교는 저장된 보드 집합·로트·원래 offset을 포함한 같은 query를 재사용한다. AI에는 그 query와 버전을 고정해 전달한다. 모델이 다른 도구·조건·버전을 선택하면 **실행 전에** 거부하고 실제 도구 결과를 직접 조회와 대조한다. 자유문 설명의 정확성을 전부 보증하는 평가는 아니며 구조화 판정·근거가 기준이다. 역할은 Quality 그대로이고 승인 도구는 공개하지 않는다. 추적을 위해 `request.run_agent(..., capture_tool_trace=True)`를 선택적으로 사용하며 HTTP 반환은 조건·도구 run ID와 결과를 제공하고 키·환경값은 포함하지 않는다.

화면 판정은 `included→장착 이력 확인`, `excluded→조건상 제외`, `needs_confirmation→추가 이력 확인 필요`로 표시한다. 결함/안전 판정으로 바꾸지 않는다. awaiting_review는 작성 시 검증을 통과한 검토 대기, needs_changes는 검증 오류 또는 보완 요청, approved는 승인 저장, applied는 정확 버전 반영 완료다. validate_draft는 저장 상태를 자동 전환하지 않는다. 신규 submit 상태/API를 만들지 않고 한 초안의 생성·검증·검토를 이어간다. 보완 preview는 미반영임을 표시한다.

여섯 메뉴는 실제 공통 조회·등록·검토 기능에 연결한다. S2는 확인된 L/L1 WROOM ODS 접수만 지원하며 임의 PDF/OCR은 미지원이다. Knowledge의 문자열/ID 탐색과 실제 임베딩 의미 검색을 구분한다. Supervisor는 한 번의 역할 분류·인계, Product는 선택 구성·등록안 설명 및 명시 요청의 접수 후보 초안 저장을 지원한다. 실제 인증·지속 대화·범용 추출/해석 품질은 미완료다.

접수는 [receipts.py](../ontologics/receipts.py)의 호스트 작업이며 새 DB 테이블을 만들지 않는다. 기존 `requests`에 사용자·원본 hash·기준 버전별 receipt와 서버 후보를 보관한다. 재연결해도 같은 원본/버전은 같은 접수로 복구한다. 성공한 초안 요청은 기준 버전 변경 후에도 동일 키로 성공을 재생하며, 새 요청은 `STALE_RECEIPT`로 막아 원본 재접수·차이 재확인을 요구한다. 다른 사람의 접수는 `RECEIPT_OWNER_REQUIRED`, 미확인 BOM 바이트는 `UNSUPPORTED_SOURCE`로 거부한다. 선택 구성의 의존 노드만 후보에 넣고 기존 공통 ProductModel은 덮어쓰지 않는다.

가상 기록 JSON은 `{origin:"synthetic", authored_at:"ISO 시각+offset", notice:"근거 설명", records:[...]}`다. records는 기존 운영 다섯 타입의 id/type/필드를 쓰며 origin/evidence를 입력하지 않는다. 호스트가 원문 SHA ID의 SourceDocument와 `/records/N` Evidence를 추가하고 기존 근거를 보존한다. 등록 조건이 미충족이면 초안 needs_changes로 남고 승인은 차단된다. `data/receipts/<sha256>.json`은 Git 제외·무변경 보존이며 파일과 PostgreSQL은 별도 백업 대상이다. 파일 저장 오류는 SOURCE_STORAGE_ERROR와 실패 기록으로 남긴다. 파일 생성과 DB 저장은 하나의 트랜잭션이 아니므로 DB 실패 후 미참조 원본 파일이 남을 수 있으며 확정 사실로 공개하지 않는다.

[assistant.py](../ontologics/assistant.py)는 화면 역할 연결이다. 기본 explain은 읽기이며, 명시적 prepare_draft만 정확한 접수 후보를 저장한다. 모든 Agent 실행은 동일 사용자 CommonHost 및 기존 ModelRuntime을 재사용하고 실제 도구 trace와 직접 읽기의 결과·근거·버전을 대조한다. Quality는 추가로 모델의 observations(mode/unit_id/before/after/status/evidence)를 도구의 모든 보드 행과 대조한다. 보드별 근거가 비어 있으면 빈 배열이어야 하며 다른 보드의 근거를 빌리면 AI_FACT_MISMATCH로 설명을 숨긴다. 검증 결과는 agent_answer_validation 실행 기록에 남긴다. 구조화 값이 맞아도 자유문 요약이 판정과 모순되는 실모델 사례가 있어 Quality 자유문은 표시하지 않는다. 화면의 시간 규칙·판정·근거 설명은 검증된 값으로 서버가 구성한다. Product/Knowledge의 자유문 품질도 범용 검증 완료로 계산하지 않는다. 이 검사는 자유문 전체의 정확도 평가를 대체하지 않는다. Supervisor의 모델 분류는 product/knowledge/quality enum만 인계하며 도구·권한을 생성하지 않는다. 등록해/접수해/구조화해·초안 작성/저장처럼 명시한 작업 표현은 Product, 장착/제거/로트 사용처/보완 전후 표현은 Quality 범위로 제한하고 모델이 이를 벗어나면 AI_ROUTING_INVALID로 거부한다. “등록된 보드”처럼 기존 상태를 수식하는 표현만으로 Product를 강제하지 않는다. 이는 역할 제한이며 자동 저장이 아니다. 신규 조건이 없으면 전문 역할이 질문하고, 화면에 명시된 기존 조건이 있으면 그 조건을 유지한다. 접수 후보 저장을 임의 BOM/사실 생성이나 다단계 자율 업무 수행으로 확대하지 않는다.

## 모델 호출 접점

`runtime.py`의 `ModelRuntime`과 기존 gateway·PostgreSQL 실행 기록을 재사용한다. `openai_provider.py`는 OpenAI Responses API 고정 endpoint와 `gpt-5.4-mini-2026-03-17`만 사용한다. `parallel_tool_calls=false`로 순차 도구 실행을 요청한다. 원래 API `call_id`와 출력 항목(있는 경우 암호화된 reasoning 상태)을 보존해 후속 tool 결과를 연결한다. 도구 스키마는 `tool_specs.py`에 있으며 optional·이종 노드 필드를 위해 `strict=false`로 전달하고 공통 코드가 실제 입력을 검증한다. [공식 function calling 계약](https://developers.openai.com/api/docs/guides/function-calling)을 따른다.

`OpenAISettings.from_environment(환경파일)` → `OpenAIProvider(settings, PRODUCT_TOOLS 또는 QUALITY_TOOLS)` → `ModelRuntime(service, 호스트가 정한 신원, provider, config=ModelConfig(model=settings.model, max_steps=12))` 순서다. 실행마다 새 provider를 만든다. 예제 실행·모델·DB 환경변수·설정 우선순위는 [README](../README.md#실제-llm-실행과-동료-개발-시작)에 있다. 기본 timeout 30초, 응답 한도 4096토큰이며 설정 가능 범위는 각각 1~60초, 256~8192토큰이다. 실행 전체 wall-clock 제한이나 강제 종료 복구는 없다.

역할별 기본 도구는 `tool_specs.py`의 PRODUCT_TOOLS/QUALITY_TOOLS/KNOWLEDGE_TOOLS/SUPERVISOR_TOOLS가 기준이다. Product는 기존 초안 도구와 접수 후보 읽기/저장, Knowledge는 문자열·의미 검색을 포함한다. 웹은 매 요청의 정확한 도구·인자를 추가 제한한다. 승인·반영·bootstrap·신원 선택·색인은 모든 목록에 없다. 문서 안 지시문을 데이터로 처리하라는 역할 지침과 별개로, 실제 권한은 공통 코드가 강제한다.

`runtime_runs`, `runtime_steps`에는 공개 모델 설정·해시·상태·공통 도구 run ID가 기록된다. provider의 `calls`에는 HTTP 상태·요청/응답 ID·토큰·경과 시간이 있으며 키·HTTP 오류 본문은 없다. 기본 런타임은 원문 trace를 저장하지 않는다. 예제는 `capture_tool_trace=True`로 로컬 보고서를 만들고 웹 호스트는 같은 옵션으로 직접 조회와의 일치성을 대조한다. 일반 읽기 답은 반환하되 DB 대화 기록으로 보관하지 않는다. 검증된 Quality/의미 검색/초안 안내는 별도 display_text/display_hash로 반환하고 runtime.text는 숨김(output_withheld=true) 처리한다. runtime.output_hash는 DB에 저장한 원래 모델 출력 해시이며 서버 안내 문구의 해시와 구분한다. 로컬 검증 보고서·브라우저 기록은 공유 전에 내용을 검토한다.

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
