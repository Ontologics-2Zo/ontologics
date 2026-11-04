# Task08 별도 PostgreSQL 문서 저장 확장

2026-10-28 Asia/Seoul. 사용자가 기존 코드를 수정하지 않고 필요한 기능을 별도 파일로 만들도록 허용한 후 추가한 모듈이다. 기존 `PRODUCTION_PLANNING.md`19절과 Task08 결과의 “문서 영속 접점 미구현”은 그 시점의 기록이며 수정하지 않았다. 현재 추가 기능은 이 문서와 [별도 확장 결과/학습](../../sungsoo/task_08_별도_저장_확장.md)을 따른다.

## 추가 파일과 책임

- [document_store.py](../ontologics/production_planning/document_store.py): 업무 문서 전용 저장/수정/검토/송부 기록/정확 버전 export.
- [test_document_store.py](../tests/production_planning/test_document_store.py): 실제 일회용 PostgreSQL의 원자성·버전·신원·재생·권한 철회·출력 시험.
- [production_planning_document_store.py](../examples/production_planning_document_store.py): 새 PG에 세 문서 저장→재접속→v1 검토→구매 문구 수정v2→재검토→시험용 외부 송부 사실→정확 파일 출력.

`host.py`, `service.py`, `database.py`, `identity.py`, `tool_specs.py`, 기존 Task01~08 코드는 수정하지 않는다. 새 별도 DB·Service subclass·신원/승인 프레임워크·Agent 도구·HTTP/화면은 만들지 않는다.

## 생성과 호출

```python
from ontologics.production_planning.document_store import BusinessDocumentWorkflow

# 기존 trusted CommonHost와 같은 DB/IdentityProvider의 수요 workflow.
workflow = BusinessDocumentWorkflow(host, demand_workflow)
response = workflow.create_documents(
    person_context,
    calculation_arguments=calculation_arguments,
    request_key='caller-stable-key',
)
```

`host`와 `demand_workflow`는 trusted 애플리케이션 구성 객체다. 사용자의 actor/reviewer dict나 모델 출력으로 만들지 않는다. 생성자에서 같은 DatabaseSettings와 동일 IdentityProvider 객체를 확인한다. `calculation_arguments`는 기존 `build_saved_business_documents`가 받는 JSON 직렬화 가능한 Task07/03 입력이다. demand_id/item_id/data_version/expected_revision/expected_target_sha256는 명시해야 한다. snapshots는 원래 관측이고 가상 잔여로 대체할 수 없다. source refs/hash는 caller 근거이며 공급처 진위를 인증하지 않는다.

| 메서드 | 필수 입력과 반환 |
| --- | --- |
| `create_documents(context, *, calculation_arguments, request_key, scenario='virtual_remaining')` | 현재 수요/공개 조회→Task03~08로 세 초안을 만들고 한 transaction으로 세 문서 version1을 저장한다. `data.documents`에 ref/version/kind/content_sha256 목록을 반환한다. |
| `get_document(context, document_ref, document_version)` | 정확 버전의 payload/editorial/markdown/content_sha256, latest_version, 사건 목록, 현재 상태, binding_current/binding_issue를 반환한다. |
| `revise_document(context, document_ref, *, expected_version, edits, request_key)` | edits의 `text`는 기존 note/request_message, `inputs`는 기존 계산 입력 허용 목록이다. 현재 latest와 다르면 DOCUMENT_VERSION_CONFLICT. 성공 시 version+1, 옛 본문·사건은 그대로다. |
| `review_document(context, ref, version, *, expected_sha256, decision, note, request_key)` | decision=approve/request_changes. 작성자와 다른 현재 human+read+review가 정확 본문을 검토한다. event_id와 기록 대상만 반환하며 현재 유효성은 get에서 조회한다. |
| `record_manual_dispatch(context, ref, version, *, expected_sha256, details, request_key)` | 현재 작성자+read+draft와 유효한 독립 문서 검토가 필요하다. 실제 전송은 없으며 `transmission_performed=False`다. |
| `export_document(context, ref, version, *, expected_sha256, output_directory=None, filename=None)` | 정확 저장 버전 Markdown/encoding=utf-8을 반환한다. 선택 trusted 디렉터리와 새 .md 파일명으로 배타 파일 생성도 가능하다. |

모든 DB 동작은 기존 공통 envelope/run_id/errors를 반환한다. 규칙 `document-store-0.1`은 content.storage_rule_version에, 공통 temporal-0.1은 envelope.rule_version에 있다. 데이터 버전, 수요 revision/target hash, 문서 version/내용 hash, 임시 초안 source_draft_sha256를 구별한다.

## 같은 PG를 확장하는 실제 방법

새 모듈은 기존 `CommonHost.request(context)`가 연 **그 RequestSession의 Service 연결**을 재사용한다. `_DocumentStore`는 업무 SQL action을 모은 작은 adapter이며 별도 권한/트랜잭션 엔진이 아니다. `Service._run`을 통해 schema lock→권한→request 재생→action→requests/runs→commit/rollback이 실행된다.

문서가 처음 저장될 때 그 action 안에서만 같은 schema에 두 테이블을 만든다.

| 테이블 | 내용 |
| --- | --- |
| pp_document_versions | (document_ref, document_version) PK, owner_actor/kind/data_version, immutable content/Markdown, 원 Task08 draft, 재계산 recipe, 수요 확인 사건·신원 의존성, record_hash |
| pp_document_events | 정확 ref/version FK, review/manual_dispatch 구분, 서버가 붙인 actor/subject/identity_mode/created_at, 정확 content hash, 사건 내용과 event_hash, 순서 |

최신 버전은 MAX(document_version)로 조회하므로 별도 가변 head 테이블이 없다. 저장 API는 기존 version/event를 UPDATE/DELETE하지 않는다. 기존 버전과 사건의 hash, 본문 재생성 일치도 조회 시 검증한다. DB 관리자의 직접 SQL 수정은 권한 경계 밖이지만 불일치 변조는 DOCUMENT_INTEGRITY로 감지한다. DB 연결 자체와 schema 초기화·통신 실패 정책은 기존 공통 구현을 따른다.

사용하는 내부 접점은 `host._database/_identities`, `session._service`, `Service._run/_require/_version/_envelope`, 기존 `DemandConfirmationStore._integrity`다. 사용자가 이번 별도 확장을 허용했으므로 명시적으로 사용한다. 이후 공통 내부 API가 바뀌면 새 모듈의 회귀 시험으로 호환성을 확인해야 한다.

## 버전·재계산·현재 근거

수정은 텍스트만 또는 구조화 입력을 지원한다. 텍스트는 기존 `edit_document_text`의 잠긴 필드 보호를 그대로 쓴다. 구조화 입력이 바뀌면 저장된 recipe를 바탕으로 기존 `build_saved_business_documents`를 호출한다. 기존 계산 결과 숫자를 덮어쓰지 않는다. N·수요·구성·공개 버전 변경은 edits로 우회할 수 없다. 변하지 않은 텍스트/입력은 DOCUMENT_NO_CHANGES이며 새 version을 만들지 않는다. 이 정책은 새 문서 API가 명시한 계약이지 수요 NO_CHANGES를 추정한 것이 아니다.

쓰기 transaction 안에서 공개 최신 data_version, 수요 최신 revision/target hash/저장 원본 hash/identity_mode, 확인 사건 id/hash를 다시 확인한다. 계산 도중 수요가 바뀌면 저장하지 않는다. 수요 확인의 유효성은 기존 수요 workflow.get을 다시 호출해 대조한다. 그 확인자의 현재 principal을 의존성으로 저장하고 transaction에서 다시 검사하므로 **확인 사건 자체가 그대로여도 확인자 권한 철회는 문서를 현재 유효하지 않게 만든다.** 수요 확인 정책을 새로 구현하지 않는다.

과거 버전은 계속 읽고 정확 본문을 export할 수 있다. `binding_current=False` 또는 문서 최신 버전이 아니면 새로운 검토/송부/수정을 허용하지 않는다. 오래된 본문을 조용히 갱신하지 않으며 최신 수요를 대상으로 새 문서를 작성한다.

## 사람 신원·검토·재생

현재 요청 사람은 기존 호스트에서만 받는다. 신원 lookup를 다시 검사하고 요청 시작 권한과 현재 권한의 교집합을 적용한다. **read 검사는 기존 `_run`의 재생 분기에 들어가기 전에도 수행**한다. write/review 권한은 `_run`이 다시 검사한다. 본문이나 caller 입력으로 actor/reviewer/sender를 지정할 수 없다.

- 소유자+read: 자기 문서 조회/export.
- 현재 read+review: 문서 조회와 다른 사람의 문서 검토. 자기 문서 검토는 SELF_REVIEW_FORBIDDEN.
- 소유자+read+draft: 작성/수정/외부 송부 사실 기록.
- AgentGateway에 새 메서드를 등록하지 않는다. Agent는 문서 검토/송부 기록을 호출할 수 없다.

검토는 exact version/content hash의 `document_content_only` 범위다. latest review가 approve이고 검토자 read/review 권한과 원 수요 결합이 현재 유효할 때만 state.review=reviewed다. request_changes이면 changes_requested, 권한·수요·문서 최신성이 바뀌면 review_invalidated다. 새 version의 events는 비어 있어 이전 검토를 상속하지 않는다.

같은 actor/request_key/동일 인자 재시도는 기존 `_run`의 원래 응답을 재생하며 버전/사건을 중복 생성하지 않는다. 같은 키/다른 인자는 IDEMPOTENCY_CONFLICT다. 현재 권한은 재생 전 확인하지만, 성공 재생은 **과거 기록의 접수 확인**이며 지금도 검토가 유효하다는 뜻이 아니다. 새 효과는 없고 현재 상태는 get_document로 조회한다. request_key는 매번 임의 생성하지 말고 같은 요청 재시도에서 유지한다.

동시 동일 expected_version 수정은 공통 schema lock 아래 하나만 새 버전을 만들고 나머지는 충돌한다. 실패는 전체 rollback되며 부분 문서 묶음과 성공 request가 남지 않는다. 실패 runs 감사는 기존 의미를 유지한다. 새 별도 감사 저장소는 없다.

## 외부 송부 사실과 export

`details`는 정확히 recipient/sent_at/method/source를 가진다. source는 source_ref/sha256/locator다. sent_at는 offset이 있는 시각이며 정확 문서 버전 생성 시각 이상, 기록 시점 이하이어야 한다. 미래 사건·버전 생성 전 사건·날짜-only·임의 sender_actor를 거절한다. 송부자와 기록 시각은 호스트 신원·Service.now에서 붙인다. 현 구현은 작성자 본인이 외부 송부했다고 남기는 기록만 지원하며 대리 송부 기록은 지원하지 않는다.

송부 기록은 state.dispatch=recorded다. purchasing_received/ordered/received는 계속unknown이며 생산·구매 권한/자원 예약False, 고객 도착 준수None이다. 신뢰된 사람 입력의 **보고 사실**이지 외부 메일 서버의 전송/수신 인증이 아니다. 실제 전송 도구를 호출하지 않는다.

저장 본문은 생성 시점의 데이터이고 수정되지 않는다. 이후 review/dispatch 상태는 별도 사건이므로 export에 현재 상태를 섞어 같은 버전의 bytes를 바꾸지 않는다. 헤더에 “생성 당시 미검토·미송부, 이후 사건은 별도 조회”라고 표시한다. 정확 version/content hash와 payload가 본문에 포함된다. Markdown 내부 근거·미확인·원문 표기·가정도 유지된다.

파일 출력은 기존 신뢰된 디렉터리 안의 단일 .md 이름으로 배타 생성한다. 경로 탈출·심볼릭 링크·기존 파일 덮어쓰기를 거절한다. 파일 저장 실패는 INVALID_EXPORT_PATH이며 DB 문서에는 영향이 없다. 현재는 Python/CLI 출력이지 새 웹 다운로드 화면이 아니다.

## 검증과 운영 경계

정확한 최종 명령·통과 수·파일 보존·산출물은 [별도 확장 결과](../../sungsoo/task_08_별도_저장_확장.md)를 따른다. 실제 일회용 PG에서 재접속, 옛버전 불변, 현재 검토, 권한 철회, 수요 변경, 재시도/충돌, rollback/동시성, 정확 export를 검사한다. 운영 DB에는 연결하지 않는다.

예제의 신원은 개발용이며 해석은 수작업 fixture다. 송부 사건은 테스트라고 명시한 synthetic fixture이며 실제 송부가 아니다. 원 실제 BOM·개발 재고·별도 초기 가정70대14:30·실제 잔여30대 미정·견적/납기 미확보를 유지한다. 개발 신원이 실제 인증 인프라를 대신하지 않는다. 모델/임베딩 API 호출은 없다.
