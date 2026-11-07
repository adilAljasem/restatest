Kit selection and source review editing
======================================

Version 19.0.1.50.11

No-transit requester exception
-----------------------------

For request operations using two_step_no_transit only, the requester, manager,
and responsible users of the source/destination warehouse may change
product, UOM and requested quantity, add lines, and delete lines while the order
is draft, submitted, ready or in_transit. Saving does not change the order stage.
The exception does not apply to direct transfers, other flows, unauthorized
warehouses/companies, or completed orders.

In transit, requested quantity remains the administrative delivered quantity,
as in the existing no-transit calculation. Added lines start open and await
receipt; they do not return to Preparation Summary. Lines without recorded
receipts can be changed or deleted. Pending receipts must be completed/cancelled
first. A recorded receipt or closure prevents deletion or changing product/UOM;
quantity cannot fall below received plus closed/cancelled receipt quantities.
No completed stock moves are modified. Edits and receipt creation use the same
order lock to serialize concurrent changes.

Scoped record rules permit these item writes without sudo while retaining the
global company rules and existing read access. The order write guard only allows
item commands; it does not grant access to warehouse, workflow or audit fields.
New ready-stage lines start open and no stock moves are created by editing.

Preparation Summary continues to recompute from current remaining_delivery_qty
and matching lines whenever its data is read/refreshed. No cached aggregate is
stored or manually patched. Integration tests cover adding, changing and deleting
ready-stage items and expected totals of 10, 3 and 6. These database tests still
require execution on Odoo; local state/flow/access checks have passed.

Default behavior for other flows
-------------------------------

New request/transfer lines exclude products with an active Kit (phantom) BOM
for the operation company, or a shared BOM. Template BOMs exclude all variants;
variant-specific BOMs exclude only that variant. Components are not excluded
unless they themselves have a Kit BOM. Manufacturing remains optional.

The product selector and server-side create/product-change validation share the
same classification. Existing lines are not revalidated when only their quantity
or operational metadata changes. No migration rewrites old requests.

Draft editing retains its existing permissions. During source warehouse review,
the source responsible user or manager may update quantities and add new lines.
Existing product/UOM changes, deletion and relation replacement remain blocked
in that stage. New lines inherit order metadata and start in draft line state;
the order stays submitted. Acceptance uses the current lines as before.

A narrowly scoped create record rule permits source responsible users to add
lines only to submitted requests from their warehouse. Existing ACLs and rules
are unchanged. Creation/quantity editing after approval remains blocked.

Validation: local checks cover actual command validation, product search
operators, UI permission/state expressions and Python/XML syntax. The existing
12 preparation-summary contract tests pass. Database tests in
tests/test_kit_and_review_lines.py are included but require an Odoo database;
they have not been executed locally.
