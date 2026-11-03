# 단계 4 첫 범위 — CQ01 접수 연결·무료 검증 결과

2026-10-29. `feature/b-first-mvp`, HEAD `f264955`, 원격보다 6커밋 앞선 상태를 유지했다. 이번 작업은 사용자가 확인한 단계 4의 CQ01 접수·비용/승인 무통신 검사 범위다. 실제 유료 API 요청 0, 실제 키 로드 0, 기존 DB/실행 재개 0, commit/push/PR 0이다.

## 결과와 한계

CQ01 접수는 `AgentSpec → AgentRegistration.from_spec → AgentTask → request.run_task`로 실제 연결됐다. 기존 bounded provider의 4응답/9도구 단계와 독립 DB/정답 evaluate를 유지했다. 공통 사전 조회를 더한 요청 전체는 모델 4회·도구 10회·repair 0회이며 비용 장부도 같은 요청/하위 역할에서 공유한다. 보존한 [접수 보고서](../../artifacts/local/order_fulfillment/cq01_typed_20261029_0206/proof/cq01/reports/cq01_intake.json)는 `offline_http_double`, 실제 요청 0, 미승인 초안 `awaiting_review`, 사람 검토 이벤트 0이다. HTTP 대역의 원래 JSON/text·wire·provider 제출·runtime과 공통 AgentResult/근거를 함께 저장했다. 이는 새로운 실제 LLM 성공이나 사람 의미 검토가 아니다.

**도메인 계약 전체 호환성은 미확인이다.** B의 `model.SCHEMA_VERSION=graph-0.5`와 공통 `PinnedContext.ontology_version=graph-0.1`이 다르며, 공통 context는 현재 `graph-0.1`만 허용한다. B에서 우회하거나 공통 파일을 바꾸지 않았다. 결과의 `connection_scope`에 두 버전과 `full_compatibility_verified=False`를 기록했고 요약에도 `domain_schema_review_required=True`, `full_typed_connection_ready=False`를 둔다. R4에서 버전의 의미와 지원 범위를 공통 담당자가 검토해야 한다. Git 병합 충돌 없음, 무료 실행/회귀 통과, 도메인 계약 정합성 완료는 서로 다른 판정이다.

나머지 6역할은 기존 `run_agent` 경로를 유지한다. 전체 10대표의 기존 무료 업무 회귀는 통과했지만 전체 typed 전환은 아니다. CQ01 접수 단독은 미승인 상태이며, 정확 초안 업무 승인 성공·작성자/검토자 분리·버전/hash 변조 거부는 기존 별도 회귀로 확인했다. 모델 답변의 별도 한국어 의미 검토를 자동 통과시키지 않았다.

## 변경 파일과 간소화

| 파일 | 이번 변경 |
| --- | --- |
| `examples/order_fulfillment/b_task_agent.py` | 접수 전용 Spec/입력/context/원본 decode/관찰 결과 검증. 기존 `_answer`·bounded 지침 재사용 |
| `examples/order_fulfillment/b_host_agent.py` | 기존 7개 호환 등록은 유지하고 접수 typed 등록을 따로 정의 |
| `examples/order_fulfillment/b_run_workflow.py` | 접수만 run_task, 공통 정책 주입, 원본과 typed 결과 동시 보존, 실패 시 부분 초안 자료를 schema 정리 전에 보존 |
| `examples/order_fulfillment/b_cost_authorization.py` | 공개 정책 파일의 불변 스냅샷, 기존 공통 파서/예약, 실행에 결합된 기본 미승인 새 형식. 별도 비용 장부·가격 파서 없음 |
| `tests/order_fulfillment/test_b_task_agent.py` | 입력/근거/최종 답/권한/공통 저장/미승인 보존 11개 |
| `tests/order_fulfillment/test_b_cost_authorization.py` | 정책/예약/실행 결합/변조/옛 승인/synthetic 거부 14개 |
| `tests/order_fulfillment/test_b_typed_workflow.py` | 실패 후 후속 호출 중단·원본/부분 초안 보존·중복 시작 거부·공통 비용 누적 6개 |
| `docs/order_fulfillment/B_CQ01_TYPED_PLAN.md` | 수정 전 계획과 보존·수락·회귀 기준 |
| `docs/order_fulfillment/B_MAIN_CONNECTION_DESIGN.md` | R4의 실제 context/B schema 버전 차이 추가 |
| `docs/order_fulfillment/B_CQ01_TYPED_REPORT.md`, `PROJECT_STATUS.md` | 이번 B 작업 결과와 한계 기록 |

공통 공개 계약·호스트·Harness·런타임·서비스·도구·도메인 model은 이번에 수정하지 않았다. 노트북과 helper UI도 이번 단계에서 수정하지 않았다. 새 의존성·새 Agent 프레임워크·별도 계산/승인/비용 엔진을 추가하지 않았다.

## 비용·승인 적용 범위

신뢰된 운영자가 지정할 공개 JSON 파일은 기존 `ONTOLOGICS_REQUEST_MAX_COST_USD`, `ONTOLOGICS_MODEL_PRICES_JSON` 두 키만 받는다. 키/환경 파일을 읽지 않는다. 파일 bytes hash, 공통 정책 fingerprint, 순서 있는 대표 목록, 대표별 P·전체 N×P, 고정 모델·1024·30초·재시도/repair0·4/40을 승인 계획에 묶는다. 공통 CostLedger로 최소 입력 400000·출력 1024 예약 가능성을 무통신 확인하며, 실제 실행기의 호스트에도 같은 정책 객체를 전달한다. 검사의 가격과 금액은 모두 명시적인 가상 자료다. 실제 운영 가격·상한은 읽거나 임의로 정하지 않았다.

새 승인 helper는 `approved=False` 자료만 준비하고 옛 형식·타 실행/소스/입력/gate/정책·제한 변조와 synthetic 유료 사용을 거부한다. 현재 정책과 비교하며 승인 파일에서 신뢰된 가격을 가져오지 않는다. 다만 **실제 키 로더/유료 backend/CLI·노트북에 새 승인 형식을 연결한 것은 아니다.** 기존 전역 live 차단을 유지한다. 향후 실제 진입점 연결에는 현재 공개 정책 경로 재로드·진짜 무료 gate/서버 종료 증거 확인·새 승인 대조를 키 읽기보다 먼저 수행해야 한다. helper 단위 통과는 유료 실행 준비 완료가 아니다.

## 무료 검증

| 검증 | 결과 |
| --- | --- |
| 수정 전 기존 접수·승인·전송·중복·실패·호스트 회귀 | 93개 전부 통과 |
| 최종 전체 unittest discover | **829개 실행, 823개 통과, 6개 skip, 실패/오류 0, 141.097초** |
| 새 검사 | 위 전체에 포함된 31개. 수치 중복 합산 없음 |
| 보존 가능한 무료 예제 | 접수 4회 HTTP 대역·실제 API 0, 공통 demo v1→v2→v3, cq_check, agent_tools, typed model/history 통과 |
| 실패·거부 | 최종 답 변조 시 원본/부분 초안 보존 및 약속 후속 전송 0. 보고서 생성 오류도 부분 초안 보존 후 소유 schema 제거. 같은 출력 재사용은 DB/호스트 시작 전 거부 |
| 비용 | 정책 누락/예약 불가/새 승인 불일치 거부. usage 누락의 첫 대역 요청 예약액을 유지해 다음 전송을 공통 잔액 부족으로 차단 |
| 정적 검사 | 변경 Python 7파일 AST·git diff --check 통과. ruff/mypy 미설치로 미실행, 별도 설정/의존성 추가 없음 |
| Docker | 검토된 이미지/실제 Docker opt-in이 없어 격리 파서 6개 skip. 실제 컨테이너 검증 통과로 보고하지 않음 |

첫 검증기에서는 `open` 래퍼의 `opener` 인자 충돌과 자체 정책의 로컬 테스트 UDS 제한 때문에 전체 789개에서 실패 9·오류 4·skip 6이 발생했다. 제품 코드를 완화하지 않고 검증기를 수정했다. 영향 검사 67개(61통과·6skip) 뒤 최종 전체를 새로 실행해 위 결과를 얻었다. 새 보존 테스트의 초기 2실패는 대역 응답 속성/파일명 참조를 수정한 뒤 통과했으며 초기 로그도 보존했다. 이전 실패나 부분 실행을 최종 성공으로 소급하지 않는다.

검사는 새 cluster `/private/tmp/bq4-6trozzq1/pg`, 새 사용자, 전용 Unix socket, 테스트별 새 schema로 수행했다. OS sandbox는 외부 네트워크·저장소 환경 파일·과거 artifacts 접근을 차단했다. 새로 소유한 DB/테스트 socket과 loopback HTTP만 허용했다. 공통 오류 검사의 비소유 로컬 UDS 접근 1건은 차단됐으며 실제 외부 연결·환경 파일 로드는 0이다. PostgreSQL 초기 공유 메모리 차단과 locale 시작 실패도 별도 로그에 남겼고 기존 서버로 대체하지 않았다.

## 보존·종료와 다음 경계

시작 파일 **104612개 SHA** 대조에서 기존 노트북 7개(체크포인트 포함), 원본 입력, 과거 실제 성공·실패를 포함한 실행 자료는 변경/삭제가 없었다. 기존 파일 중 이번 코드 2개·B 설계 문서만 달라졌으며 이후 B 작업 기록에 이 결과를 추가했다. 루트 `PROJECT_STATUS.md`와 공통 코드의 HEAD 일치를 별도로 확인했다. 기존 증거를 새 실제 성공으로 계산하지 않는다.

새 소유 서버 PID 39749를 종료했고 `pg_ctl status=3`, PID/pidfile/socket 부재를 확인했다. 기존 서버/DB/controller/settings는 사용하거나 종료하지 않았다. 검증 원문·실패·최종 결과·접수 원본·[종료 확인](../../artifacts/local/order_fulfillment/cq01_typed_20261029_0206/shutdown.json)은 Git 제외 `artifacts/local/order_fulfillment/cq01_typed_20261029_0206/`에 보존했다. 강제 Git 추가는 하지 않았다.

다음 경계는 R1~R4 공통 계약 검토, 특히 context/B 도메인 버전 의미의 확인과 나머지 6역할의 도구/인자/근거 지원이다. 실제 비용 자료와 새 승인·무료 gate의 유료 진입점 연결도 남았다. 노트북 간소화 후속 연결·실제 시연은 이번 작업에서 시작하지 않았다. 현재 노트북을 실행해도 실제 호출 차단은 유지되며, 실제 호출 승인/시연 순서를 안내할 준비 완료 단계가 아니다.

## 후속 검토와 수정

위 검증 이후 독립 업무 검사 실패의 성공 AgentResult 잔류와 대체 보존 실패 뒤 schema 삭제 문제를 확인했다. 사용자 승인에 따라 공통 실패 처리 재사용과 B 전용 조건부 삭제 보류로 보완했다. 이전 검사의 통과를 이 두 상황의 보장으로 해석하지 않는다. 새 회귀·전체 검증과 보존/종료 결과는 [후속 수정 보고서](B_CQ01_FAILURE_FIX_REPORT.md)를 따른다.
