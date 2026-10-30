# B-CQ04 synthetic production-only delivery scenario v1

Newly authored project development data, 2026-10-27. These are fictional operational facts and calculation assumptions, not OLIMEX or a real customer's production, dispatch or receipt records. Public ESP32-POE L/WROOM design identity is reused only as the existing Configuration reference. The prior CQ02/CQ03 operational scenarios are separate and must not be added to this scenario.

## A. Promise and snapshot

| Field | Value |
| --- | --- |
| Company / customer | DEMO-B / 가상고객사 A |
| Configuration | config:esp32-poe:L:WROOM |
| Contract / delivery round | contract:demo:B-01 / delivery-round:demo:B-01:1 |
| Promised quantity / unit | 1000 / piece |
| Arrival deadline | 2026-10-27T15:00:00+09:00, arrival at exactly the deadline is on time |
| Destination | 가상고객사 A 수령창고 |
| Snapshot | snapshot:demo:CQ04:base:10 |
| as_of | 2026-10-27T10:00:00+09:00 |

## B. Confirmed coverage of this synthetic snapshot

All allocations for the listed round and all other-round allocations from its sources have been checked. Source allocations, stock observations/events, shipments, production state, physical disjointness and absence of reallocation are explicitly confirmed for this example. The nine assertions are allocations_complete=true, source_allocations_complete=true, allocations_disjoint=true, stock_events_complete=true, stock_observations_before_departures=true, shipment_events_complete=true, production_state_complete=true, material_disjoint=true and no_reallocation=true.

Only the production work and its full allocation below belong to this round. There are no separate delivered, in-transit or warehouse quantities, other production works, stock allocations or shipments. This is a positive fictional completeness assertion, not an inference from an empty search. No overlapping physical quantities or prior departures are hidden in the work's completed quantity.

## C. Work and full-target allocation

| Work ID | snapshot | observed_at | target_quantity | completed_quantity | unit | production_state | started_at |
| --- | --- | --- | ---: | ---: | --- | --- | --- |
| work:demo:CQ04:base | snapshot:demo:CQ04:base:10 | 2026-10-27T10:00:00+09:00 | 1000 | 600 | piece | in_progress | 2026-10-27T08:00:00+09:00 |

The work belongs to DEMO-B and config:esp32-poe:L:WROOM. Its normal-unit target is assigned in full to the one listed round; no target share belongs to another round.

| Allocation ID | delivery_round_id | production_work_id | quantity | unit |
| --- | --- | --- | ---: | --- |
| allocation:demo:CQ04:base | delivery-round:demo:B-01:1 | work:demo:CQ04:base | 1000 | piece |

## D. Cumulative observations

| Observation ID | observed_at | produced_quantity | good_quantity | defective_quantity | unit |
| --- | --- | ---: | ---: | ---: | --- |
| observation:demo:CQ04:base:09 | 2026-10-27T09:00:00+09:00 | 550 | 510 | 40 | piece |
| observation:demo:CQ04:base:10 | 2026-10-27T10:00:00+09:00 | 650 | 600 | 50 | piece |

Both observations refer to work:demo:CQ04:base. These are cumulative counts, with produced=good+defective. At the snapshot the work completed count equals the latest good count. The latest interval adds 100 produced, 90 good and 10 defective in one hour. It is not an actual operating-time or generic quality measurement.

## E. Delivery calculation condition

| Condition ID | snapshot_id | delivery_round_id | preparation_seconds | transport_seconds | dispatch_mode |
| --- | --- | --- | ---: | ---: | --- |
| condition:demo:CQ04:base | snapshot:demo:CQ04:base:10 | delivery-round:demo:B-01:1 | 0 | 3600 | split_available |

The condition applies to each newly dispatched part of this round at its promised destination. Already completed good units may begin preparation at as_of; future good units may begin preparation when they become available. A nonzero preparation duration would be applied to every part, followed by transport. A missing duration is unknown and must not default to zero.

## F. Explicit calculation assumptions and limits

Recent good-unit production continues at the observed interval trend without interruption; the good units are usable for this round. Split dispatch is allowed whenever a part is available, with sufficient dispatch/preparation capacity and no batch minimum, vehicle schedule or frequency limit. Preparation and transport durations are constant for each part. No working calendar, shutdown, shift, rework, counter reset, resource competition or route/vehicle optimisation is modelled.

Future readiness durations use exact rational rates and round elapsed seconds up; deadline-side available elapsed seconds are rounded down and piece counts are rounded down. This is a conservative numeric model, not an assertion that real individual boards finish on integer seconds. Calculations never backdate current stock or production to before as_of.

The implied arithmetic is 90 good pieces/hour, 400 remaining pieces, production completion after16000 seconds at14:26:40 and full arrival at15:26:40. At a15:00 arrival deadline, production must be available by14:00:600+90*4=960 pieces, with40 short. These are conditional predictions, not actual departures, actual first completion, customer receipt, a changed contractual promise or approval to dispatch.

## G. Independent variants

Independent test variants may change the arrival deadline to16:00, transport_seconds to7200, preparation_seconds to1800, or dispatch_mode toall_at_once. all_at_once means the entire still-unshipped round becomes available before one preparation/transport cycle. For the unchanged15:00 deadline that gives0 on-time pieces; at16:00 it gives1000. A variant at11:00 can add cumulative produced750/good690/defective60 (continued90/hour) or produced750/good680/defective70 (recent80/hour), while updating snapshot/work/as_of consistently. These variants do not execute a change-approval workflow.
