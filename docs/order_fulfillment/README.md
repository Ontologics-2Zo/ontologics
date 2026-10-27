# Order Fulfillment — B 개발 안내

공통 문서 [README.md](../../README.md)의 메인 기준(`7c5e2d9`)과 비교해 B 개발 중 추가·변경된 내용을 분리했다. 공통 문서는 해당 메인 원본으로 복원했다.

아래 테스트 수·실제 모델 실행·수락 여부는 원문에 기록된 당시 결과다. 현재 폴더 분리 이후의 전체 동작을 새로 검증했다는 뜻은 아니다. 실행 예제와 B 파일 경로는 현재 하위 폴더 위치로 표시한다. 이 문서의 과거 기록 이후 공통 Provider·Service·도구 정의를 B 하위 폴더로 분리했다. 현재 검증 상태와 남은 모델 연결 문제는 [B 작업 기록](PROJECT_STATUS.md)을 따른다.

B 문서: [작업 기록](PROJECT_STATUS.md) · [서비스 명세](SERVICE_SPEC.md) · [도구 계약](TOOLS.md) · [Agent 지침](AGENT_RUNTIME.md).

## 추가·변경 1

> 원문 위치: 문서 시작 (1–24행)

### Ontologics 공통 기반 v0.8.0 — PostgreSQL + pgvector

**최신 CQ06 Task10(2026-10-27) — 개발 범위 수락:** 새 전체586개·skip0·129.653초와 무료예제34개를 통과했다. Task07 설계28개를 고유 검사83개의 새 통과 로그와 대조하고 CQ06 예제5사례를 독립 정답과 확인했다. Task09 실제 불량/운송 각27/27·별도 의미 PASS는 현재 코드/자료/원문 해시 일치로 재사용했다. 이번 추가 LLM0회·$0, 업무코드변경0. CQ06 Task07~10의 명시 승인 입력·고정 가상 자료·읽기 Agent 개발 범위를 수락했다. 반복 신뢰도·서비스 연결·운영/전체MVP 수락은 별도다. 기존 작업·개인 Git 제외·브랜치/main 보존, push0.

**이전 CQ06 Task09 실제 검증(2026-10-27) — 두 대표 통과:** 공용 pattern 제거 코드·출력1,024 한도에서 불량 증가 새4단계와 운송 변경 새4단계가 각각 자동27/27·별도 의미 검토를 통과했다. 실제8/최대8회·과거응답재사용0·자동재시도0. 입력68,489(캐시32,128)·출력2,091토큰, 캐시 반영 표준단가 추정$0.03908985(청구미확정). 고정 가상 두 대표의 Task09 검증을 완료했으며 Task10은 진행하지 않았다. 반복 신뢰도·운영 수락·전체MVP 완료를 뜻하지 않는다. 기존 업무 코드·자료·키·개인 Git 제외와 main은 보존했다.

**이전 공용 코드·무료 검증(2026-10-27):** CQ06 최종 API 스키마의 문안4개에서 `pattern`만 제거했다. Python 기본 문안의 고정 `enum`·type·description·16필드와 로컬 길이/줄바꿈/문안/사실/근거/권한 검사는 유지한다. 관련44개·전체586개(skip0)·무료 예제34개 통과. 이번 실제 LLM 호출0회, 공용 입력·모델·출력1,024 한도는 그대로다. 이전 개인 진단의2,048 한도·과거 응답3개 재사용 성공과 구분하며, 수정 공용 코드의 새 실제4단계 검증·Task09 전체·Task10은 아직 미완료다.

**이전 실제 진단(2026-10-27) — pattern만 제거한 마지막1회 통과:** 원래 입력49,705바이트·모델·2,048출력 한도·enum·description을 유지하고 문안4개 pattern만 제거한 마지막 실제1회가 completed·자동27/27·별도 의미 검토를 통과했다. 과거 성공 응답3개는 재사용했으며 추가 호출/운송 대표/Task10은 진행하지 않았다. 입력14,023(캐시13,824)·출력696토큰, 캐시 반영 표준단가 추정$0.00431805(청구미확정). 이번 비교에서 pattern 제거 후 결과가 달라졌지만 API 내부 원인·반복 신뢰도·Task09 전체 통과는 확정하지 않는다. 업무 코드와 공개 기본 스키마는 변경하지 않았다.

**이전 무료 진단 — CQ06 스키마/입력 축소 후보 준비:** 실제 API·API 키 로드·DB 사용0으로 마지막 요청을 원래 SHA 그대로 재구성했다. 16필드와 양쪽 버전의 선택 근거 메타데이터를 보존한 진단 후보에서 입력49,705→9,928바이트, 전체 요청54,514→14,321바이트로 줄였다. 고정 enum4개는 기존 pattern을 모두 만족하며 로컬 모순은 발견하지 못했다. 중복 제약과 프롬프트1,024/API2,048 불일치를 확인했고49개 동치 경계 사례·독립 검토를 통과했다. 축소본은 비전송 진단용이며 원격 실패 원인은 미확정이다. 업무 코드/기본값은 그대로이고 Task09 전체·Task10은 미완료다.

**이전 실제 기록 — CQ06 출력2,048 최종 단계 진단 실패:** 불량 증가 사례의 마지막 출력 한도만1,024→2,048로 바꿔 실제1회 진단했지만 다시 incomplete/max_output_tokens·output=[]·API 보고 사용량0으로 실패했다. 과거 성공 응답3개를 원래 호출ID/내용으로 재사용했고 최종 요청의 입력·모델·스키마는 동일함을 SHA로 검사했다. 추가 호출·운송 대표·Task10은 진행하지 않았다. 사용량0 보고는 청구0이나 실제2,048토큰 소진의 증거가 아니다. 한도 증가만으로 이번 문제가 해결되지 않았으며 API 내부 원인은 미확정이다. 업무 코드/기존 기본 한도는 변경하지 않았고 개인 진단기에만2,048을 적용했다.

**이전 기록 — 출력1,024 실제 최종 응답 미완료:** CQ06 Task09 새 실제 재검증 실패로 중단. 불량 증가 대표의 처음3회 조회는 완료했지만 마지막 API가 max_output_tokens 사유의 incomplete·빈 output을 반환해 최종 JSON을 받지 못했다. 실제4/최대8회·자동재시도0, 운송 대표0회·Task10 미진행. API 보고 토큰 기준 비캐시 단가 추정 $0.01961325(청구미확정). 기존 무료586개·예제34개는 코드/자료 해시 일치로 재사용하고 관련44개·두 HTTP 대역 사전검사는 새로 통과했다. 원본과 이전 두 실패를 보존했다.

**이전 무료 보완 기록 — 무료 보완 완료·실제 재검증 대기:** CQ06 Task09의 Python 문안 무료 보완 완료. 전체 무료586개(skip0)·예제34개 통과. 이번 실제 API 0회·비용 $0. 변화 이유·기본 문안은 Python이 만들고 LLM은 조회·근거 확인 후 그대로 전달한다. 새 코드의 실제 대표 검증은 미실행이므로 Task09 최종 통과·Task10은 미완료다. 이전 두 실제 실패는 보존했다.

**이전 실제 재검증 기록 — CQ06 Task09 재검증 실패로 중단:** 전체 무료572개(skip0)·예제34개 통과, 새 실제4/최대8API·자동재시도0·비캐시 단가 추정$0.03163350(청구미확정). 실행한 대표의 원래 응답·별도 문장 검토와 이전 실패를 보존했다. Task10·반복신뢰도·전체MVP/운영 수락은 진행하지 않았다. 첫 실제 자동26/26·설명151자는 통과했지만 속도/누계 혼동과 미완결 회신으로 의미 불합격이다. 이후 추가 호출을 중단했다. 설명 예산60/80/45/35자를 마지막 JSON pattern과 평가기에 연결하고, 검토용 표시·조건부 표현·의미 검토를 유지했다. 이전 실패는 당시 기록으로 남긴다.

**이전 기록 — CQ06 Task09 첫 시도 실패:** 기존 CommonHost에 읽기 전용 `b_comparison`을 연결했고 전체 무료569개(skip0)·실행 예제34개를 통과했다. 실제 불량 증가 대표는4 API에서 수량·시간·버전·근거가 일치했지만 설명문 합계339자로 각100자/합계220자 조건에 실패했다(자동21/24). 운송 대표와 Task10은 진행하지 않았다. 입력35,184/출력1,158토큰, 비캐시 단가 추정$0.031599이며 청구는 미확정이다. 실패 답을 수정하거나 합격 기준을 완화하지 않았다. 반복 신뢰도·전체MVP 수락·운영 실행은 별도다.


현재 공통 계약은 v0.8.0/`graph-0.5`다. CQ06 Task08은 승인 자료 두 버전의 생산·도착 예상과 대응 초안을 비교하는 읽기 도구를 추가했다. 비교 규칙은 comparison-0.1이며 기존 예측 규칙·그래프 타입을 유지한다. CQ05 Task08에서 기존CQ04 결과를 읽어 고객 회신과 미출고 물량의 출고/운송 검토 초안을 만드는 공통 도구를 추가했다. CQ05 Task09는 형식 보완 후 같은 코드의 실제 기본·혼합 각 1번과 별도 의미 검토를 통과했다. Task10에서 새 전체 회귀·예제·설계와 보존을 확인해 CQ05 개발 범위를 수락했다. CQ01~04의 과거 실제 모델 기록은 보존하며, 계약/Service 코드가 바뀐 현 버전의 실제 응답 검증으로 재사용하지 않는다. 최신 검사 결과와 남은 범위는 [PROJECT_STATUS](PROJECT_STATUS.md)를 따른다.

## 추가·변경 2

> 원문 위치: Ontologics 공통 기반 v0.8.0 — PostgreSQL + pgvector (27–119행)

v0.4.0에서 추가한 B-CQ02의 완성 보드 재고·생산 작업·배정·운송과 6상태 현황 계약은 유지한다. v0.5.0은 `ProductionObservation`의 총생산·정상 완료·불량 누계와 공통 Python 계산을 추가한다. 회차에 전량 배정한 단일 작업의 최근 정상 완료 속도를 기준으로 생산 완료만 예상한다. 고객 도착 예상은 별도 CQ04 조회에서 제공하며 CQ02의 부분 완료 작업 미확인 제한도 유지한다. 기존 장착 이력·승인·PostgreSQL 계약과 `temporal-0.1`은 그대로고 예측 규칙은 별도 `production-0.1`이다.

CQ05는 Task07의 정의를 Task08 공통 함수와 기본 문안으로 구현했다. 실제 발송·신청 등록·운영 계획 변경은 범위 밖이다. CQ05 Task09는 초기 실패 기록을 보존하고, 추가 승인된 8회 호출로 형식 보완 후 실제 기본·혼합 대표를 재검증했다. Task10 개발 범위 수락도 완료했고 CQ06 Task07은 승인된 전후 입력의 재계산·비교 질문과 독립 정답을 정의했다. CQ06 Task08은 공통 비교 기능·독립 변경 자료·무료 검증을 완료했다. CQ06 Agent와 실제 LLM 검증은 Task09에서 진행하며 기존 CQ05 실제 응답을 새 계약의 검증으로 재사용하지 않는다.

#### B-CQ06 변경 전후 비교 예제

```bash
python -m examples.order_fulfillment.b_recalculation --case defects11 --output artifacts/local/b_cq06/example-01.json
```

LLM 없이 일회용 PostgreSQL 스키마에서 새 가상 입력의 초안·개발용 사람 검토 대역·승인 반영과 두 버전 비교를 실행한다. continued11/defects11/transport2h/combined11/zero_rate11은 각각 독립 사례다. 기본960/40과 비교해 정상11시는유지,불량증가는920/80,운송2시간은870/130이다. 전후전체초안·근거와후−전차이를보존하며null은0으로계산하지않는다. 실제발송/출고/운영승인은 아니다. CQ06 Agent 연결·실제 검증 상태는 위 최신 상태를 따른다. [도구계약](TOOLS.md#b-cq06-승인-자료-변경-전후-비교)을따른다.


CQ06 읽기 역할의 무료 실행:

```bash
python -m examples.order_fulfillment.b_host_agent --mode comparison --case defects11 --output artifacts/local/cq06_host_defects_new.json
python -m examples.order_fulfillment.b_host_agent --mode comparison --case transport2h --output artifacts/local/cq06_host_transport_new.json
```

Python이 지표별 변화 이유·완결된 기본 문안을 만들고 LLM은 그대로 전달한다. 문안의 자유 작성 역할을 없애고 동적 enum·로컬 일치 검사로 수정된 문장을 거부한다. 새 코드의 실제 대표 검증은 불량 대표의 최종 API 응답 미완료로 중단했다. CLI는 모델 대역만 사용한다. 비교 조회 → 변경 전 Evidence 묶음 → 변경 후 Evidence 묶음 → 최종 JSON의4단계이며, 기존 전후 초안과 변경 목록을 전달하고 원본 전체 결과·최종 답을 보존한다. 모델은 버전별 Evidence 메타데이터를 읽고 호스트는 전체 원문 사슬/SHA를 확인한다. [CQ06 Agent 계약](TOOLS.md#b-cq06-agent-연결과-평가)을 따른다. `--mode all`은 독립 가상 사례의 역할별 검사이며 CQ01부터CQ06까지 하나의 업무로 이어지는 전체 흐름 수락은 아니다.

#### B-CQ05 대응 초안 예제

```bash
python -m examples.order_fulfillment.b_response --output artifacts/local/cq05_normal_new.json
python -m examples.order_fulfillment.b_response --mixed --output artifacts/local/cq05_mixed_new.json
```

LLM 없이 일회용 PostgreSQL 스키마에서 공유 가상 자료를 개발용 검토 역할로 승인한 뒤 명시 버전을 조회한다. [response_plan 계약](TOOLS.md#b-cq05-대응-초안-조회), [독립 기대결과](../../data/order_fulfillment/b_response_expected.json)를 사용한다. 기본 기한내960/부족40은 현재확보600과 미래생산 예상400을 포함한 조건부 계산이며 새 출고 검토량은1000대다. 혼합은 이미도착100·실제운송중150을 새 출고에서 제외해750대만 검토한다. 고객 회신·신청 내용은 미발송·미등록·담당자 검토 초안이다. 미확인은null이며 과거 기한은 이행 검토로 반환한다. 선택 인용8/13개와 전체 무결성 근거12/17개를 구분한다. 예제는 운영 승인이나 CQ05 Agent 검증이 아니다.

#### B-CQ05 Agent 연결 예제

```bash
python -m examples.order_fulfillment.b_host_agent --mode response --case normal --output artifacts/local/cq05_host_normal_new.json
python -m examples.order_fulfillment.b_host_agent --mode response --case mixed --output artifacts/local/cq05_host_mixed_new.json
```

위 CLI는 무료 대역 전용이다. b_response는 response_plan→선택Evidence묶음→SourceDocument묶음→최종JSON을4단계로 수행한다. 원래 도구 결과·최종 답을 보존한다. 마지막 출력에만19키 strict JSON 형식을 사용하며 사실/의미 검증은 별도다. [연결 계약](TOOLS.md#b-cq05-agent-연결과-평가)을 따른다. CQ05 Task07~10 개발 범위 수락: 명시 승인 버전의 고정 가상 자료에서 대응 초안의 수량·시간·조건·근거와 읽기 권한을 확인했다. 새 전체 502개·skip 0·105.625초, 관련 82개와 무료 예제 24개 통과. 설계 36개를 고유 검사 83개와 대조했으며 D26·D34의 확장 검증은 부분 확인으로 남긴다. Task09 실제 기본·혼합 각 25/25·별도 의미 검토는 현재 코드/원본 해시 일치로 재사용했다. 이번 추가 API 0회·비용 $0. 고객 문장 단독 표시·반복 신뢰도·운영 성공·전체 MVP는 미수락이다. 다음은 CQ06 Task07 정의이며 미착수다. 기존 작업·원문·정답·키·개인 Git 제외·실패 기록·브랜치/main 보존, fetch/commit/push 0.

#### B-CQ04 고객 도착 예상 예제

```bash
python -m examples.order_fulfillment.b_delivery --output artifacts/local/cq04_base_new.json
python -m examples.order_fulfillment.b_delivery --mixed --output artifacts/local/cq04_mixed_new.json
```

각각 새 PostgreSQL 스키마에서 가상 조건·자료의 초안/개발용 사람 검토 대역/반영 후 명시 승인 버전을 조회한다. API 키와 LLM 호출은 없다. 기본 분할 출고 사례는 **기한 내960대·부족40대·전량 도착 예상15:26:40**, 별도 혼합 사례는 **1,000대·부족0대·14:20**이다. 출고 준비/운송/방식과 최근 추세·처리 능력 가정에 따른 예측이며 실제 출하·고객 도착 사실을 만들지 않는다. 조건/관측/확인 범위 부족은null과 이유를 반환한다.

[공통 계약](TOOLS.md#b-cq04-조건부-고객-도착-예상-조회), [기본 정답](../../data/order_fulfillment/b_delivery_expected.json), [혼합 정답](../../data/order_fulfillment/b_delivery_mixed_expected.json)을 따른다. 기존CQ03의 단일 전량 배정 범위와CQ02의 부분 완료 미지원은 유지한다. CQ04 Task09에서 Agent 연결과 실제 기본·혼합 대표 검증을 진행했다. Task10에서 전체448개·설계36개·예제20개를 확인해 위 개발 범위를 수락했다. 실제 모델 반복 신뢰도·운영 납품 보증은 별도다.

```bash
# 대역 모델로 실행하며 API 비용이 없다. 출력 파일은 새 경로를 쓴다.
python -m examples.order_fulfillment.b_host_agent --mode delivery --case normal --output artifacts/local/b_cq04_host_normal_new.json
python -m examples.order_fulfillment.b_host_agent --mode delivery --case mixed --output artifacts/local/b_cq04_host_mixed_new.json
```

[Agent 계약](TOOLS.md#b-cq04-agent-연결과-평가)은 계산→근거 전체 묶음→원문 정보 전체 묶음→최종 JSON으로 최대4모델응답을 사용한다. 수치·조건·정확한 인용과 자유문 의미 검토를 구분한다. 실제 기본은960/40/15:26:40, 혼합은1,000/0/14:20과 정확한8개/13개 인용을 확인했다. 원문 정보 조회 누락으로 실패한 두 기록도 보존한다. 총4시도/API14회, 비캐시 단가의 토큰 기준 추정$0.04743525이며 실제 청구액은 미확정이다. 기본 답의 “전량 도착 예상은 아닙니다”는 기한 내 범위로 읽히지만 단독 인용하면 모호하다는 한계가 있다. 실제 도착 보증·실모델 예외/반복 신뢰도·웹 연결·운영 검증은 별도다.

#### B-CQ03 생산 완료 예상 예제

```bash
python -m examples.order_fulfillment.b_production --output artifacts/local/b_cq03_task08/example.json
```

LLM 없이 새 PostgreSQL 스키마에서 가정 생산 실적을 초안·검증·개발용 사람 검토 대역·반영한 뒤 명시 버전으로 조회한다.09시 정상510대→10시600대의 최근 속도90대/시간과 목표1000대로부터 잔여400대·16000초·**2026-10-27T14:26:40+09:00**를 계산한다. 자료 부족·오래된 현황·속도0은 완료 예상null로 구분한다. 목표 도달 관측은 최초 실제 완료 시각이나 창고 입고가 아니다.

[실행·자료 계약](TOOLS.md#b-cq03-생산-완료-예상-조회), [고정 원문](../../data/order_fulfillment/sources/b_demo_production_01.md), [독립 정답](../../data/order_fulfillment/b_production_expected.json)을 따른다. 예제는 승인 경계·시점·근거·기존CQ02 제한을 포함한14개 검사를 제공한다. 이 명령 자체는 공통 도구 검사다. 아래 Agent 경로와 실제 업무 승인은 구별한다.

CQ03 Agent를 기존 CommonHost에 연결한 무료 예제:

```bash
python -m examples.order_fulfillment.b_host_agent --mode production --case normal --output artifacts/local/b_cq03_host_new.json
```

`--case missing_latest/zero_rate/target_observed`도 무료 대역으로 확인한다. [역할/근거/평가 계약](TOOLS.md#b-cq03-agent-연결과-평가)은 생산 조회→Evidence묶음→SourceDocument묶음→최종 JSON의 최대4모델응답을 사용한다. 실제 모델 정상 대표1건은 실패2번 보완 뒤3차19/19·별도 의미 검토를 통과했다. 모델이 원문 전체를 읽었다는 뜻은 아니며 실제완료/도착/납품가능성을 확정하지 않는다. 공용 CLI는 유료 API를 호출하지 않는다. Task10에서24개 설계 대조·전체361개·예제10개를 확인해 위 개발 범위를 수락했다. 추가API0회다. CQ04는 별도Task08에서 도착 조건·기본/혼합 계산을 구현했으며 Task09에서 전용 읽기 역할과 기본·혼합 실제 대표 검증을 연결했으며 Task10 개발 범위 수락도 완료했다. CQ05 Task07~10을 고정 가상 자료의 개발 범위에서 수락했고 CQ06은 미착수다. CQ03 실제 모델 성공을CQ04 모델 검증으로 재사용하지 않는다.

#### B-CQ02 물량 현황 예제

```bash
python -m examples.order_fulfillment.b_fulfillment --output artifacts/local/b_cq02_task08/example.json
```

LLM 없이 새 PostgreSQL 스키마에서 초안·검증·개발용 사람 검토 대역·반영·버전별 조회를 실행한다. 가상 B-01 1회차의 10시 현황은 납품 완료100·운송 중150·창고250·생산 중300·준비 완료100·대기100대다. 잔여 약속900대와 미배정0대를 구분한다. 자료가 없으면 0 대신 미확인으로 반환한다. [자료 규칙·도구·제한](TOOLS.md#b-cq02-물량-현황-조회)을 따른다. 이 예제는 실제 업무 승인이나 Agent 모델 검증이 아니다.

현황 Agent 연결은 기본적으로 API 없는 대역 실행이다.

```bash
python -m examples.order_fulfillment.b_fulfillment_agent --provider scripted --output artifacts/local/b_cq02_task09/scripted-new.json
```

실제 호출은 같은 명령에 `--provider openai`와 새 출력 경로를 명시한다. 고정 모델·정상 사례 하나·최대 API4회·재시도 없음·요청당 출력1024토큰으로 제한한다. 원문/근거를 읽는 모델 설명을 검사하며 수량은 공통 도구가 계산한다. 이전 보고서는 덮어쓰지 않는다. [연결·평가 계약](TOOLS.md#b-cq02-agent-연결과-평가)을 따른다.

## 추가·변경 3

> 원문 위치: Ontologics 공통 기반 v0.8.0 — PostgreSQL + pgvector / 확인할 결과 (166–196행)

#### B-CQ01 계약 약속 확인

```bash
python -m examples.order_fulfillment.b_contract
```

새 `b_cq01_<UUID>` PostgreSQL 스키마에서 기존 실제 설계 시드 → 가정 계약 초안·검증 → 승인 전 반영 차단 → 개발용 사람 역할 검토 → 버전 2 등록 → 약속·근거 조회를 실행한다. 모델 키 없이 공통 함수를 직접 호출하는 예제이며 실제 운영 승인이나 LLM 실행이 아니다.

기대 답은 가상고객사 A에 ESP32-POE Rev L WROOM 보드 1000대를 `2026-10-27T15:00:00+09:00`까지 고객 창고에 도착시키는 약속이다. 현재 이행 가능성은 `fulfillment_assessed=false`다. 해시가 고정된 원문과 근거 메타데이터, 독립 정답은 [TOOLS](TOOLS.md#b-cq01-계약-접수와-약속-조회)에 연결한다. 수량·기한 누락/모호함은 보완, 잘못된 숫자/참조는 검증 실패로 처리한다.

보고서는 Git 제외 `artifacts/local/task08/b_cq01.json`에 남는다. 필요할 때만 `python -m examples.order_fulfillment.b_contract --export-graph`로 [B 대표 그래프](../../data/order_fulfillment/b_contract_graph.jsonld)와 공통 T-box를 재생성한다. 기존 `data/initial_graph.jsonld`·OLIMEX 원본·부품 입출고 예제는 보존한다. 첫 추출 형식은 고정된 Markdown 표의 항목 1개/회차 1개이며 범용 계약서 파서는 아니다.

B 역할 연결 예제는 다음과 같다. 기존 런타임·provider를 재사용하며 별도 서버가 필요하지 않다.

```bash
python -m examples.order_fulfillment.b_agent --provider scripted
python -m examples.order_fulfillment.b_agent --provider openai --output artifacts/local/task09/b_agent_live.json
```

기본 `scripted`는 연결 시험용 대역이다. 실제 LLM 품질을 검증하지 않는다. `openai`는 로컬 키를 검증한 뒤 기존 고정 모델을 실제 호출한다. `--mode intake`는 원문·기존 구성·근거 조회 → 계약 3노드 초안 → 검증 → 사람 검토 대기, `--mode promise`는 개발용 호스트가 미리 확정한 자료의 회차 → 약속·근거·미확인 설명이다. `both`가 기본이며 두 모드는 별도 새 DB 스키마를 사용한다. 모델 초안을 자동 승인하지 않는다.

결과는 Git 제외 `artifacts/local/task09/b_agent.json`에 기록된다(`--output`으로 변경 가능). 실모델 실행에는 별도 경로를 권장한다. 키 누락은 DB 접수·API 호출 전에 `OPENAI_KEY_MISSING`으로 중단하며 대역 통과와 실모델 완료를 구별한다. 자동 평가는 원문 파서·독립 정답·실제 도구 기록·저장된 초안을 대조한다. 자유문 설명 전체와 임의 계약 PDF/OCR의 품질을 보증하지 않는다. 모드별 도구 권한과 입력/출력은 [TOOLS](TOOLS.md#b-agent-연결-예제-task-09)를 따른다.

새 CommonHost를 통한 B 연결 예제는 다음과 같다. 매 실행마다 새 개발 스키마를 사용하며 보고서 경로는 새 파일이어야 한다.

```bash
python -m examples.order_fulfillment.b_host_agent --mode all --env-file .env.local --output artifacts/local/b_host_first.json
```

[b_host_agent.py](../../examples/order_fulfillment/b_host_agent.py)는 기존 지침·도구·평가기를 `b_intake`·`b_promise`·`b_fulfillment`·`b_production`·`b_delivery`·`b_response`·`b_comparison`으로 등록한다. 개발 사용자의 요청 신원으로 실행·초안 작성자를 기록하고, CQ02의 정확한 근거 인용 검사를 유지한다. 이 명령은 **대역 모델 전용이며 유료 API를 호출하지 않는다.** 기존 standalone 예제도 유지한다. 실제 로그인·B 웹 메뉴/Supervisor 인계와 예외/반복 신뢰도는 별도 확인이 필요하다. 새 호스트의 실제 정상 대표 사례는 CQ01 접수/초안 작성10회·14/14·미승인 초안, CQ01 약속 조회10회·14/14·전체5개 근거 보존, CQ02 현황 조회4회·17/17·정확11개 인용이며 각각 별도 의미 검토도 통과했다. 범용 계약 파싱/OCR·운영 승인 성공은 검증 범위 밖이다. 범위와 비용은 [PROJECT_STATUS](PROJECT_STATUS.md)를 따른다. 위 CLI는 계속 대역 전용이다. 등록 방식과 권한은 [TOOLS](TOOLS.md#b-역할의-commonhost-연결)를 따른다.

## 추가·변경 4

> 원문 위치: Ontologics 공통 기반 v0.8.0 — PostgreSQL + pgvector / 동료의 확장 Agent 연결 (302–303행)

B의 CQ01·CQ02·CQ03·CQ04는 [b_host_agent.py](../../examples/order_fulfillment/b_host_agent.py)의 `REGISTRATIONS`로 이 접점을 사용한다. 기존 기본 네 역할을 교체하지 않으며 B 웹 라우팅은 아직 연결하지 않았다.

## 추가·변경 5

> 원문 위치: Ontologics 공통 기반 v0.8.0 — PostgreSQL + pgvector / 저장소 공유 상태 (306–306행)

비공개 저장소는 [Ontologics-2Zo/ontologics](https://github.com/Ontologics-2Zo/ontologics), clone 주소는 `https://github.com/Ontologics-2Zo/ontologics.git`이다. PostgreSQL 전환 PR #1·목표/완료 기준 PR #2·S1/S2 및 핵심 Agent 통합 PR #3은 가져온 main 이력에 포함되어 있다. 2026-10-27 로컬에서 가져온 기준은 `7c5e2d9`다. 이전 `codex/s1-s2-service`의 기준은 `189500e`였으며 해당 브랜치의 공통 호스트·S1·S2·네 Agent 변경이 이번 main에 들어왔다. 원격 CI 성공 여부는 해당 PR/커밋 checks에서 별도로 확인한다. 실제 인증·운영 백업·AWS·RDF/Neo4j 완료를 의미하지 않는다.

## 추가·변경 6

> 원문 위치: Ontologics 공통 기반 v0.8.0 — PostgreSQL + pgvector / 저장소 공유 상태 (308–308행)

현재 B 작업은 `feature/b-first-mvp`의 보존 커밋 `f808b69`를 남긴 뒤 위 main을 병합했다. 기존 작업을 보존한 상태에서 공통/CQ01·CQ02 회귀와 새 호스트의 제한된 대표 검증을 수행했으며, 세 B 경로의 실제 정상 대표 검증을 갖췄다. CQ03의 정의 재점검 뒤 공통 생산 실적/예측 도구를 추가했고, Task09 전용 읽기 Agent·호스트 연결과 실제 정상 대표 검증까지 통과했으며 Task10 최종 점검과 개발 범위 수락까지 완료했다. CQ04 Task07의 도착/출고/운송 조건과 기본·별도 혼합 정답 정의도 완료했다. CQ04 Task08 공통 조건 계약과 계산 구현도 완료했다. CQ04 Task09도 읽기 Agent·근거 설명·실제 기본/혼합 대표 검증을 완료했다. CQ04 Task10도 전체448개·설계36개·예제20개와 원본 보존을 확인해 위 개발 범위를 수락했다. CQ05 Task07~10은 제한된 개발 범위에서 수락했고 CQ06 정의는 아직이다. 반복 신뢰도 검사는 후속으로 남긴다. 브랜치를 받았다는 사실이나 충돌을 해결했다는 사실을 업무 검증 통과로 계산하지 않는다. 실행 기준은 `git rev-parse HEAD`, 검증 범위와 남은 작업은 [PROJECT_STATUS](PROJECT_STATUS.md)로 확인한다.
