# Stock availability / webhook verification — 2026-09-17

## Follow-up — 2026-09-22 / 19.0.1.23.0

The duplicate-ID regression below is now resolved by rejecting ambiguous
catalogue identities rather than silently using another product's stock. This
does not renumber existing IDs; any real collision still requires coordinated
data correction. The regression is now passing and also verifies no ambiguous
product notification is sent.

Unified webhook support now sends `item.updated` with a Boolean `is_stock`
when configured; legacy routing still sends `menu.updated`. See
[UNIFIED_WEBHOOK.md](UNIFIED_WEBHOOK.md) for routing and rollout prerequisites.

- Full native local Odoo 19 suite: **169 tests, 0 failures, 0 errors**.
- Includes 16 unified-webhook tests covering the four event routes, depletion /
  restock / reservations, timeout and manual retry reconciliation, branch and
  product toggles, inbound echo prevention, ID collisions, destination changes,
  manual accept/reject and auto acceptance.
- Outbound HTTP remained mocked; no staging/production changes or Blend calls.
- Result: `tools/logs/validation-module-bs_pos_blend_intgeration-20260922-104923-217894-19476-e8ceca66.json`.

The original investigation below is retained as a historical record. Its
remaining-defect statement and menu-only contract describe the earlier version,
not this follow-up. Its scheduler, latency, concurrency-test and external
delivery limitations still apply; this is not an unconditional production
sign-off.

## Original result — 2026-09-17

**Not an unconditional production sign-off. One reproducible defect remains.**

- Native local Odoo 19, database `test_blend_menu_notifications_20260916`.
- Full module suite: **153 tests; 152 passed, 1 failed, 0 errors**.
- Stock-specific coverage: 33 stock tests plus 2 HTTP tests; 34 passed,
  1 failed (duplicate API identifiers).
- Added 15 regression/lifecycle tests. Only tests and this report were changed
  for this investigation; runtime logic was not modified.
- Static validation: 52 files, 0 errors, 0 warnings.
- No staging/production access, deployment, real Blend requests or credentials.

Full result: `tools/logs/validation-module-bs_pos_blend_intgeration-20260917-190348-230211-35456-3e639776.json`
(workspace-relative path).

## Confirmed defect: one product can inherit another product's availability

Native products use their Odoo template ID in the menu API. Imported products
can instead use `blend_external_id`. Both are placed in the same per-branch
dictionary in `services/pos_api.py:get_menu` without checking uniqueness.

Reproduction:

1. Publish tracked native product A, initially with one unit.
2. Publish another available product B in the same branch, with
   `blend_external_id` equal to A's template ID, later in catalogue order.
3. Deplete A to zero. Its stock service correctly returns unavailable, and
   its `menu.updated` notification is sent.
4. Fetch the menu. A's displayed row can nevertheless have
   `available_branches[].is_stock = true`, taken from B through the shared ID.

The test `test_regression_duplicate_api_ids_must_not_mask_out_of_stock` is
deliberately left failing, not skipped or marked expected-failure. It is the
remaining regression to fix. The same issue first appeared in the HTTP test
because fixture external ID `100` collided with a native demo product ID.
The normal HTTP lifecycle fixture now limits the catalogue to its own category;
the dedicated collision test independently reproduces the defect.

Before a production sign-off, enforce unambiguous product identity consistently
across menu output, order resolution, and webhook targets. Silently choosing a
different product's stock is not a safe fallback. No live catalogue was audited,
so this report does not claim that a collision exists on the user's server.

## Verified behavior without ID collisions

| Area | Verified |
| --- | --- |
| Thresholds | Positive to zero, positive directly to negative, zero to negative, negative to zero stays unavailable, positive restock becomes available |
| Branch scope | Source location and internal shelves, unrelated locations/companies excluded, independent branch availability, transfers notify affected branches |
| Reservations | Available balance is on hand minus reserved; native stock assignment can exhaust availability; cancellation/unreserve restores it |
| POS inventory | Native realtime POS picking depletes stock and queues notification; deferred stock waits for session picking |
| Other operations | Inventory adjustment, quant deletion, native stock move/return, negative quants offset positive shelf quants |
| Modifiers | Stock changes invalidate parent menu items, including every parent of a shared modifier |
| Transactions | No HTTP during stock writes, rollback removes jobs, uncommitted jobs are invisible to an independent database transaction |
| Queue | Cron processes pending jobs, does not resend completed jobs, respects retry times, recovers interrupted attempts |
| Failures | Timeout/connection/503 failures, retry limit, failed state, manual retry recovery, no internal stock metadata sent |
| HTTP contract | Local GET /api/menu changes true to false at zero/negative and back to true on restock; Boolean types preserved |
| Order guard | Local POST /api/orders rejects unavailable tracked products with HTTP 409 even before webhook delivery; no order created |
| Permissions | Stock operator can trigger the queue; unauthorized stock write fails without producing a stock job |

Existing tests also simulate stale writer snapshots and coalescing of multiple
changes. They are not a real parallel-worker load/stress test. The independent
cursor test establishes uncommitted-job isolation, not a blanket concurrency
guarantee.

## Operational prerequisites and limits

- The current outgoing contract is **`menu.updated`**, not an outgoing
  `item.updated` carrying `is_stock: false`. Blend must fetch the fresh menu after
  the notification and apply each branch's `available_branches[].is_stock`.
- Automatic stock-based availability applies to **Track Inventory** products
  (`is_storable`). Untracked goods/services are not disabled by zero quants.
- The POS picking type must point to the correct branch stock source/company.
  Manual unavailability and branch schedules can keep a restocked item disabled.
- Queue processing happens after commit, not synchronously in an inventory
  write. A temporarily zero balance that returns positive before processing may
  be coalesced into the final state instead of notifying an intermediate state.
- For timely POS depletion, use **In real time** stock updates. The test confirms
  that **At the session closing** cannot signal a sale's depletion before the
  associated inventory movement.
- Configure a valid menu-event webhook URL, required outbound authentication,
  an active queue cron, and running cron workers. The local development config
  currently has `max_cron_threads = 0`; tests invoked the queue explicitly.
  Staging/production scheduler settings were not checked.
- Retries are finite. Once the configured limit is exhausted, the log remains
  **Failed** and is not automatically selected by the cron until retried.
  Failed/delayed jobs require operational monitoring.
- This is Boolean availability, not stock reservation or a requested-quantity
  guarantee. A new pending/accepted incoming order does not itself guarantee
  that its quantities have already been reserved or deducted.
- Outbound responses were mocked. Successful local send-path tests do not prove
  actual Blend receipt, menu refetch, downstream Jahez synchronization, or
  network availability.
