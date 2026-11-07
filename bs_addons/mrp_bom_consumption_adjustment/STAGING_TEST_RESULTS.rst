Live staging verification - 2026-10-25
======================================

Environment: Cove STG, Odoo 19 enterprise. Addon version 19.0.1.0.4.
Test records use the prefix ``BCA STG 20261025`` and dedicated products,
accounts, categories and analytic account. Production branch was not changed.

Executed checks
---------------

* Standard browser form: created and saved BCA/2026/00013 without the original
  Access Error. Readonly BOM/component snapshots were populated server-side.
  Review and Confirm also completed in the browser: move 153 and posted entry
  STJ/2026/10/0021, value 10, document state Confirmed.
* Manufacturing: 100 completed units consumed 20 components. BCA/2026/00003
  increased required consumption to 25: extra issue 5, stock value and entry 50.
* Duplicate period: BCA/2026/00004 calculated zero and created no stock move or
  accounting entry. BCA/2026/00005 reduced required consumption to 15 and returned
  10 (including reversal of the previous 5), value 100.
* Phantom kit: BCA/2026/00006 and 00007 passed the same increase/decrease checks;
  changing the BOM did not change the frozen original execution quantity.
* POS: a dedicated copied POS config/session processed a paid 10-unit sale.
  Its completed picking consumed 2 components. BCA/2026/00014 changed required
  consumption to 3 and posted the extra 1. The original POS session was not closed.
  The dedicated test session was subsequently closed and its journal entry posted.
* Each nonzero tested correction posted one balanced entry. Stock moves linked
  to that entry; only difference-account lines carried the analytic distribution.
* AVCO: stock quantity 180 -> 175, total inventory value 3600 -> 3500,
  adjustment stock movement and entry both 100.
* FIFO first exposed two defects: frozen-cost rounding and the aggregate report
  ignoring forced outgoing move values. Both were corrected and retested.
* FIFO remaining-value test: 180 units/value 2800 -> 175/value 2722.22,
  adjustment movement/entry 77.78. The subsequent ordinary issue and a negative
  correction also reconciled with inventory value.
* Final FIFO allocation test used receipt prices 1 and 100: value 10080 -> 9800,
  matching adjustment 280. Subsequent ordinary issue reduced value by 9.73;
  the negative correction increased value by 593.35. Original receipt values
  100 and 10000 and original delivery value 20 remained unchanged. No negative
  FIFO cost was introduced by the large price ratio.
* Server rejected edits to confirmed documents, their component quantities,
  generated stock moves, and a second confirmation. A changed BOM after Review
  was also rejected rather than silently confirmed against stale data.
* All 14 local calculation/package checks passed.

Scope and operating constraints
-------------------------------

FIFO posting date must be today to avoid retroactively changing outgoing costs;
the execution period can be historical. A residual value with empty/negative
remaining stock is rejected. Historical kits lacking reliable frozen execution
evidence require documented manual verification through Execution Sources.

The complete Odoo TransactionCase runner, non-manager login, multi-company,
lot-valuated FIFO, nested kit, and concurrent confirmation scenarios were not
all executed live in this session. This report describes the tested scenarios,
not an exhaustive certification. Earlier failed FIFO experiments remain clearly
labelled test data in STG and were not silently rewritten.
