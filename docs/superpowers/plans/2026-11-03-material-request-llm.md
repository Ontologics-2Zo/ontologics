# 자재 검토 요청 LLM 연결 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 사용자 요청 원문을 LLM으로 해석하고 기존 데이터에 연결한 뒤 자재 요청 초안을 생성하여 기존 검토·반영·Neo4j 동기화 경로로 처리한다.

**Architecture:** 전용 B AgentSpec은 기존 lookup만 사용하고 구조화된 후보를 반환한다. B의 신뢰된 실행 코드가 후보·실제 조회 결과를 검증하고 prepare_request를 호출한다. 기존 CQ 실행기의 매 실행 새 DB 방식과 분리하여 지정된 DB를 사용하며, LLM에 승인 권한을 주지 않는다.

**Tech Stack:** 기존 Python AgentSpec/AgentTask/CommonHost/OpenAIProvider, PostgreSQL, Neo4j.

**Spec:** 사용자 대화의 추가 30대 사례와 ontologics/materials/docs/B_TO_A_MATERIALS_REQUEST.md. 이 계획은 구현 전 제안이며 운영 승인 정책을 새로 정의하지 않는다.

## Global Constraints

- 공통 코드·공통 스키마·T-box는 수정하지 않는다. 기능은 order_fulfillment 및 해당 examples/tests/docs 내부에 둔다.
- 자유 대화 UI와 A 자동 접수·자재 계산은 이번 범위에서 제외한다.
- 원문·모델 출력·등록 결과를 구분하고 실제 사용자 프롬프트 원문을 무변경 보존한다.
- 요청 ID·작성 주체·등록 시각·origin·재시도 키는 신뢰된 실행 코드가 결합한다.
- 날짜만 있는 입력에 임의 시간을 추가하지 않는다. 값 누락·제품/계획 모호함은 needs_confirmation으로 반환한다.
- 운영에서는 기존 승인 없이는 반영하지 않는다. 개발 시연의 별도 검토 신원은 운영 승인과 분리한다.

## Review Focus

- L/L1 등 유사 제품의 오연결: 실제 성공 조회 결과와 후보 ID를 대조한다.
- 원문에 없는 수량·기준일·조건 발명: 근거 구절 및 누락 검증, 확인 필요 상태로 중단한다.
- 반복 제출과 반영 후 동기화 실패: 같은 요청 키로 기존 결과를 회수하며 노드를 중복 생성하지 않는다.
- 원문 내 도구/승인 지시: 데이터로 취급하고 lookup 외 모델 도구를 허용하지 않는다.
- 해석 후 데이터 버전 변경: 초안 생성 직전 참조/개정 상태를 재확인하고 충돌을 명시한다.

## Task 1: 요청 해석 및 참조 확인

Files: 신규 ontologics/order_fulfillment/material_request_agent.py, tests/order_fulfillment/test_material_request_agent.py.

- [ ] 자유 원문 입력과 고정 데이터 버전을 받는 AgentTask, ready/needs_confirmation/invalid 출력 계약을 정의한다.
- [ ] 기존 AgentSpec/run_task/OpenAIProvider를 재사용한다. 허용 모델 도구는 lookup만이며 새 공통 도구를 만들지 않는다.
- [ ] 제품·계획·선택 계약/회차 ID는 성공한 조회 응답에 존재하고 업무 내용과 맞아야 한다. 기존과 동일 조건은 정확한 기준 자료를 연결한다.
- [ ] 단위 테스트: 30대/2026-11-20/production_complete 추출, L/L1 구분, 수량 누락, 잘못된 날짜, 여러 계획, 조작 ID, 원문 지시 주입. 실패를 먼저 재현하고 구현 후 통과시킨다.

## Task 2: 원문 보존과 등록 연결

Files: 신규 ontologics/order_fulfillment/material_request_workflow.py, 수정 material_request_document.py, material_request_registration.py; 신규 tests/order_fulfillment/test_material_request_workflow.py 및 기존 document/registration 테스트 확장.

- [ ] run_material_request(session, source_text, *, request_key, provider, config) 실행 진입점을 둔다. 모델 신뢰 필드와 서버 결합 필드를 분리한다.
- [ ] 원문과 해시를 요청 문서 보존 형식에 추가하되 기존 document-1 읽기·해시 검증을 유지한다. 필요한 경우 명시적 새 문서 형식 버전을 도입한다. 기존 보존 문서는 재작성하지 않는다.
- [ ] 검증된 ready 후보만 prepare_request로 넘긴다. needs_confirmation이면 초안·그래프를 변경하지 않고 부족 항목을 반환한다.
- [ ] 등록 결과는 draft_prepared/approved/applied/graph_synced를 구분한다. 기존 review/apply_approved를 재사용하고 운영 자동 승인을 추가하지 않는다.
- [ ] 테스트: 원문 보존, 기존 문서 호환, 후보 실패 시 무변경, 같은 키 재시도, 키 재사용 내용 충돌, 승인 전 반영 차단, 동기화 실패 후 중복 없는 재시도.

## Task 3: 실제 LLM 실행 및 끝까지 검증

Files: 신규 examples/order_fulfillment/b_material_request_llm.py, tests/order_fulfillment/test_material_request_llm.py; 수정 docs/order_fulfillment/README.md, docs/order_fulfillment/PROJECT_STATUS.md, ontologics/materials/docs/B_TO_A_MATERIALS_REQUEST.md, PROJECT_STATUS.md.

- [ ] 원문 파일 또는 표준 입력, 명시적 DB/그래프 설정, 요청 키, mock/openai 실행 방식, 기존 공개 비용 정책을 받는 전용 CLI를 만든다. 운영 신원을 임의 생성하지 않고 기존 identity 연결을 받는다. 개발 실행만 명시적인 개발 신원을 쓴다.
- [ ] 기존 b_run/CQ01~06/b_web의 고정 사례·독립 DB 의미는 바꾸지 않는다.
- [ ] mock으로 연결을 검증한 후 명시적으로 선택한 실제 LLM 실행에서 원문→조회→초안 결과를 검증한다. 실제 유료 호출 한도와 실행 기록을 남긴다.
- [ ] 격리 DB에서 기존 검토·반영 절차 후 Neo4j 조회를 검증한다: 요청 0→1, SourceDocument/Evidence 두 노드 증가, PostgreSQL/Neo4j 내용 일치, 같은 키 재실행 시 불변.
- [ ] 관련 unittest 및 저장소 요구 전체 회귀를 실행하고 실제 LLM 테스트와 대역 테스트 결과를 구분하여 기록한다.

## Handoff

현재는 계획만 작성했다. 코드·스키마·DB는 변경하지 않는다. UI 입력창 연결은 추후 별도 범위로 판단한다. 공통 등록/승인/동기화를 재구현하거나 우회하지 않는다.
