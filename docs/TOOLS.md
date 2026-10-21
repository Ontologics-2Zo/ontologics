# 공통 도구 계약 0.1.0

Python 3.10+ 내부 함수가 기준이다. 모든 경로는 저장소 루트 기준이며 별도 서비스 배포가 필요 없다. `Service`는 신뢰된 호스트 애플리케이션, `AgentGateway`는 서비스 Agent에 공개하는 도구 경계다. `examples/agent_tools.py`와 `ontologics/demo.py`는 실행 가능한 예제다.

## 그래프·저장·시간

`model.py`의 `SCHEMA`·허용 필드·검증 함수가 실행 계약이다. `ontology/tbox.ttl`은 여기서 생성한 OWL/RDFS 클래스·관계 정의이며 자동 추론 엔진이나 SHACL 구현은 아니다. `data/initial_graph.jsonld`는 관계를 ID로 연결한 JSON-LD A-box다. SQLite의 `snapshots`에 정규화 노드 묶음을 불변 데이터 버전으로 저장한다. 작은 기준 데이터에 맞춘 저장이며 대규모 그래프 최적화·SPARQL 서버는 포함하지 않는다.

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

## 공통 반환·오류

```json
{
  "ok": true,
  "data": {},
  "evidence": ["evidence:..."],
  "data_version": 3,
  "schema_version": "graph-0.1",
  "rule_version": "temporal-0.1",
  "contract_version": "0.1.0",
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

검토 decision은 approve/request_changes/reject다. 작성자와 승인자가 달라야 하고 승인자는 `kind=human`, `roles`에 review가 있어야 한다. `apply`는 별도 apply 권한이며 승인된 버전·hash·기준 버전·현재 검증을 다시 검사한다. 의존 변경과 스냅샷·승인 상태·중복 방지 결과는 한 SQLite 트랜잭션으로 반영한다. 중간 실패는 전부 롤백한다.

초안 상태는 awaiting_review / needs_changes / approved / applied / superseded / rejected다. 실행 상태 succeeded/failed는 별도 `runs`에 기록한다. `review_events`는 검토 결정을 추가 기록하며 새 검토나 초안 수정으로 이전 의견을 지우지 않는다. `run_history`는 audit 권한이 있는 호스트만 사용한다.

UI는 초안 버전·변경 전후·근거 위치·검증·미확인과 승인/보완/거절 동작을 연결하면 된다. 사용자가 보고 승인한 정확한 버전을 호출해야 한다. 로그인·세션·CSRF·프로세스 격리는 실제 UI/API 통합 단계의 과제다. 예제 principals를 네트워크 API 입력으로 받으면 안 된다. 로컬 Python 객체나 DB 파일을 가진 개발자를 격리하는 권한 모델은 아니다.

`bootstrap`은 seed 권한과 비어 있는 DB가 필요한 개발 전용 적재다. 운영 승인 흐름이나 Agent 도구로 사용하지 않는다.

## 모델 호출 접점

`runtime.py`의 `ModelRuntime`은 고정 모델명·주입형 provider·호출 횟수 제한을 사용한다. 공급자 SDK와 실제 모델은 아직 연결하지 않는다. provider가 반환하는 tool_calls는 위 gateway만 호출한다. 모델은 승인·신원·모델 설정을 바꿀 수 없다. provider는 자신의 네트워크 timeout을 설정해야 한다. 실모델 품질·비용·지연·장기 실행 복구는 아직 검증하지 않았다.

## 계약 변경

새 원본·변형은 manifest와 parser 매핑, 근거, CQ 기대 결과를 함께 추가한다. 원본 간 같은 부품을 합치려면 검토된 대응표와 ID 이전 영향을 먼저 설계한다. 시간 의미·제품 단위·승인 권한·공통 schema 변경은 명시적 버전 변경과 회귀 검증 대상이다. 공통 도구에 필요한 기능부터 추가하며 별도 Agent·서버·Skill 생성을 전제하지 않는다.
