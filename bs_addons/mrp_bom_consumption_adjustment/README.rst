BOM Consumption Adjustments — Odoo 19
====================================

Arabic: تسويات استهلاك الوصفات

Installation
------------

Add the parent directory to ``addons_path``, update the Apps list and install
``mrp_bom_consumption_adjustment``. Dependencies: ``mrp_account`` and ``analytic``.
The menu is Manufacturing > Configuration > BOM Consumption Adjustments.
Only manufacturing administrators have document access and execution permission.

Setup and workflow
------------------

* On every **raw material category**, configure Recipe Adjustment Location in
  the active company. Its usage must be Inventory Loss; its company must match
  the document or be empty. Configure its ``valuation_account_id``.
* Configure the component stock valuation account, automated inventory valuation,
  and the company's stock journal. A cost centre and reason are optional.
* Choose the BOM, company, warehouse, internal stock location and execution period.
  Component quantities are per **BOM output base**, not necessarily per one unit.
* Tick only the lines to change and enter new quantities. Review shows actual
  leaf components for phantom children. Stocked manufactured children stay stocked.
* Open Execution Sources to inspect source documents, moves and frozen quantities.
  Historical/partial kit sources without an unambiguous frozen basis require a
  verified executed quantity and written evidence. Validate each source, then
  Review again. Sales ordered quantities and invoice counts are never used.
* For tracked components, open the review detail and allocate the absolute
  net adjustment quantity to lots/serials across its review details. Serial allocations must be one unit each.
* Confirm creates the stock corrections, posts one journal entry, links every
  correction move using Odoo 19 ``stock.move.account_move_id``, and updates only
  checked BOM lines. The confirmation runs in one database savepoint.

The document Date controls correction move dates and the journal date. The
execution period only selects historical sources. There are no inventory-count
date conditions. A zero-quantity result creates no move or journal entry.

Source and correction rules
---------------------------

Completed manufacturing orders use actual ``qty_produced`` and completed raw
material moves, including origin-linked component returns. Completed unbuilds
linked to the selected completed MOs are separate negative executions. Each MO/backorder has its own source identity.

Kit explosion captures root BOM, variant, original root/leaf UoMs, an execution
token and frozen component-per-kit coefficients **before** the kit move is
deleted by Odoo. Actual quantities are inferred only when all expected leaves
are present and yield the same execution quantity. Otherwise review requires
documented human verification. Backorders/returns retain frozen coefficients;
separate pickings remain separate executions. The source's verified quantity is
immutable. POS flows are supported when they create component stock moves with
the native BOM links; the source's picking opens the original stock document.

Other BOM types use their linked stock executions and require a verified
execution basis unless an exact frozen basis is available. Custom execution
adapters can override ``_collect_sources``. Flows without native BOM links or an
identifiable variant are explicitly rejected. Repeated leaf lines through
multiple phantom branches are rejected because native merged moves do not
preserve enough parent provenance to attribute consumption safely.

The Components tab displays ten optional columns. Recorded consumption includes
signed confirmed corrections; required consumption uses net executed output and
the new BOM quantity. Quantity difference = required - recorded. Value difference
= quantity difference * current company cost, with its sign. Displayed unit cost
uses the displayed component UoM. Confirmation refreshes costs and nets the
source corrections per component before creating stock moves. A zero net
component produces neither a movement nor journal lines. Historical detail
provenance remains available; previous corrections are hidden by default there.

Previous corrections are counted by source + selected BOM line + leaf BOM line
+ component. Original consumption excludes every module correction. Review is
checked again at confirmation under a BOM row lock; stale BOMs, source changes
or newly confirmed overlapping corrections require a fresh review.

Accounting and valuation
------------------------

The cost of every raw material is captured from its company-specific
``standard_price`` before any confirmation move changes costs. Module moves
override Odoo 19 ``_set_value`` / ``_get_value_data`` to use this frozen cost for
both incoming and outgoing moves, including FIFO and AVCO. Ordinary movements
continue to use native costing methods. Native per-move journal creation and
stock analytic entries are suppressed for module moves; the sole consolidated
entry places 100% analytic distribution on difference-account lines only when
a cost centre is selected; otherwise it supplies no analytic distribution.

FIFO remaining inventory uses immutable value overlays on the surviving receipt
quantities. The delta between native FIFO removal and the frozen current-cost
entry is allocated in proportion to remaining layer value. Subsequent ordinary
FIFO issues consume those overlays; original receipt and outgoing move values
are unchanged. FIFO corrections must be posted today (the source period may be
historical). An empty/negative remaining inventory with a residual value is
rejected transactionally instead of posting an unreconciled value.

Verification status
-------------------

UI/workflow update 19.0.1.1.1: the Journal Entry smart button opens the standard
Accounting form; Stock Moves and Execution Sources use organized action-specific
views; all seven Components quantity columns are optional and shown by default.
Draft -> Reviewed -> Confirmed remains the execution path. Draft/Reviewed may be
cancelled, and Reviewed/Cancelled may be reset to Draft, requiring a fresh Review.
Confirmed documents cannot be edited, cancelled, reset or executed again.
See ``UI_TEST_RESULTS.rst`` for the five live acceptance checks.

Executed in the supplied workspace:

* Python AST and XML syntax validation.
* Manifest data-file and ACL model-reference validation.
* Twelve isolated tests executing the module's actual ``_calculate`` method:
  positive/negative differences, positive/negative prior corrections, batch
  size 10, customer returns, UoM conversion, unchecked lines, unverified sources,
  intermediate transfers, ambiguous kit paths and missing BOM provenance.

Total: **14 checks passed**.

Live Odoo 19 staging validation was also executed on 2026-10-25, including
installation/upgrades, standard web-form save, normal manufacturing, phantom
kits, an actual POS sale, positive/negative corrections, duplicate prevention,
posted entries and analytic distribution. FIFO was checked against the aggregate
inventory report, subsequent ordinary issues, an extreme 1:100 receipt cost
ratio, and unchanged historical movement values. AVCO movement/report agreement
was checked separately. See ``STAGING_TEST_RESULTS.rst`` for concrete evidence.

The ``tests/`` Odoo TransactionCase suite is included for automated regression.
It was not run through odoo-bin in this local workspace, which lacks a complete
Odoo runtime and PostgreSQL. The live checks above were executed against the
staging application's real ORM and stock/accounting methods.

Run the local checks::

    python mrp_bom_consumption_adjustment/tools/check_module.py

Run Odoo integration tests against a disposable database::

    python odoo-bin -d recipe_adjustment_test --addons-path=odoo/addons,enterprise,custom \
        -i mrp_bom_consumption_adjustment --test-enable \
        --test-tags /mrp_bom_consumption_adjustment --stop-after-init

التشغيل بالعربية
----------------

بعد تثبيت الموديول، اختر اللغة العربية من تفضيلات المستخدم. حدّد موقع تسوية
الوصفات على فئة كل خامة باستخدام الشركة الصحيحة، ثم افتح «تسويات استهلاك
الوصفات» من إعدادات التصنيع. راجع تفاصيل المصادر والمكونات الفعلية قبل التأكيد.
بيانات تنفيذ الـKit القديمة غير الكافية تحتاج كمية تنفيذ موثقة؛ لا تُستنتج
من الكميات الجديدة للوصفة. تم تنفيذ اختبارات فعلية على STG تشمل التصنيع وPOS والقيود وFIFO وAVCO.
تسويات FIFO تُرحّل بتاريخ اليوم، ويمكن أن تغطي فترة استهلاك تاريخية.
راجع ملف نتائج الاختبار للتفاصيل وحدود الحالات المختبرة.
