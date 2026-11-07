UI and workflow acceptance - 2026-10-25
========================================

Environment: Cove STG, Odoo 19 enterprise, addon 19.0.1.1.1.
The following checks were executed against the real staging application.

1. Journal Entry
----------------

Opened account.move 23 (STJ/2026/10/0021) from the adjustment smart button and
from Accounting's Journal Entries action 262. Both resolved to the fully
inherited standard account.view_move_form (759). The rendered main form text
matched in both Journal Items and Other Info. Posted status, Reverse Entry,
Reset to Draft and Chatter were present for the existing administrator account.
The existing entry and adjustment link were preserved; no entry was copied.

2. Stock Moves
---------------

Opened the list from BCA/2026/00013 and its original move 153. The final layout
was also opened from BCA/2026/00033, move 207: Done, quantity 1 Units, source
WH/Stock, destination BCA STG 20261025 Adjustment, posting date 2026-10-25.
List/form views are action-specific primary derivatives of standard stock
views. The form uses normal Odoo groups and a statusbar, without importing an
unrelated stock-request allocation table that squeezed the original layout.
Other Inventory screens are not modified.

3. Optional Components columns
------------------------------

All seven requested columns were initially visible and checked in the native
column chooser. Previous Adjustments was hidden; after reloading the screen its
column remained absent. It was then re-enabled successfully. The user's test
preference was restored to showing all seven columns. No custom persistence or
calculation changes were introduced.

4. Execution Sources
--------------------

Source 1 displayed grouped execution information, quantity/UoM, verification
fields, a full-width raw-material table and a Review and Evidence tab.
Open Execution Document opened the correct original mrp.production 3,
WH/MO/00003, with 100 completed units and 20 consumed components. Existing
quantity/evidence verification methods remain in place.

5. State transitions and server protection
------------------------------------------

The live RPC regression created BCA/2026/00032 against a dedicated test BOM/MO.
It covered Draft cancellation, cancellation guards, reset, review, reset/edit,
re-review, Reviewed cancellation, reset/re-review and execution. Quantity on
hand and BOM quantity were unchanged during Review/Cancel, and no stock moves
or journal entry existed until execution. Reset cleared review results and
signature; execution without a new Review was rejected.

The same workflow was exercised in the browser with BCA/2026/00033. Draft showed
Review and Cancel. Reviewed showed Execute Adjustment, Reset to Draft and
Cancel. Cancelled showed only Reset to Draft as a workflow action. Confirmed
showed the linked smart buttons and no workflow action. The quantity was edited
from 0.35 to 0.40 after resetting; re-review calculated the new difference and
execution created one movement. Direct server calls to execute/cancel/reset a
confirmed document were rejected, as were edits to confirmed/cancelled records.
Smart buttons were absent before linked records existed and appeared when those
records were available.

Validation scope
----------------

All 14 local package/calculation checks passed. An Odoo TransactionCase regression
for the new workflow was added; the local workspace has no complete Odoo runtime,
so that runner was not executed locally. The live ORM and browser checks above
were executed. Screenshots are retained in the local .stg-test directory.
Deployment targeted STG only; the production main branch was unchanged.
