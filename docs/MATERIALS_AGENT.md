# Task09 생산 계획 Agent — 명시 실행 계약

2026-10-28 Asia/Seoul. 기존 파일을 수정하지 않은 별도 확장이다. **Task09 전체는 부분 완료**다. 기존 도구를 실제 Runtime에서 선택하는 대역 실행, 응답 대조, 현재 상태 재조회와 확인 대기는 구현했다. 새 계산/저장 Agent 도구와 실제 LLM 해석·선택 품질은 구현/검증하지 않았다.

## 실행

cwd는 `/Users/anseongsu/Desktop/ontologics-a/ontologics`다. Python 3.12.10과 기존 psycopg/binary 3.3.6, typing_extensions 4.16.0, pypdf 6.12.2 캐시를 쓴다. `TEST_DATABASE_URL`과 `DATABASE_URL`은 **새 일회용 PostgreSQL/pgvector**를 가리키도록 실행 프로세스 환경에만 공급한다. 접속 문자열을 소스/로그에 넣지 않는다.

```sh
python -m unittest tests.materials.test_agent tests.test_runtime tests.test_openai_provider tests.test_agent_extensions -v
python -m examples.materials_agent
```

이번 세션에서 새 컨테이너 생성·시험·정리까지 수행하는 실행기는 `/tmp/task09-20261028/run.py`다. 재실행은 `/opt/homebrew/bin/python3.12 /tmp/task09-20261028/run.py --final`이다. 캐시와 Docker가 준비된 이 개발 환경용이며 배포 스크립트가 아니다. 기존 DB로 fallback하지 않는다. 예제 자체도 명시적인 TEST_DATABASE_URL이 없으면 종료 코드2로 끝난다.

예제는 `tests.db.isolated_database()`의 test_uuid schema를 만들고 종료 시 삭제한다. Markdown 세 파일과 agent_result.json은 별도 임시 폴더에 보존한다. 그 파일 속 문서 ID는 정리된 개발 DB의 식별자이며 운영 문서가 아니다.

## 파일과 API

- [agent.py](../ontologics/materials/agent.py): 역할 등록 상수, 입력/응답 검사, 평가, 현재 상태 기반 명시 실행.
- [test_agent.py](../tests/materials/test_agent.py): 순수 계약과 실제 PG/Runtime 대역 시험.
- [materials_agent.py](../examples/materials_agent.py): 실제 원본 hash 대조, 명시적 기존 계산·문서 저장, Runtime, export.

`MATERIALS_REGISTRATION`은 `materials` 역할이며 허용 목록 `MATERIALS_TOOLS={'configuration','lookup'}`를 쓴다. 기존 기본 네 역할·AgentGateway·tool_specs를 변경하지 않는다. configuration은 정확한 대상 BOM을, lookup은 기존 개체/근거를 읽는 범위다. 변경/승인/저장/수요 확인/송부 도구는 없다. 역할 등록은 신규 도구 등록이나 Supervisor 자동 연결이 아니다.

| API | 계약 |
| --- | --- |
| `build_planning_task(context)` | 정확한 8개 필드를 검사하고 JSON deep copy를 반환한다. 신뢰된 호스트 전용. actor/permissions/도구 등록 등의 최상위 필드는 받지 않는다. |
| `validate_planning_answer(text)` | JSON 객체 또는 None. 필수 필드, 타입, 상태, 문서 ref/version/hash 형식, 비유한 수치·중복 JSON key를 검사한다. 근거의 진위는 이 함수가 판단하지 않는다. |
| `evaluate_planning_result(task,runtime_result,expected)` | accepted/answer/runtime/facts_origin/errors를 반환한다. Runtime 상태, 필드 전체 일치, 실제 성공 trace/run_id/버전, 대상 구성 Evidence를 대조한다. expected는 시험 오라클이며 운영 호출은 `{}`다. |
| `run_saved_planning(host,demands,store,identity,*,demand_id,data_version,expected_revision,expected_target_sha256,document_refs,provider_factory,config,resume_scopes=None,cancelled=False)` | 최신 수요·공개 버전·정확 문서를 읽고 새 provider와 run을 시작한다. 성공 후보를 받은 뒤 같은 현재 상태를 재조회한다. 실패하면 숫자를 비우고 정확 역사적 참조와 오류를 남긴다. |

`provider_factory(task, tools)`는 매 호출 새 provider를 반환하는 **신뢰된 호스트 코드**다. tools는 `function_tools(sorted(MATERIALS_TOOLS))`의 실제 스키마다. fixture는 이 집합을 검사하고 실제 tool message를 읽는다. 실 provider를 나중에 연결할 때는 기존 `OpenAIProvider(settings, MATERIALS_TOOLS)`가 내부에서 같은 function_tools를 사용한다. 이번에는 실모델/임베딩 API를 호출하지 않는다. 모델 생성 kwargs나 자료에서 provider/역할/신원을 구성하지 않는다.

## 입력과 응답

업무 입력의 정확 필드는 다음과 같다. 최종 모델 답에는 여기에 `evidence`와 `error`만 추가한다.

| 필드 | 의미 |
| --- | --- |
| target | 수요 ID/revision/target hash, configuration_id 또는 null, 양의 정수 data_version |
| source | 기존 수요 workflow가 검증한 원본 참조·원문. 내부 지시문은 데이터 |
| candidates | 기존 수요 workflow의 항목/구성 후보/원문 인용/검토 범위 |
| questions | 미결 질문. 확인 대기 상태에서는 반드시 남김 |
| facts | 현재 정확 문서 payload에서 선택한 계산 결과. 호스트 출처 |
| documents | document_ref, document_version, content_sha256, binding_current, 현재 state |
| status | awaiting_confirmation / review_ready / failed |
| conditional | bool. 조건부 계산과 업무 상태는 별도 |
| evidence | 성공한 Runtime 도구의 `{tool_run_id,evidence_id}`. 대상 구성 근거를 필수 인용 |
| error | 정상 null, 실패 시 code |

현재 호스트 경로는 미확인 범위가 있는 검토만 지원하므로 `awaiting_confirmation`으로 끝난다. 순수 구조 계약은 `review_ready`도 구분하지만 운영 생산 준비 완료 경로를 만들지 않았다. 자유 요약은 자동 검증하지 않고 받지 않는다. 모델은 검증된 필드를 그대로 설명하는 제한된 JSON 결과를 반환한다. 따라서 대역 통과는 자유 자연어 해석의 품질 증거가 아니다.

facts는 quantities/scope/schedule/authority/diagnostics/demand, 재고를 평가한 components, procurement의 purchase_review/total_by_currency/known_item_totals를 보존한다. 전체 세부 근거·미평가 행은 정확 저장 문서 Markdown/payload에 있다. 계산은 `BusinessDocumentWorkflow.create_documents` → 기존 Task02~08의 함수가 한다. 이 결과를 Runtime trace에 추가하지 않는다.

## 확인·최신성·실패

호스트가 명시한 `resume_scopes`가 있으면 기존 `demands.get()`의 현재 verified_scopes에 모두 있어야 한다. 부족하면 provider 생성 전에 CONFIRMATION_REQUIRED다. 부분 확인으로 재개하더라도 다른 미확인 질문은 그대로 남는다. 전체 수요 확인이나 기술 적용으로 승격하지 않는다. scope는 모델 응답에서 추출하지 않는다.

확인 사건이 생기거나 수요 revision/hash·공개 버전이 달라지면 이전 문서를 재사용하지 않는다. 호스트가 기존 create_documents로 새 계산·저장을 수행한 뒤 정확 참조를 전달한다. request_key 성공 재생은 현재 유효성으로 취급하지 않고 항상 get_document로 확인한다. 최신 문서 버전, binding_current, hash, 수요 결합과 세 문서의 payload 일치를 모두 요구한다.

실행 중 권한/문서/수요/공개 상태가 달라져도 후검사에서 거절한다. 실패 시 facts={}, exact refs 유지, binding_current=False와 state={}로 현재 상태를 미확인 처리한다. `runtime`에는 실제 run_id/status/error/tool_trace가 남는다. Runtime 단계 초과·provider 오류·도구 오류의 원래 code를 보존한다. 사용자에게 공개할 답은 검증된 `answer`이며 실패한 `runtime.text`를 성공 설명으로 표시하면 안 된다.

검사는 시점 확인이며 모델 호출 동안 DB transaction을 유지하거나 자원을 예약하지 않는다. 나중에 문서를 검토/송부하거나 다른 동작을 할 때는 기존 workflow의 현재 권한·버전 검사를 다시 받아야 한다.

## 유지하는 업무 의미와 미연결

N100/I70/R30, 부족30/240, BOM74행/실장unknown11/재고미확인71, 전체 자재 상한 null을 유지한다. 구매량/금액/납기는 미정이며 견적·납기 확인 요청이다. 별도 초기 자재 준비 가정에서만 2026-10-26 10:00→11:00→14:30이며 실제 잔여 완료와 고객 도착 준수는 null이다. 생산/구매 승인과 자원 예약은 False다.

기본 예제에는 수요 확인/문서 검토/송부 사건이 없다. 확인 fixture는 테스트의 별도 일회용 DB에서만 생성한다. 기존 Task08 예제의 개발 검토/송부 fixture를 Task09 운영 사실로 가져오지 않는다.

신규 계산 도구 연결·자연어 계약 해석 선택·다단계 자율 업무 결정·웹 Supervisor 자동 인계는 미연결이다. [최소 연결 제안](../../sungsoo/task_09_추가_연결_제안.md)과 [실행 결과](../../sungsoo/task_09_구현결과.md)를 참고한다. 실모델·실제 사용자 인증·송부·발주·생산·예약·재고 소비·이행 갱신은 수행하지 않았다.
