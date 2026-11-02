# 실제 치수를 이용한 공정 자재 대당 기준 보완

사용자가 요청한 실제 치수 기반 보완을 수행한다. 변경은 materials 안으로 한정한다.
전체 보드 면적에 임의 비율을 곱하는 방식 대신 고정 커밋 PCB의 Paste 도형 합집합과 BOM 실장 상태를 이용한다.
페이스트 두께는 기존 제조사 예제 0.1016 mm이며 OLIMEX 실제 스텐실 사양이라고 주장하지 않는다.
플럭스는 실제 외곽 면적과 제조사 도포 범위를 연결한다. 두 값 모두 실측 사용량이나 승인 공정이 아닌 조건부 계획 기준이다.
여유율 10%는 변경 가능한 초기 계획 정책이며 제조사 권고/실측 손실률이 아니다. 초기 장비 충전량은 별도이며 0으로 추정하지 않는다.
실납·세정제·솔더 바는 도면만으로 소비량을 결정할 수 없는 조건과 산식을 함께 보완하고 미확인 수량을 0으로 채우지 않는다.

- [x] 도형 추출: pad margin, 모듈/패드 회전, 앞뒤 면, U4 중복 선, UEXT1 delta=0 trapezoid, BOM 불명확 위치 분리
- [x] 원문 해시·치수·부품별 집계·대당 값·출처를 별도 JSON으로 재현 가능하게 저장
- [x] 런타임 원문 연결 검증 및 화면에 기준/여유율/생산 수량별 조건부 예상량 표시
- [x] 수학/실제 도면/기존 공정 재고·RFQ 회귀 검증

이 단계는 기준 데이터 보완이다. 기존 저장된 수동 검토/RFQ를 자동 변경하지 않는다. 실제 사용 차감과 자동 새로고침은 범위 밖이다.

## 결과

별도 데이터: `../data/per_board_usage_v1.json`. 재생성: `python -m ontologics.materials.build_usage_basis` (오프라인 Shapely 2.1.2 환경 필요, 웹 런타임 의존성 없음).

| 제품 | Paste 면적 합집합 | 미확인 실장 위치의 별도 면적 | 기본 페이스트 g/대 | 10% 여유 포함 100대 g |
|---|---:|---:|---:|---:|
| L WROOM | 487.300130131 mm² | 13.343536 mm² | 0.209070 | 22.9977 |
| L1 WROOM | 486.200130131 mm² | 14.443536 mm² | 0.208598 | 22.94578 |

두 보드 외곽은 80 × 28 mm, 노치 제외 면적 22.28331621 cm², 두께 1.6 mm.
페이스트 수치는 양면 PCB Paste 레이어 기반이며 실제 발주 스텐실/Variant 승인 값은 아니다.
U4의 중첩 도형 72개는 합집합으로 처리했다. UEXT1 trapezoid 10개는 rect_delta가 없으므로 직사각형과 같으며 footprint paste margin을 적용했다.
플럭스는 한 면 전체에 제조사 권고 고형분 도포 범위 상단을 적용하면 0.26739979452 g/대, 계획 기본량은 0.2674 g/대로 올림, 여유 10% 적용 100대는 29.414 g.
여유율은 현재 화면 비교용이며 저장된 회사 정책이나 RFQ 입력으로 자동 반영하지 않는다.

공식 계산 근거:
- [Indium 페이스트 계산법](https://www.indium.com/wp-content/uploads/2025/03/Calculating-Solder-Paste-Usage-APPNOTE-98807-R1-1.pdf): 개구부 체적과 페이스트 밀도를 사용. 초기 비드와 잔량 별도.
- [Kester 951 TDS](https://www.kester.com/DesktopModules/Bring2mind/DMX/API/Entries/Download?Command=Core_Download&EntryId=61851&PortalId=0&TabId=96&language=en-US): 고형분 120–240 µg/cm².
- PCB 출처는 기존 BOM과 같은 OLIMEX 커밋 `deb38a377d50eadb04ddc7118a17a5ae58b1ee53`. 원문/라이선스/해시와 위치별 산출값은 JSON에 유지.

검증: 격리 PostgreSQL 포함 관련 24개 통과 (`/private/tmp/per-board-tests.log`), Shapely 도형·재현성 3개 통과. UI에서 100대/10% 및 20% 계산 확인, 10%로 복원. 새 운영 검토/RFQ 생성이나 재고 변경 없음.
전체 테스트는 1,553개 실행, failure 10/error 19/skip 9로 전체 통과하지 못했다 (`/private/tmp/per-board-full-tests.log`). 기존 실행에서 확인된 neo4j/rdflib 누락, 서브프로세스 psycopg 경로, 테스트 패키지/환경 조건 실패가 포함된다. 신규 관련 검사는 통과했지만 전체 성공이라고 주장하지 않는다.
