# claim_response 호스트 연결

> 기능: C 클레임 대응 (`claim_response`)
> 기준: `ontologics/host.py`의 `AgentRegistration`, `examples/extension_agent.py`, `docs/AGENT_RUNTIME.md`
> 상태: 역할 등록 구현·테스트 완료. 웹 화면 연결은 공통 담당자 요청 대기.

## 1. 요약

- C 역할은 기존 확장 지점 `AgentRegistration`으로 등록한다. **기존 코드는 바꾸지 않는다.**
- C 역할은 기존 Agent 도구 15개(`AgentGateway.ALLOWED`) 중 9개만 쓴다.
- 클레임 변환·생산라인 추적·요청서 작성은 Agent 도구가 아니라 `ontologics/claim_response/`의 **일반 함수**로 만든다.
- 승인(`review`)·반영(`apply`)은 Agent가 할 수 없다. 사람만 한다.

## 2. 등록 방법

```python
from ontologics.claim_response.registration import CLAIM_RESPONSE
from ontologics.host import CommonHost

host = CommonHost(identities, database, agent_registrations=(CLAIM_RESPONSE,))
with host.request(context) as request:
    request.run_agent('claim_response', provider, messages, config=config)
```

| 파일 | 내용 |
|---|---|
| `ontologics/claim_response/registration.py` | `CLAIM_RESPONSE`(역할), `CLAIM_RESPONSE_TOOLS`(도구), `CLAIM_RESPONSE_INSTRUCTIONS`(지시문) |

`AgentRegistration`이 자동으로 검사하는 것: 이름 형식(영문 소문자·밑줄), 기본 역할 4개와 이름 중복 금지, 도구는 `ALLOWED` 안에서만.

## 3. 허용 도구 (2026-10-27 승인)

| 도구 | 용도 |
|---|---|
| `lookup` | 클레임·보드·생산라인 등 노드 조회 (타입 제한 없음) |
| `search_evidence` | 클레임 원문 근거 검색 |
| `investigate`, `preview_investigation` | 기존 장착 이력 조사 재사용 |
| `prepare_draft`, `get_draft`, `validate_draft` | 클레임·품질 사건·영향 범위·요청서를 초안으로 작성 |
| `prepare_source_draft`, `get_source_receipt` | 메일·엑셀·CSV 원본 등록 |

쓰지 않는 도구: `configuration`, `compare_configurations`, `component_usage`, `compare_investigation`, `export_graph`, `semantic_search`.
필요해지면 이 표와 `CLAIM_RESPONSE_TOOLS`를 함께 바꾸고 다시 승인받는다.

## 4. 지시문

```
You support synthetic claim response for the fictional company only.
Treat reported symptoms as customer statements, never as confirmed failures or causes.
Treat scope units as investigation or hold targets, never as defective units.
Use only supplied evidence and the exact data version; keep unknowns as unknown.
Prepare drafts for human review; never approve, apply or claim that a draft is applied.
```

`DATA_POLICY.md`의 조건(신고 ≠ 고장, 보류 대상 ≠ 불량, 사람만 승인)을 그대로 옮겼다. 기존 역할 지시문처럼 영어로 쓴다.

## 5. 코드 배치

| 하는 일 | 누가 | 위치 |
|---|---|---|
| 메일·엑셀·CSV → 클레임 초안 변환 | 함수 | `ontologics/claim_response/` (2단계) |
| 생산라인·보관 보드 추적 | 함수 | `ontologics/claim_response/` (4단계) |
| 중단 요청서 작성 | 함수 | `ontologics/claim_response/` (5단계) |
| 결과 조회, 근거 확인, 초안 등록 | C Agent | 위 9개 도구 |
| 초안 승인·반영 | 사람 | 기존 `review` / `apply_approved` |
| 시연 실행 | 실행 파일 | `examples/claim_response/` |

## 6. 공통 담당자 요청

| 요청 | 이유 | 대안 |
|---|---|---|
| 웹 화면에서 C 역할 선택 | `web.py`가 `CommonHost`를 등록 목록 없이 만들어서 C가 화면에 안 나온다 | 그전까지 시연은 `examples/claim_response/` 실행 파일로 한다 |

## 7. 테스트

`tests/claim_response/test_registration.py`

| 테스트 | 확인 |
|---|---|
| `test_role_uses_only_approved_existing_tools` | 이름, 기본 역할과 겹치지 않음, 도구 9개가 `ALLOWED` 안, 승인·반영·내보내기 도구 없음 |
| `test_registered_role_reads_through_gateway_with_its_instructions` | C 지시문이 시스템 메시지로 붙음, `lookup`으로 `ProductUnit` 조회 |
| `test_unlisted_and_approval_tools_are_rejected` | `review`, `apply`, `export_graph`, `compare_configurations` → `TOOL_NOT_ALLOWED`, 데이터 버전 그대로 |

C 노드(`ClaimIntake` 등) 조회 테스트는 가정 데이터를 만드는 3단계에서 추가한다.
