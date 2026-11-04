# claim_response 첫 결과 (8-1 MVP 확인, 8-2 인계)

> 브랜치 `feature/c-first-mvp` · 2026-10-29 · 공통 그래프 변경은 PR #6(1차 6개 타입), PR #7(2차 3개 타입)
> 팀 개발 안내 10장 "동료가 추가 Agent의 첫 결과로 전달하면 되는 것" 4개 항목과 `C_기능_1차_MVP.md` 9장 "확인할 장면" 10개를 정리한다.

모든 데이터는 가상이다(`origin=synthetic`, 가상 회사 Demo Assembly Company). 실제 고장 원인·불량률·현장 조치를 검증한 것이 아니다.

## 1. 역할의 목적과 기존 Agent와의 접점

| 항목 | 내용 |
|---|---|
| 목적 | 메일·엑셀·CSV 클레임 → 보드·생산라인 추적 → 보관·후보 범위 → 생산 중단·출고 보류 요청서 → 테스트팀 의뢰서 → 테스트팀 결과로 재생산 수량 → A 전달용 요청서. 모든 요청서는 사람이 검토·송부한다 |
| 역할 이름 | `claim_response` (AgentSpec 1.0.0). 기본 네 Agent와 이름·도구가 겹치지 않는다 |
| 기존 Agent와의 접점 | Product·Quality가 쓰는 공통 그래프(`ProductUnit`, `Configuration`, `Evidence`)와 초안·검토·반영 경로를 함께 쓴다. Quality의 장착 이력 조사는 재사용 후보(4-5, 보류) |
| 요청 예시 | "확정된 클레임 CLM-DEMO-C-20261026-001을 보드와 생산라인까지 추적해줘" |
| 입력 | `intake_id`(사람이 확인한 `ClaimIntake`), `question`. 데이터 버전은 호스트가 고정 |
| 누락 시 | 확인되지 않은 클레임 → `AGENT_RESULT_REJECTED`, 없는 클레임 → `NOT_FOUND`. 변환 단계의 미연결 일련번호·수량 불일치·시간대 없음은 사람 확인 대상으로 남기고 추측하지 않는다 |

## 2. 사용한 공통 도구와 근거

| 항목 | 내용 |
|---|---|
| 허용 도구 (9개, 기존 도구만) | `lookup`, `search_evidence`, `investigate`, `preview_investigation`, `prepare_draft`, `get_draft`, `validate_draft`, `prepare_source_draft`, `get_source_receipt`. 승인·반영·내보내기 없음 |
| 실제 호출 조건 | 클레임 추적 작업에서 고정 버전의 `lookup` 3회: 클레임(`node_id`), `ProductUnit`, `UnitProductionRecord` |
| 결과의 근거 | 클레임, 연결된 보드, 그 보드의 생산 기록의 Evidence. 호스트가 이번 작업에서 실제로 읽은 것만 `evidence_refs`로 인정 |
| 데이터 버전 | 답의 `data_version`이 호스트가 고정한 버전과 다르면 `AGENT_RESULT_VERSION_MISMATCH` |
| 함수가 하는 일 | 형식 변환, 일련번호 연결, 라인 추적, 범위, 요청서 작성, 결과 읽기, 수량 계산. 모델에게 맡기지 않는다 |

## 3. 프롬프트·답변 처리와 대표 사례

**처리 방식**: 모델 답은 믿지 않고, 모델이 읽은 결과로 4단계 추적 함수를 다시 돌린 값과 같을 때만 인정한다(`ClaimTraceAgent.validate_result`). 모델 메시지에는 읽을 순서, `lookup` 인자 3개, 답 형식, 근거 규칙을 적는다.

**실제 모델 실행** (`gpt-5.4-mini-2026-03-17`, `run_demo --live-agent`, 2026-10-29)

| 실행 | 결과 | 원인 | 대응 |
|---|---|---|---|
| 1 | 실패 `AGENT_RESULT_REJECTED` | 클레임 조회에 `kind=ClaimIntake`를 덧붙임 | `node_id` 조회에 그 `kind`만 허용. 목록 조회는 정확히 일치해야 함 |
| 2 | 실패, 답 안 보임 | 공통 런타임은 거부된 답을 남기지 않음 | `RecordingProvider`로 `final_text`·`expected_from_reads` 보고 |
| 3 | 실패 | 읽은 근거 전부(보관·대체 재고·기존 데모)를 인용 | 근거 규칙 명시: 클레임, 연결된 보드, 그 보드의 생산 기록 |
| 4 | **성공** | 보드 5대, 라인 C-01, 근거 2개. 자동 검사 4/4 | 검사 기준은 완화하지 않음 |

**테스트 대역 사례** (`test_registration.py`, `test_run_demo.py`): 맞는 답 → 통과 / 보드 하나 빠진 답 → 거부 / 읽지 않은 근거 인용 → 거부 / 미확인 클레임 → 거부 / 비용 정책 없음 → 호출 전 `COST_POLICY_REQUIRED`

## 4. MVP 확인할 장면 10개 (8-1)

C 테스트 114개, 전체 회귀 통과(Linux, Docker sandbox 6개 skip). 윰 Windows에서 C 114개 통과.

| # | 확인할 장면 | 결과 | 근거 (테스트·실행) |
|---|---|---|---|
| 1 | 형식별 변환 | ✅ 메일·엑셀·CSV가 같은 클레임 번호·일련번호 5개·합계 5대·현상·시각, 원본 위치(행·셀·메일 줄) | `test_intake`: `test_three_formats_give_the_same_claim`, `test_locators_point_to_rows_cells_and_mail_lines`, `test_run_demo`: `test_mail_and_excel_claims_reach_the_same_result` |
| 2 | 변환 검토 | ✅ 미연결 일련번호·다른 구성·같은 번호 두 보드·수량 불일치는 사람 확인 대상 | `test_trace`: `test_unknown_duplicate_and_mismatched_boards_stay_open`, `test_quality_case_needs_a_person_and_resolved_serials`, `test_intake`: `test_quantity_serial_mismatch_is_a_warning_for_people`, `test_bad_quantities_and_serials_are_reported` |
| 3 | 클레임 추적 | ✅ 고객 보드 → C-01·생산 기록, 같은 라인 보관 120대, 납품 후보 75대 | `test_trace`: `test_claimed_boards_trace_to_line_c01_with_evidence`, `test_hold_scope_is_the_120_stored_boards_from_line_c01`, `test_candidate_scope_is_the_75_other_delivered_boards`. 실제 모델 추적도 같은 결과 |
| 4 | 요청서 검토 | ✅ 생산 중단·출고 보류 2종 생성, 사람 승인 전 미검토, 승인돼도 미송부·현장 미확인, Agent 승인 거부 | `test_requests`: `test_requests_wait_for_a_human_review_and_stay_unsent`, `test_review_labels_never_claim_sending` |
| 5 | 전수 테스트 | ✅ 의뢰 125대 → 테스트팀 결과·판정 → 재생산 수량 연결 (시나리오 1: 10대, 2: 16대) | `test_reproduction`: `test_mvp_scenarios`, `test_run_demo`: `test_confirmed_scenario_ends_with_an_unsent_request_for_a` |
| 6 | 증상 미재현 | ✅ 시나리오 3: 미정, 재생산 노드·A 요청서 없음(0으로 저장 안 함). 보류 120대는 보류 판정 그대로 | `test_reproduction`: `test_undetermined_is_not_stored_as_zero`, `test_undetermined_gets_no_document_and_no_node` |
| 7 | 표본 테스트 | ✅ 시나리오 4: 미검사 100대, 하한 2대, 요청서에 "최소 수량, 최종 수량으로 쓰지 않음" | `test_reproduction`: `test_mvp_scenarios`, `test_lower_bound_is_marked_as_a_minimum` |
| 8 | 중복 검사 | ✅ (방식 변경) 같은 의뢰에서 같은 보드 결과가 두 번 오면 `DUPLICATE_RESULT`로 막아 사람이 고친다. 그래프도 의뢰·보드당 결과 1개(`DUPLICATE_IDENTITY`) → 재생산 수요에 한 번만 반영 | `test_reproduction`: `BrokenResultTests`, `test_schema_phase2`: `test_one_result_per_requested_board` |
| 9 | A 인계 | ✅ 10대·16대 요청서와 산출 근거(고객 교체·보관 대체·대체 재고·계산식), 검토 대기·미송부 | `test_reproduction`: `test_confirmed_scenarios_become_requests_with_their_numbers` |
| 10 | 생산 이력 부족 | ✅ 기록 없는 보드는 `needs_production_history`, 두 개면 `conflict`. 라인을 정하지 않고 해당 보드를 표시. 생산 중단 범위는 `line_not_confirmed` | `test_trace`: `test_missing_or_double_history_is_never_guessed`, `test_requests`: `test_line_scope_needs_every_claimed_board_on_that_line` |

8번 설명: MVP 문서는 "여러 불합격 결과를 한 번만 반영"이다. 2026-10-29 회의로 결과·판정을 테스트팀이 돌려주게 되어, 같은 보드의 결과가 둘이면 어느 판정이 맞는지 C가 고르지 않고 사람에게 돌려보낸다. 결과적으로 한 보드는 한 번만 센다.

## 5. 현재 가능한 범위와 한계

| 구분 | 내용 |
|---|---|
| 가능 | 위 10개 장면. `python -m examples.claim_response.run_demo [--results scenario_1~4] [--live-agent]` |
| 임시 근거 | 보관·납품·대체 재고는 C 운영 기록(`basis: c_operations_record`). B 공통 재고·출하 타입이 생기면 `c_operations_*` 함수만 바꾼다(3-3) |
| 처리 못 하는 자료 | 정해진 세 양식 밖의 클레임, 이미지·OCR, 실제 메일함, 여러 구성이 섞인 클레임 |
| 처리 못 하는 질문 | 불량 원인 판정, 라인 재개 여부, 납품 75대의 회수 여부, 실제 생산·구매 실행 |
| 모델 사용 범위 | 클레임 추적 작업 1개만 실제 모델로 확인. 테스트 결과 설명·요청서 문장 생성은 하지 않음 |
| 필요한 공통 기능 | ① 웹 화면에서 C 역할 선택(`web.py`가 등록 목록 없이 호스트 생성) ② Supervisor → C 인계(7-3) ③ PR #6·#7 리뷰·병합 ④ Windows 전용 실패 확인: receipts `SOURCE_STORAGE_ERROR`, `test_sandbox` UnixStreamServer, `test_web` WinError 10053(가끔) |
