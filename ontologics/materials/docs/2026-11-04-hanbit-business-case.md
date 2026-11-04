# 한빛시설관리 업무 자료 구현 계획

> **For agentic workers:** Use superpowers:executing-plans or subagent-driven-development. 사용자가 승인한 자료 생성 범위에서 진행하며 공통 코드와 다른 업무 화면은 수정하지 않는다.

**Goal:** 생산·자재 계획 화면에서 실제 업무 문서 형식의 계약·추가 요청·재고·구매 담당자를 한 흐름으로 검토한다.

**Architecture:** OLIMEX 기존 제품·BOM은 그대로 사용한다. 한빛시설관리 180대 계약과 독립 추가 60대를 기존 그래프 타입과 등록·검토·반영 경로로 연결한다. 업무 문서와 이름은 일반 업무 표현을 사용하고 `origin: synthetic` 및 가정은 메타데이터에 보존한다.

**Tech Stack:** 기존 Python/Host/PostgreSQL/Neo4j 및 생산·자재 계획 화면.

**Spec:** 이 채팅의 “진짜처럼 만들어줘” 요청과 기존 `2026-11-04-unified-workspace-plan.md`.

## 제약과 확인할 위험

- 공통 스키마·공통 실행 코드·다른 업무 화면 변경 없음.
- 현재 `a_contract_library`에는 활성 예약 1건과 입고 원장이 있다. 회사 소유자를 바꾸지 않고 별도 `materials_hanbit_*` 스키마에서 재현한다. 기존 8062 화면과 이력을 보존한다.
- 재실행으로 사용자가 추가한 재고·예약·전달 이력을 덮어쓰지 않는다.
- 기존 180대와 추가 60대는 별도 수요다. 기존 기능이 240대 합산 검토를 자동 수행한다고 표시하지 않는다.
- 실제 공급업체 로트·사용기한·실측 사용량·외부 메일 발송·생산 승인을 만들어내지 않는다.
- 새 그래프 자료는 기존 SourceDocument/Evidence/Contract/ContractItem만 사용하며 출처 해시와 원문 경로를 검증한다.

## 작업과 검증

- [x] `data/hanbit_facilities_20261104/`: 기존 계약 원문 그대로, 기준계획서, 추가 요청서/JSON, 구매 담당자 3명, 공정 재고 5종과 manifest 작성.
- [x] 같은 위치의 `bom_stock.json`: 실제 BOM의 확인된 품목만 재고와 연결. L/L1 공용 품목은 동일 로트 한 번만 계산.
- [x] `library_process_materials.py`: 기존 권한·감사·멱등 경로로 명시적 재고 관측 추가 저장. `tests/materials/test_process_stock_store.py`에서 기존 관측 보존·권한·오입력 검증.
- [x] `business_case_graph.py`: 기존 초안→다른 개발 검토자→반영으로 계약·계획과 추가 요청 등록. 동일 내용 재실행은 중복 없음, ID 충돌은 거부.
- [x] `business_case.py`: 전용 환경 설치/동일 웹 실행. 제품 카탈로그 초기화, 재고 서비스·원문 접수 서비스 재사용, 구매팀 연락처 설정. 기존 데이터 변경·혼합은 거부.
- [x] `tests/materials/test_business_case*.py`: 실제 PostgreSQL에서 설치→조회→180대/60대 계산, 반복 설치·기존 기록 보존 검사.
- [x] 관련 회귀와 전체 unittest, 실제 Chrome에서 계약 목록·요청 연결·제품별 재고·RFQ·구매 연락처 확인.

## 실행 및 결과

모델 해석은 기존 화면의 AI 해석을 사용한다. 설치는 계약 원문 접수까지만 수행하며 해석 결과나 사람 확인을 자동 생성하지 않는다.

전용 PostgreSQL 접속 설정을 로컬 환경 파일에 둔다. Neo4j를 사용하면 같은 파일 또는 별도 `--graph-env-file`에 기존 Neo4j 접속 설정과 전용 `ONTOLOGICS_GRAPH_NAMESPACE`를 지정한다. 비밀번호는 커밋하지 않는다. 다음은 저장소 루트에서 실행한다.

```sh
python -m ontologics.materials.business_case install --development \
  --schema materials_hanbit_20261104 --env-file .env.materials-hanbit.local \
  --graph-backend neo4j --graph-env-file .env.materials-hanbit.local

python -m ontologics.materials.business_case serve --development \
  --schema materials_hanbit_20261104 --env-file .env.materials-hanbit.local \
  --graph-backend neo4j --graph-env-file .env.materials-hanbit.local --port 8063
```

- 화면: `http://127.0.0.1:8063/#planning`. 기존 8062 실행 환경은 그대로 보존했다.
- 모델 해석은 기존 공통 `.env`/`.env.local` 또는 프로세스 환경의 모델·비용 한도 설정을 사용한다. 이 설치는 모델 호출을 실행하지 않는다.
- 구매팀 연락처와 발신자는 추적되는 bundle에서 읽는다. 별도 Microsoft 연결 없이 기존 local capture 전송을 사용한다. 실제 외부 이메일 발송을 의미하지 않는다.
- `MAT_REQUEST_*`가 설정된 다른 요청 저장소와 섞지 않는다. 이 환경은 계약·추가 요청·자재 계산에 동일한 저장소를 사용한다.
- 재실행은 같은 계약과 요청을 유지한다. 재고 변경·활성 예약·입고 원장이 생기면 초기 데이터 재설치를 거부하며 기존 데이터를 보존한다.
- Git에는 bundle·설치 코드·문서가 포함된다. PostgreSQL/Neo4j 데이터와 `data/receipts/`는 Git으로 전달되지 않으므로 발표자 환경에서 위 설치를 실행한다.

### 등록한 업무 자료

| 항목 | 내용 |
| --- | --- |
| 계약 | HFM-2026-1104-01 · 한빛시설관리 통합운영센터 증설용 제어보드 공급계약서 |
| 회사 | 구매자: 한빛시설관리 주식회사 / 공급자: 주식회사 온톨로직스 |
| 제품 | OLIMEX ESP32-POE Rev L WROOM |
| 기존 계약 | 180대, 생산 완료 11월 26일, 예정 출고 27일, 고객 도착 기한 30일 |
| 추가 요청 | MREQ-HFM-20261104-001 · 기존 구역과 다른 3개 구역 × 20대 = 60대, 11월 27일 생산 완료 요청 |
| 부품 재고 | 확인된 BOM 125행 → 공용 64품목, L/L1 동일 재고 매핑 61쌍 |
| 공정 재고 | 솔더 페이스트·실납·플럭스·IPA·솔더 바 5종, 기존 근거의 대당 사용량 사용 |
| 구매팀 | 김서윤 과장(견적 접수), 박준호 대리(전자부품), 이수진 대리(공정자재) |

### 확인한 결과

공통 제품 카탈로그 v1/460노드 → 계약·계획 v2/466노드 → 추가 요청 v3/468노드. PostgreSQL과 Neo4j의 같은 버전 내용이 일치한다. 새 typed 요청 스키마를 추가하지 않았으며, 기존 B의 SourceDocument/Evidence 요청 본문에 계약품목·구성·기준근거 ID를 기록한다. ContractItem의 기존 관계로 원 계약과 제품 구성에 연결된다.

Chrome에서 계약 목록·원문 해시·추가 요청 연결·60대 계산·RFQ·구매팀 연락처·첨부 준비를 확인했다. 메일 발송은 실행하지 않았다.

| 검토 조건 | 확인된 BOM 충족 | 확인된 BOM 부족 | 실장 미확인 제외 |
| --- | ---: | ---: | ---: |
| Rev L 180대 | 46 | 17 | 11 |
| Rev L1 180대 | 45 | 17 | 12 |
| Rev L 추가 60대 | 62 | 1 | 11 |

추가 60대의 RFQ는 BOM 부족 1종과 공정자재 부족 1종을 포함했다. 초기 여유율은 기존 화면의 10%이며 사용자가 변경할 수 있다. 180대와 60대는 별도 계산 결과로, 자동 합산이나 재고 예약을 한 것이 아니다.

시연 순서: **계약 검토 → 계약 열기 → AI 해석·원문 대조 → Rev L 연결 → 180대 계산 → RFQ 수정·구매팀 메일 준비**. 다음으로 **추가 생산 요청 → 60대 요청 내용 확인 → 기존 180대 계약 원문 확인 → 계산 → RFQ**를 보여준다. 제품별 자재 탭에서는 계약 접수 없이 Rev L/L1 재고를 비교할 수 있다.

### 최종 검증

- 새 공정 재고 저장 및 관련 회귀 51개 통과. 새 graph loader PostgreSQL/Neo4j 시험 5개, 업무 환경 설치 시험 2개 통과.
- 전체 unittest 1,776개 실행, 실패·오류 0, 기존 환경 조건에 따른 11개 제외(832.272초). 새 Hanbit 시험은 모두 실행됐다.
- 기존 공통 demo의 v1→v2→v3, U104 A 제외/B 포함 결과 유지. JavaScript 구문 검사와 `git diff --check` 통과.
- 실제 업무 환경 재설치 후 동일 원문 ID·그래프 v3·468노드 유지, 추가 노드 0. 기존 검토 결과는 보존됐다.
- 기존 8062는 A v1 / 요청 그래프 v5 / 요청 3건으로 계속 조회된다. 새 8063은 별도 v3 / 요청 1건이다.
- 구매 담당자 선택지의 반복적인 `(더미)` 접미사만 제거했다. 내부 origin·출처, 실제 메일 전송 방식, 공통 개발 환경 표시는 유지한다.
- 새 환경에서 원문을 모델로 해석하거나 사람 대조를 대신 완료하지 않았으며 메일도 보내지 않았다. 기존 UI로 사용자가 진행할 단계다.
