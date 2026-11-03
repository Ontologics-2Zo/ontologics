# B-CQ04 synthetic mixed-quantity delivery scenario v1

Newly authored project development data, 2026-10-27. Fictional operational facts and assumptions, not OLIMEX production, shipment or customer receipt records. This scenario is separate from both the CQ04 production-only example and the prior CQ02/CQ03 fixtures. Matching numbers do not establish shared physical inventory.

## A. Promise and snapshot

| Field | Value |
| --- | --- |
| Company / customer | DEMO-B / 가상고객사 A |
| Configuration | config:esp32-poe:L:WROOM |
| Contract / delivery round | contract:demo:B-01 / delivery-round:demo:B-01:1 |
| Promised quantity / unit | 1000 / piece |
| Arrival deadline | 2026-10-27T15:00:00+09:00, arrival at exactly the deadline is on time |
| Destination | 가상고객사 A 수령창고 |
| Snapshot | snapshot:demo:CQ04:mixed:10 |
| as_of | 2026-10-27T10:00:00+09:00 |

## B. Confirmed coverage and physical disjointness

All nine coverage assertions are true: allocations_complete, source_allocations_complete, allocations_disjoint, stock_events_complete, stock_observations_before_departures, shipment_events_complete, production_state_complete, material_disjoint and no_reallocation. All round allocations, all source allocations including other rounds, stock events, shipments and actual departure/arrival events through as_of have been checked. No other work or allocation belongs to this round. This is explicit fictional coverage, not an empty-search inference.

D, T, W and P below are different physical quantities. The historical source stocks D/T are before their departures and are not current warehouse stock. P's completed200 good pieces are not W's250 pieces and do not create another warehouse balance. No partial shipment, return, reallocation or production-to-stock transfer is included. All sources belong to DEMO-B, config:esp32-poe:L:WROOM and unitpiece.

## C. Stock observations

| Stock ID | observed_at | location | quantity | unit |
| --- | --- | --- | ---: | --- |
| stock:demo:CQ04:mixed:D | 2026-10-27T08:00:00+09:00 | 가상 완제품창고 D | 100 | piece |
| stock:demo:CQ04:mixed:T | 2026-10-27T08:00:00+09:00 | 가상 완제품창고 T | 150 | piece |
| stock:demo:CQ04:mixed:W | 2026-10-27T10:00:00+09:00 | 가상 완제품창고 W | 250 | piece |

All stock nodes belong to snapshot:demo:CQ04:mixed:10. W's250 pieces are available to begin preparation now, with no actual departure. D/T source observations precede the full-quantity departures below.

## D. Work and cumulative production observations

| Work ID | observed_at | target_quantity | completed_quantity | unit | production_state | started_at |
| --- | --- | ---: | ---: | --- | --- | --- |
| work:demo:CQ04:mixed:P | 2026-10-27T10:00:00+09:00 | 500 | 200 | piece | in_progress | 2026-10-27T08:00:00+09:00 |

The work belongs to snapshot:demo:CQ04:mixed:10. Its entire500-good-piece target is assigned to this round; it is not changed to the round's1000-piece promise.

| Observation ID | observed_at | produced_quantity | good_quantity | defective_quantity | unit |
| --- | --- | ---: | ---: | ---: | --- |
| observation:demo:CQ04:mixed:09 | 2026-10-27T09:00:00+09:00 | 150 | 110 | 40 | piece |
| observation:demo:CQ04:mixed:10 | 2026-10-27T10:00:00+09:00 | 250 | 200 | 50 | piece |

Both observations refer to work:demo:CQ04:mixed:P; counters are cumulative and produced=good+defective. The latest normal increase is90 pieces/hour, with300 remaining to the work target. It is not an actual operating-time or generic quality measurement.

## E. Separate allocations

| Allocation ID | stock_id | production_work_id | quantity | unit |
| --- | --- | --- | ---: | --- |
| allocation:demo:CQ04:mixed:D | stock:demo:CQ04:mixed:D | absent | 100 | piece |
| allocation:demo:CQ04:mixed:T | stock:demo:CQ04:mixed:T | absent | 150 | piece |
| allocation:demo:CQ04:mixed:W | stock:demo:CQ04:mixed:W | absent | 250 | piece |
| allocation:demo:CQ04:mixed:P | absent | work:demo:CQ04:mixed:P | 500 | piece |

Each allocation belongs to snapshot:demo:CQ04:mixed:10 and delivery-round:demo:B-01:1. Exactly one source is recorded per allocation. Their disjoint sum is1000 pieces; all other-round source allocations and source capacities are confirmed.

## F. Actual departures and actual/expected customer arrivals

| Shipment ID | allocation_id | quantity | departed_at | expected_arrival_at | arrived_at |
| --- | --- | ---: | --- | --- | --- |
| shipment:demo:CQ04:mixed:D | allocation:demo:CQ04:mixed:D | 100 | 2026-10-27T08:30:00+09:00 | absent | 2026-10-27T09:00:00+09:00 |
| shipment:demo:CQ04:mixed:T | allocation:demo:CQ04:mixed:T | 150 | 2026-10-27T09:30:00+09:00 | 2026-10-27T12:00:00+09:00 | absent |

Both full-quantity shipments belong to the same snapshot, unitpiece and promised destination 가상고객사 A 수령창고. D has an actual arrival; T has actual departure and an evidenced future ETA but no actual arrival through10:00. No shipment exists for W or P. A planned departure is not an actual departure. An elapsed ETA alone never establishes actual receipt.

## G. Delivery calculation condition

| Condition ID | snapshot_id | delivery_round_id | preparation_seconds | transport_seconds | dispatch_mode |
| --- | --- | --- | ---: | ---: | --- |
| condition:demo:CQ04:mixed | snapshot:demo:CQ04:mixed:10 | delivery-round:demo:B-01:1 | 0 | 3600 | split_available |

This condition applies per newly dispatched part of W/P at the promised destination. Already dispatched T uses its own evidenced12:00 ETA; it is not recalculated as as_of plus3600 seconds. D uses its actual09:00 arrival. Current goods may begin preparation at as_of, future good units when available. Missing conditions remain unknown.

## H. Explicit assumptions, arithmetic and limits

Recent normal production continues at90/hour without interruption and the good units are usable. Split dispatch can occur whenever units become available, with sufficient preparation/dispatch capacity and no batch, frequency, vehicle or calendar constraint. Preparation and transport durations are constant per part. Forecast readiness seconds round up using exact rational rates; deadline-side available seconds and piece counts round down. This is a numeric policy, not a real per-board timing claim. No current quantity is retrospectively dispatched before as_of.

P's remaining300 pieces require12000 seconds: production ready13:20, arrival14:20. W can arrive11:00. With D at09:00 and T expected12:00, the15:00 deadline has100+150+250+500=1000 on-time pieces,0 short, with full arrival expected14:20. In an independent deadline14:00 variant, P contributes200+90*3=470, so the round has970 on-time and30 short; full arrival remains14:20.

Actual arrival later than a deadline is excluded from on-time quantity, even if currently delivered. Such a test must move as_of after the actual event and keep source/work/observation times consistent or use an actual-event-only scenario. An elapsed ETA without actual arrival requires updated confirmation. Applying global all_at_once to this already partially shipped round is outside the first supported all-at-once scope and must not silently mean separate batch dispatch per allocation.

No prediction creates a Shipment, actual completion, departure, customer receipt, contract change or operational approval. Production-only scope success does not substitute for this mixed-allocation scenario. Multiple competing works, partial target shares, partial actual shipments, returns, reallocation, work calendars and resource/route scheduling are outside this first calculation model.
