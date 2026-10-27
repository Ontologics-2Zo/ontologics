# B-CQ02 가정 물량 현황 원문 v1

이 문서는 개발용 가정 자료다. OLIMEX나 다른 회사의 실제 계약·생산·재고·운송 기록이 아니다. 기존 공개 제품 구성만 참조한다. Task 07에서 업무 입력을 정한 것이며 현재 그래프·DB에 등록된 자료가 아니다. 이 프로젝트의 개발·시험·문서 예제로 사용하도록 새로 작성했다.

## 기준 표

| 항목 | 원문 값 |
| --- | --- |
| 우리 회사 | DEMO-B |
| 제품 구성 | config:esp32-poe:L:WROOM |
| 대상 계약 | contract:demo:B-01 |
| 대상 계약 항목 | contract-item:demo:B-01:1 |
| 대상 회차 | delivery-round:demo:B-01:1 |
| 약속 수량 | 1000 |
| 단위 | piece |
| 현황 기준 시점 | 2026-10-27T10:00:00+09:00 |
| 원문 작성 시점 | 2026-10-27T10:05:00+09:00 |
| 최초 조회 예시 | 2026-10-27T10:10:00+09:00 |
| 고객 도착 기한 | 2026-10-27T15:00:00+09:00 |
| 집계 범위 | 아래 재고·작업·배정·운송과 확인 범위를 함께 사용 |

## 다른 회차 표

| 계약 ID | 항목 ID | 회차 ID | 회사 | 고객 | 구성 | 항목 수량 | 회차 수량 | 단위 | 도착 기한 | 장소 |
| --- | --- | --- | --- | --- | --- | ---: | ---: | --- | --- | --- |
| contract:demo:B-CQ02-other | contract-item:demo:B-CQ02-other:1 | delivery-round:demo:B-CQ02-other:1 | DEMO-B | 가상고객사 B | config:esp32-poe:L:WROOM | 300 | 300 | piece | 2026-10-28T15:00:00+09:00 | 가상고객사 B 수령창고 |

별도 계약의 회차다. 기존 B-01 항목에 300대를 더 약속한 것으로 해석하지 않는다. 대상 회차와 구성이 같더라도 이 회차에 배정된 물량은 대상 회차 합계에서 제외한다.

## 재고 표

| 재고 ID | 물량 구분 | 구성 | 관측 시점 | 장소 | 그 시점 수량 | 단위 |
| --- | --- | --- | --- | --- | ---: | --- |
| stock:demo:CQ02:D | D | config:esp32-poe:L:WROOM | 2026-10-27T09:00:00+09:00 | 가상 출하창고 D | 100 | piece |
| stock:demo:CQ02:T | T | config:esp32-poe:L:WROOM | 2026-10-27T09:00:00+09:00 | 가상 출하창고 T | 150 | piece |
| stock:demo:CQ02:W | W | config:esp32-poe:L:WROOM | 2026-10-27T10:00:00+09:00 | 가상 보관창고 W | 350 | piece |

위 재고와 아래 생산 작업의 소유/운영 회사는 모두 DEMO-B다. D/T는 출고 전 관측이다. 이후 출발·도착은 운송 표에 있다. W의 350대는 10시 창고 관측이며 이번 회차 250대와 다른 회차 100대로 나눠 배정했다. 서로 다른 관측 시점의 수량을 현재 창고 수량으로 그대로 더하지 않는다.

## 생산 작업 표

| 작업 ID | 물량 구분 | 구성 | 상태 확인 시점 | 정상 완료 목표 | 단위 | 진행 상태 | 실제 시작 | 시작 예정 |
| --- | --- | --- | --- | ---: | --- | --- | --- | --- |
| work:demo:CQ02:R | R | config:esp32-poe:L:WROOM | 2026-10-27T10:00:00+09:00 | 300 | piece | in_progress | 2026-10-27T09:30:00+09:00 | 없음 |
| work:demo:CQ02:P | P | config:esp32-poe:L:WROOM | 2026-10-27T10:00:00+09:00 | 100 | piece | ready | 없음 | 2026-10-27T10:30:00+09:00 |
| work:demo:CQ02:Q | Q | config:esp32-poe:L:WROOM | 2026-10-27T10:00:00+09:00 | 100 | piece | waiting | 없음 | 2026-10-27T12:00:00+09:00 |
| work:demo:CQ02:O | O | config:esp32-poe:L:WROOM | 2026-10-27T10:00:00+09:00 | 200 | piece | ready | 없음 | 2026-10-27T11:00:00+09:00 |

이번 원문의 모든 작업은 기준 시점에 정상 완료 수량이 0이며, 목표 전량이 미완성임을 확인했다. 재고 D/T/W와 작업 R/P/Q/O는 서로 다른 물량이다. `ready`는 생산 시작 준비 완료, `waiting`은 아직 준비 조건을 기다리는 상태다. 두 상태 모두 생산 완료가 아니다. 구체적인 준비 조건·속도·불량률·완료 예상은 이 원문에 없다.

## 배정 표

| 배정 ID | 회차 ID | 대상 종류 | 대상 ID | 배정 수량 | 단위 |
| --- | --- | --- | --- | ---: | --- |
| allocation:demo:CQ02:D | delivery-round:demo:B-01:1 | stock | stock:demo:CQ02:D | 100 | piece |
| allocation:demo:CQ02:T | delivery-round:demo:B-01:1 | stock | stock:demo:CQ02:T | 150 | piece |
| allocation:demo:CQ02:W | delivery-round:demo:B-01:1 | stock | stock:demo:CQ02:W | 250 | piece |
| allocation:demo:CQ02:R | delivery-round:demo:B-01:1 | production_work | work:demo:CQ02:R | 300 | piece |
| allocation:demo:CQ02:P | delivery-round:demo:B-01:1 | production_work | work:demo:CQ02:P | 100 | piece |
| allocation:demo:CQ02:Q | delivery-round:demo:B-01:1 | production_work | work:demo:CQ02:Q | 100 | piece |
| allocation:demo:CQ02:OTHER-W | delivery-round:demo:B-CQ02-other:1 | stock | stock:demo:CQ02:W | 100 | piece |
| allocation:demo:CQ02:OTHER-P | delivery-round:demo:B-CQ02-other:1 | production_work | work:demo:CQ02:O | 200 | piece |

각 배정은 같은 재고/작업 안에서도 겹치지 않는 수량 부분을 가리킨다. 배정 ID가 다르다는 이유만으로 물량의 독립성을 가정하지 않고 이 원문의 확인 범위를 함께 사용한다. 수량을 단순 집계한 자료이며 개별 보드 일련번호를 생성하지 않는다.

## 운송 표

| 운송 ID | 배정 ID | 수량 | 단위 | 출발 예정 | 실제 출발 | 도착 예상 | 실제 고객 도착 | 도착 장소 |
| --- | --- | ---: | --- | --- | --- | --- | --- | --- |
| shipment:demo:CQ02:D | allocation:demo:CQ02:D | 100 | piece | 없음 | 2026-10-27T09:10:00+09:00 | 2026-10-27T09:40:00+09:00 | 2026-10-27T09:40:00+09:00 | 가상고객사 A 수령창고 |
| shipment:demo:CQ02:T | allocation:demo:CQ02:T | 150 | piece | 없음 | 2026-10-27T09:20:00+09:00 | 2026-10-27T11:00:00+09:00 | 없음 | 가상고객사 A 수령창고 |

D는 고객 도착을 확인했다. T는 실제 출발했으나 10시까지 실제 고객 도착이 없다고 확인했다. 도착 예상만으로 도착 완료를 만들지 않는다. 첫 시연은 재고 배정 한 건의 전량을 운송 한 건으로 옮긴다. 부분 출고·분할 운송·반품은 입력하지 않는다.

## 확인 범위 표

| 확인 ID | 범위 | 확인한 내용 |
| --- | --- | --- |
| coverage:CQ02:rounds | 두 회차의 10시 배정 전체 | 배정 표는 두 회차의 전체 배정이며 미등록 배정 없음. D/T는 실제 출발 전에 해당 회차에 배정됐고 이후 재배정·배정 해제 없음. 각 회차의 나머지 약속은 미배정으로 셀 수 있음 |
| coverage:CQ02:sources | D/T/W/R/P/Q/O와 관련된 모든 회차 | 다른 회차의 원천 배정까지 모두 제시. 각 배정 부분은 겹치지 않으며 원천 물량을 초과하지 않음 |
| coverage:CQ02:stock-events | D/T는 09시 관측부터 10시까지, W는 10시 관측 | 표의 출고 외 입고·출고·폐기·이동·반품 없음. 출고는 기초 재고 관측 뒤 발생했으며 재고 수량에 아직 차감되지 않은 사건임 |
| coverage:CQ02:shipment-events | 두 운송의 실제 출발부터 10시까지 | D만 고객 도착, T는 미도착. 운송 취소·수량 분할·다른 도착 기록 없음 |
| coverage:CQ02:production | 네 작업의 10시 상태와 물량 | 정상 완료 0, 목표 전량 미완성. 재고/운송과 물량 중복 없음. 생산→재고 전환 없음 |

이 표는 첫 개발 예제의 명시적 완전성 가정이다. 기존 장착 이력용 `HistoryCoverage`에 저장할 자료가 아니다. 실제 자료의 누락 없음을 일반적으로 보장하는 규칙도 아니다. 해당 근거가 없거나 충돌하면 전체 현황을 확정하지 않는다. 10시 이후의 실제 상태는 확인하지 않았다.

`없음`은 해당 시점까지 없다고 이 예제에서 확인한 값이다. 입력 필드가 빠진 경우의 ‘미확인’과 구분한다. 등록 시 선택 시각의 ‘없음’은 문자열 시각으로 저장하지 않고 필드를 생략하되 위 확인 범위 근거를 연결한다.
