# B/main 호환 점검 및 CQ01~06 간소화 결과 — 2026-10-29

이번 작업의 실제 유료 API 요청은 **0회**다. 시작 시 `feature/b-first-mvp`는 clean, HEAD `f264955`, 원격 B보다 6커밋 앞이었다. 기존 B `4539c90`과 main `ca7c391`의 병합을 확인했다. commit·push·PR 생성은 하지 않았다. Git 무충돌 병합과 기능 호환 검증은 별개다.

현재 결과는 **무료 B 업무 검증 통과, 공통 연결 일부 보완, 전체 typed B 연결 및 실제 호출 준비는 검토 필요**다. 아래 두 계약 문제 때문에 실제 호출은 키 읽기·서버 준비 전에 차단된다. 무료 성공을 새 실제 LLM 성공으로 계산하지 않는다.

## 공통 연결 결과

README의 실제 Agent 담당 폴더/확장 연결 절과 `docs/AGENT_RUNTIME.md`를 기준으로 조사했다. 기본 네 Agent, 공통 서비스·입출력·권한·비용 계약은 변경하지 않았다.

기준 출처는 사용자가 이 대화에 전달한 팀장님 연결 규칙과 위 저장소 문서다. **Discord 원문을 직접 열람·대조한 검증은 수행하지 않았다.** 따라서 이 보고는 Discord 지시 전체의 준수 완료나 B의 `AgentSpec → 등록 → run_task` 전환 완료를 의미하지 않는다. 공통 typed 경로 시험에는 예제 `model_reader`를 사용했으며, 이것을 실제 B 역할의 전환 성공으로 계산하지 않는다.

| 항목 | 결과 |
| --- | --- |
| 고유 스키마·지침·흐름 | 기존 B 역할·고정 사례·업무 테스트 재사용 |
| B host/runtime | 공통 request budget, cost policy, sandbox client, identity lookup, 도구 관찰자 및 task 인자 전달 복구 |
| 누적 한도 | 직접 호출과 역할별 호출을 같은 요청 예산에 연결, 하위 예산·도구 범위 검증 유지 |
| Provider | 공통 `begin_paid_call` 예약·실사용량 정산·잔여 deadline 연결. 정책/한도 부족 시 전송 전 차단 |
| 승인·확정 저장·A/B·신원 | 공통 Service 상속 경로 재사용. 작성자 분리, 정확 draft/version/hash 승인, 승인자 권한 재확인 유지 |
| 격리 파서 | 공통 sandbox 설정 전달 복구. 실제 Docker 파서 컨테이너 검증은 아래 한계 참고 |
| AgentSpec → 등록 → request.run_task | 공통 도구만 사용하는 역할의 B host 연결을 별도 검증. 전체 B 역할은 아래 계약 검토 필요 |

별도 공통 검토 대상:

1. **`AGENT_SPEC_DOMAIN_REVIEW_REQUIRED`**: 공통 `AgentSpec`은 `AgentGateway.ALLOWED`만 허용한다. B의 7개 등록 중 접수는 공통 도구만 사용해 도구 등록 검사를 통과하지만, 나머지 6개에는 `lookup_many`, `delivery_round`, `fulfillment_status`, `production_completion`, `delivery_forecast`, `response_plan`, `compare_response_plan` 같은 도메인 도구가 있어 거부된다. 접수의 도구 허용을 typed 업무 어댑터 구현 완료로 계산하지 않는다. 기존 계약 안에서 전체 B 흐름을 그대로 연결할 수 없었다. allowlist 확대·검증 subclass·다른 도구 이름으로 숨기는 우회는 하지 않았다. 도메인 도구 권한, typed payload/EvidenceRefs 및 검증 범위를 공통 담당자와 정한 후 전환해야 한다. 현재 10대표 무료 업무 실행은 기존 B `run_agent` 경로다.
2. **`COST_AUTHORIZATION_REVIEW_REQUIRED`**: 기존 실행 승인의 ‘입력·금액 별도상한 없음’과 키만 남기는 설정 로더는 main의 비용 정책을 충족하지 않는다. 실행에 결합한 유한 비용 상한·검토된 모델 가격·공통 `CostPolicy` 전달·승인 내용을 함께 정해야 한다. 무료 HTTP 대역에만 가상 비용 단위를 명시했다. 이를 실제 가격이나 실제 지출 승인으로 사용하면 안 된다.

두 항목의 단순 차단 제거는 해결이 아니다. 검토 후 해당 계약 회귀와 새 무료 게이트를 다시 통과해야 한다.

## 보존 및 과거 결과

기존 3개 노트북의 편집·execution count·저장 출력은 바이트 단위로 보존했다. 새로운 [간소화 노트북](../../notebooks/CQ01_06_실제실행_간소화.ipynb)을 별도로 만들었다. `.gitignore`는 유지했고 notebook·artifact·비공개 문서를 강제로 추가하지 않았다.

최종 SHA 대조에서 변경 전 보존 목록 **6,673개**, 과거 실제 성공 증거 **235개**, 고정 입력 **37개**가 모두 일치했다. 기존 DB/settings/controller/과거 실행 디렉터리는 실행하지 않았고 이전 실패·유료 실행을 재개하지 않았다.

| 과거 실행 | 당시 결과 |
| --- | --- |
| Task10 첫 CQ01 | 실제 1요청 실패, 성공 0 |
| Task10 재실행 | CQ01 접수·약속 4+4요청 성공, CQ02 2요청 실패, 후속 미실행 |
| Task10 후속 별도 승인 | CQ02~06 8대표·32요청 성공. 앞선 CQ01 2대표와 합계 10대표이며 단일 전체 실행 성공은 아님 |
| Task11 | 무료 10대표·40대역 요청, 실제 API 0, 당시 고유 회귀 688 통과 |
| 이후 CQ04 실제 노트북 | 실제 4요청, 자동·사람 의미 검토·완료·종료 통과 |
| 이후 전체 실제 노트북 | CQ01 접수 실제 4요청·자동 검사 통과, 의미 검토 미제출 후 중단. 새 대표 완료 성공으로 계산하지 않음 |

과거 무료 실패/미완료 기록도 보존했다. 상세 실행 경로와 보존 기준은 [변경 전 계획](B_MAIN_COMPAT_PLAN.md), 이번 증거 폴더의 `preservation_before.json` 및 `final_preservation_static.json`에 있다.

## 간소화 내용 및 변경 파일

- `examples/order_fulfillment/b_notebook_ui.py`: 한 메뉴에서 사례 확인·무료 검사·승인·대표별 수동 검토·전체 결과·종료. 현재 대표/요청 수/다음 행동 자동 갱신. 모델의 한국어 응답과 수량·시각·조건을 먼저 표시하고 입력 원문·Python 해석·상세 JSON·도구 기록을 구분했다. 기본 전체 셀 실행은 비활성이다.
- `b_notebook.py`: 이번 세션에 결합한 짧은 승인 입력, 공통 연결 준비 상태 표시. 반복 셀 실행은 기존 세션을 유지한다. 승인·의미 검토·업무 검토를 자동 작성하지 않는다. CQ01 의미 검토와 정확 초안 업무 승인은 별도다.
- `b_run.py`, `b_run_transport.py`, `b_run_workflow.py`: 유료 준비 차단, 공통 비용의 무료 대역 연결, 공통 잔여 timeout 반영. 대표4/전체40, 고정 모델, 출력1024, 30초, 재시도0 유지.
- `b_run_isolation.py`: main의 하위 Agent 소스도 fingerprint·격리 읽기 범위에 포함. 오래된 무료 게이트 재사용 차단.
- `ontologics/order_fulfillment/{host,runtime,openai_provider}.py`: 위 공통 연결 누락 보완.
- `tests/order_fulfillment/test_main_{host_compat,provider_compat,live_gate,task_connection}.py`, `test_b_notebook_ui.py`: 새 경계 회귀. 기존 Provider 관련 테스트는 가상 비용 정책을 명시했고 기존 승인/전송 내부 시험은 별도 live 차단 시험과 구분했다.
- 이 보고·계획과 B 전용 `docs/order_fulfillment/PROJECT_STATUS.md`·개발 안내에 현 상태를 기록했다. 루트 `PROJECT_STATUS.md`에 이번 작업 중 추가했던 문단은 사용자 지시에 따라 제거해 HEAD와 바이트 단위로 일치시켰다. 이후 B 작업 기록은 B 전용 문서에만 남긴다. 새 의존성은 추가하지 않았다.

노트북은 기존 ignore 정책을 따른다. 저장 출력 없는 새 사본의 내용은 `b_notebook_ui.notebook_document()`라는 순수 함수로도 재현할 수 있다. 기존 사용자 노트북을 재생성 대상으로 쓰면 안 된다.

## 무료 검증

증거 기준 폴더: `artifacts/local/order_fulfillment/main_compat_20261029/`.

- `final_free/`: 새 cluster/user/socket/HOME/TMPDIR와 대표별 별도 schema. **10대표·40 HTTP 대역 요청**, 자동 검사·별도 모의 의미 검토·업무 불변성·승인·저장·한도·종료 통과. 현재 소스 fingerprint와 게이트 일치, 실제 API/키 읽기 0. 이는 모델 의미 품질에 대한 실제 검증이 아니다.
- `stop_verification.json`: 초기화 중인 별도 새 무료 실행의 중단·중복 시작 거부를 확인했다. `stop_running_verification.json`은 본 서버/캠페인이 시작된 이후의 별도 중단·서버 종료 검증이다. 실패 기록을 성공으로 덮어쓰지 않았다.
- `kernel_767fa599/verification.json`: 실제 ipykernel에서 기본 코드 3셀 통과. API·credential·worker 시작 0, 커널 종료 확인.
- `regression_23c95694/`: 전체 **798개 실행, 791개 통과·신규 가정 검사 1개 실패·Docker 관련 6개 skip**. 실패는 ‘접수를 포함한 모든 B 등록이 거부된다’는 잘못된 신규 검사 가정이었다. 접수는 공통 도구만 쓰는 것을 확인해 검사를 수정했다. `regression_5fdd781d/`의 **새 DB에서 관련 16개 통과(실패/오류/skip 0)**로 최종 수정과 UI를 검증했다. 합산해 고유 792개 통과·6개 환경 skip이며, 수정 후 전체 798개를 한 번에 다시 실행한 것으로 표현하지 않는다.
- 같은 후속 환경에서 `examples.cq_check`·`examples.agent_tools`·`ontologics.demo`를 이번 실행 전용 출력으로 통과했다. 실제 소유 DB 연결을 전체 회귀 1,541회·후속 8회 대조했고 credentials 읽기·외부 API 접근은 0이었다.
- 이번에 시작한 소유 서버 **7개 전부 종료**, `pg_ctl status=3`, PID/pidfile/socket 부재를 확인했다. 초기화 중 중단한 별도 1개는 본 서버를 시작하지 않았고 initdb가 미완성 디렉터리를 정리했다. 개별 `shutdown.json`과 `final_process_check.json`에 구분해 기록했다.
- 변경 Python 25개 AST/메모리 컴파일, `git diff --check` 통과. ruff/mypy/pyright는 설치되지 않아 해당 lint/typecheck는 미실행이다.

첫 보호 실행은 중첩 sandbox 제한으로 서버 시작 전에 실패했다. 앱 외부 실행 권한을 사용한 후속 검사에서도 자체 OS sandbox·깨끗한 환경·새 소유 DB 검증을 유지했다. 첫 전체 회귀는 검증기의 DB 반환 열 이름 충돌 및 긴 Unix socket 임시 경로 문제로 실패했다. 검증기를 수정하고 **새 환경**에서 다시 실행했으며 처음 실패 기록과 종료 증거는 보존했다. 첫 후속 검사16개는 통과했지만 예제 실행기의 argv 초기화 누락으로 CQ/demo CLI가 실행 전 거부됐다. 새 전용 출력 경로와 argv를 지정한 마지막 후속 실행에서 모두 통과했다. 초기 중단 검사는 initdb의 임시 pidfile을 본 서버 시작으로 오인해 초기화만 중단했다. 캠페인 시작 파일·실제 소유 PID를 확인하는 별도 새 중단 검사로 본 서버 종료를 구분했다.

실제 Docker 격리 통합은 Docker/검토된 이미지가 없어 실행하지 못했다. Jupyter 기본 셀은 실행했지만 최종 화면의 스크린샷 검증은 미실행이다. 실제 LLM 품질·실제 유료 승인 이후의 성공·운영 인증·배포·독립 설치는 이번 검증 범위 밖이다.

## 사용 순서

1. 새 `notebooks/CQ01_06_실제실행_간소화.ipynb`를 열고 저장소 `.venv` Python 커널을 선택한다.
2. 기본 `Run All`로 계획과 현재 차단 이유를 확인한다. 이 단계는 호출/서버를 시작하지 않는다.
3. 조작 셀의 `ENABLE_CONTROLS=True`를 직접 설정하고 실행한다. 메뉴 `1`로 사례, `2`로 새 무료 검사를 실행한다. 상태는 자동 갱신된다.
4. 메뉴 `4`로 전체 무료 결과와 서버 종료를 확인한다. 현재 메뉴 `3`은 두 공통 검토 항목을 표시하고 실제 호출을 시작하지 않는다.
5. 추후 공통 계약·비용 연결이 해결되고 회귀/새 무료 게이트가 통과한 코드에서만 이번 실행 승인 토큰을 직접 입력한다. 매 대표의 실제 응답·자동 검사·사람 한국어 의미 검토가 통과해야 다음으로 간다. CQ01은 정확 초안의 다른 검토자 업무 승인까지 별도로 진행한다.
6. `4`에서 전체 결과와 `server_stopped=true`를 확인하고 `종료`를 입력한다. 실패/중단 시 자동 재실행하지 않는다. 새 시연은 이전 종료 확인 후 커널을 재시작하고 무료 검사와 새 승인을 다시 거친다.
