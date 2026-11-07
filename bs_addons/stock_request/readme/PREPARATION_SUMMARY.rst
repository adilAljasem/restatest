Preparation Summary (Odoo 19)
=============================

Enable the report in Stock Transfer > Configuration > Settings > Preparation
Summary. Open Stock Transfer > Preparation Summary. Upgrade stock_request to
19.0.1.50.2 and reload the browser to load the native list assets.

The report uses Odoo's list, search, Filters, Group By and Favorites. Source,
destination, company, product, internal reference and request-date conditions
are applied to authorized request lines before aggregation. Custom date ranges
can be entered using the native custom filter dialog. Grouping is offered for
the existing row dimensions (company, source, product, reference and UoM), so it
cannot split a product total by destination or date. Group order counts use a
union of contributing orders; quantities have no mixed-unit total.

The row key and effective list domain are used to recompute drilldown access.
Print PDF and Export Excel operate on all matching rows, independently of list
pagination or selection. PDF and Excel use the same dataset as the list. Excel
keeps internal references as text and quantities/counts as numbers.

The enable setting is global, consistent with the existing module settings.
It defaults to enabled when upgrading an existing report. A disabled value is
stored explicitly; other settings saves and module updates retain it. Disabling
hides the menu and blocks list data, drilldown and both exports on the server.
Only configuration metadata is read with elevated access. Request/order data
always uses the current user and selected companies, with an additional source
warehouse responsibility restriction for warehouse responsible users.

Validation performed locally
----------------------------

* 12 Python contract tests using real Odoo 19 domain utilities and precision
  comparison, with fixture ORM data: 25/2 -> 10/1 -> 6/1, pre-aggregation date
  and destination filtering, partitions, distinct orders, cancellation and
  precision, old flow filters, disabled/unauthorized entry points, pagination,
  grouped protocol and drilldown, shared PDF data, and real XLSX XML contents.
* Odoo 19 list/search RelaxNG schemas, Python syntax, XML parsing and manifest
  references passed.
* Real Odoo Owl compiled/rendered the toolbar in Edge. Controller contract tests
  verified row-specific domains and selected companies, PDF actions, and binary
  Excel download. These were not tests of the full Odoo web client.
* English/Arabic 100-row browser PDF layout fixtures had no horizontal cell
  overflow; column headers repeated on every English PDF page. Arabic direction
  and joined text were visually checked. This used browser printing, not Odoo's
  QWeb/wkhtmltopdf rendering pipeline.

Remaining runtime validation
----------------------------

No PostgreSQL/Odoo test database or wkhtmltopdf runtime was available locally.
The three post-install database tests in test_preparation_summary_settings.py
were therefore not run. Actual Settings saves/menu cache behavior, native list
and Favorites integration, actual stock delivery/backorder operations, real
record-rule enforcement across users/companies and QWeb PDF generation must be
checked on an Odoo 19 staging database before production deployment.

Example staging command (adapt database and addons paths):

  odoo-bin -d TEST_DATABASE -u stock_request --test-enable \
    --test-tags /stock_request:TestPreparationSummary,/stock_request:TestPreparationSummarySettings \
    --stop-after-init

No approval, delivery, receipt, backorder, reservation, warehouse configuration
or existing access-rule implementation was changed.
