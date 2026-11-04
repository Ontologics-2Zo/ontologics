# RFQ purchasing handoff implementation plan

> 이 문서는 먼저 구현한 **외부 메일 발송 후 수동 증빙 등록**의 이력이다. 현재 주 화면은 [자동 메일 작성·전달·보관](2026-11-04-rfq-mail-plan.md) 흐름으로 대체한다. 아래 수동 기록의 조회·그래프 등록 호환성은 유지한다. 이 문서의 과거 테스트 결과를 새 메일 기능의 검증 결과로 사용하지 않는다.

## 구현된 사용 흐름 (2026-11-04)

생산·자재 계획의 RFQ 결과에서 **구매팀 전달·기록 →**을 연다. 수신자·제목·본문을 준비하고 RFQ 파일을 내려받아 사용 중인 메일에서 전달한다. 보낸 시각과 `.eml`/`.txt` 증빙, 실제/가상 구분을 입력하고 확인하면 기존 공통 등록안이 만들어진다. 이 기록은 담당자 제출 증빙이며 메일 서비스의 발송·수신 확인이 아니다.

등록안은 기존 검토 화면에서 다른 사용자가 검토·승인한다. 화면은 승인 대기, 승인 후 미반영, 정확한 버전의 그래프 반영 확인, 조회 실패를 구분한다. 저장 완료 후 입력 폼은 접히며 증빙·등록 결과가 먼저 표시된다. 내려받기는 기존 원본 제공 경로에서 해시를 확인한다.

같은 영역의 **구매팀 전달 기록** 탭에서 최신순 10건씩 조회한다. 그래프 승인으로 제품 연결 기준 버전이 바뀌거나 새 RFQ를 계산해도 이전 증빙을 조회하기 위해 재연결·재계산할 필요가 없다. 읽기 권한이 있는 사용자도 조회할 수 있다. 그래프 등록 전 실패한 기록은 원래 등록자가 **보관한 증빙으로 그래프 등록 재시도**를 누른다. 메일을 다시 보내지 않는다.

새 UI는 `ontologics/static/planning.js`의 A 영역에 한정한다. 처리와 기록 형식은 `materials/rfq_handoff.py`, `materials/rfq_handoff_document.py`, `materials/workspace.py`에 있다. 공통 코드·공통 스키마·T-box·다른 업무 화면·계산기·DB 테이블은 변경하지 않았다. 기존 미커밋 수량/제품 연결 UI 변경을 보존했다.

검증 근거: 백엔드·HTTP·기존 workspace/request 35개 통과 후, 독립 기록 조회 추가분을 포함한 handoff/HTTP 19개를 다시 통과했다. 실제 Chrome + 격리 PostgreSQL에서 계약 180대의 RFQ → 잘못된 시각 거부/수정 → 증빙 등록 → 원본 다운로드 바이트 일치 → 새로고침 → 다른 사용자 승인 → 그래프 반영 재조회 → 독립 기록 탭 조회를 확인했다. 모바일 가로 넘침 및 브라우저 오류도 없었다. 계약 해석만 모델 대역이며 유료 모델 호출·메일 발송·사용자 계약·재고 변경은 없다. 로컬 8062 서비스는 같은 DB/schema/graph 설정으로 재시작하고 읽기 전용 화면 점검을 통과했다.

전체 회귀는 1,729개 실행, 실패/오류 0, 환경 조건 skip 11로 완료했다(734.980초). 전체 실행은 독립 기록 조회 추가 전 모듈을 로드했으며, 후속 조회 변경은 위 19개 회귀와 최종 브라우저 흐름으로 별도 검증했다. Python 컴파일·JS 구문·diff 공백 검사와 독립 코드 검토도 통과했다. 마지막 브라우저 테스트 스키마와 테스트 증빙은 서버 종료 시 정리했다. 로그와 화면 캡처는 `/private/tmp/rfq-handoff-*`에만 보관하며 실제 원문·설정·키를 커밋하지 않는다.

> For agentic workers: use subagent-driven-development or executing-plans. User approved this scope in the conversation; implement without another approval round.

**Goal:** Add manual purchasing-team handoff preparation and evidence registration to the production/materials planning RFQ screen.

**Architecture:** Preserve immutable calculation records. An A-only adapter reads saved workspace/request RFQs, preserves a receipt and attachment, and uses existing SourceDocument/Evidence and common draft/review/apply/lookup. No email sending, new ontology schema, common routing changes, or additional database tables.

**Tech stack:** Existing Python, PostgreSQL, common graph tools and vanilla planning.js.

**Spec:** User-approved file/scope plan in this conversation: RFQ -> manual email -> evidence registration -> separate graph approval/status. Only the production/materials planning UI changes.

## Constraints and review focus

- Preserve existing planning.js edits and RFQ quantities/exclusions; no changes to other screens, common code, ontology schema/T-box or order_fulfillment.
- No paid model calls, mail transmission, reservation/consumption or purchase execution.
- Authenticate through the existing host; do not trust caller actor, table name, RFQ body or hash. Read saved records with a source_kind allowlist.
- Registration is a user-reported handoff, not provider-verified send/receipt. Actual and synthetic evidence origins remain explicit and separate.
- A request RFQ uses pp_request_reviews; workspace/contract RFQ uses pp_workspace_material_reviews. Both must work.
- Freeze RFQ/receipt bytes; a later calculation never changes prior handoff. Same-key retries must preserve bytes and reject changed input.
- Graph approval and graph visibility are separate; incomplete apply/lookup must not display applied or prompt email resend.
- Files are runtime artifacts under ignored data/receipts/materials/rfq_handoffs, not committed mail contents.

## Task 1 — A backend and registration tests

Files: new materials/rfq_handoff.py, materials/rfq_handoff_document.py; modify materials/workspace.py; new tests/materials/test_rfq_handoff.py and extend test_workspace_http.py.

- [x] Add and run failing tests for an RFQ -> common draft -> other-user approval -> graph readback cycle.
- [x] Implement RFQHandoff(host).prepare(context, source_kind, review_id, recipient, subject, message, sent_at, evidence_filename, evidence_base64, evidence_origin, confirmed, request_key).
- [x] Expose action rfq-handoff-prepare with exactly those fields. source_kind is workspace or request. Evidence accepts .eml/.txt, at most 256 KiB. evidence_origin is real or synthetic; confirmed must be true. sent_at must include timezone, be after review and not future. Recipient is one email address, no newline; subject/message bounded.
- [x] Expose rfq-handoff-status(source_kind, review_id): data.items list of immutable receipts. Each item has recipient, subject, sent_at, reported_by, evidence_filename, evidence_sha256, rfq_sha256, graph_status and draft (common response data or null). Prepare returns data.item plus data.draft (full common response, if available); status items contain draft data with draft_id/draft_version.
- [x] Add owner-only rfq-handoff-retry(source_kind, review_id, receipt_id), reconstructing only the server-preserved input. Add read-only rfq-handoff-history(offset), 10 latest records per page, independent of stale product links and new calculation IDs.
- [x] Preserve bytes before common prepare. Registration retry uses same input/key. A failure retains receipt for retry without a new email. Status values: file_saved, pending_review, changes_requested, rejected, approved_pending_apply, applied, lookup_failed. Never classify approved alone as graph-applied.
- [x] Use SourceDocument/Evidence only; receipt description records validated RFQ identifiers/hash and references. Do not attach cross-origin evidence or invent typed contract IDs from archive IDs.
- [x] Test permissions, unsupported kind/fields, empty shortages, exact bytes, different key/body handling, both RFQ sources, synthetic/real separation, pending/rejected/applied, failure/retry. Reuse existing common graph status and approval tools.

## Task 2 — A-only UI

File: ontologics/static/planning.js.

- [x] Add a purchasing handoff area below RFQ with recipient, editable suggested subject/body, copy and existing RFQ download. Preserve the existing draft text.
- [x] Add explicit manual evidence entry with sent_at, attachment, origin and confirmation; no send button or automatic send-success assertion.
- [x] Bind actions to source_kind/review_id from saved results, not arbitrary source data. Reuse common showBusinessDraft for review navigation.
- [x] Display handoff and graph statuses separately; retain input and request key on failure; refresh records on revisiting RFQ or explicit refresh.
- [x] Add a scoped 구매팀 전달 기록 tab, reusing the same record renderer and exact-version source downloads. Collapse the submitted form so saved results remain visible.
- [x] Verify form/permissions/no-data/stale-result behavior and actual browser registration/refresh on isolated data.

## Task 3 — Verification and scoped documentation

- [x] Review changes independently against this scope; fix material findings.
- [x] Run A tests, existing common HTTP/registration tests, JS syntax/diff checks and required full unittest discovery. Record skips/failures accurately.
- [x] Document completed behavior, limits and test evidence in this A-owned document. Keep root/common documentation unchanged per user scope.
- [x] Refresh the running local planning page after safe service restart if needed. Do not modify user business records or send mail during testing.
