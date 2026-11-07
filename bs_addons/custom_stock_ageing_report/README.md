# Custom Stock Ageing Report (Odoo 19)

`custom_stock_ageing_report` adds **Inventory > Reporting > Stock Ageing Report** for Inventory Managers.

## Functional scope

- A modal wizard matching the reference workflow: company, Product/Category mode,
  Warehouse/Location mode, period length, number of periods, and report date.
- A configurable period length and 2-12 total buckets. The last bucket is open-ended; 30 days and four buckets produce 0-30, 31-60, 61-90, and over 90 days.
- Explicit **View Report**, **Print PDF**, and **Print Excel** actions in the modal footer.
- On-screen dynamic results, landscape PDF, and formatted XLSX export. Excel is presented
  through a separate **Files to Download** dialog, matching the reference flow.
- Product, internal reference, UoM, warehouse, total quantity/value, and quantity/value for every ageing bucket.
- Historical quantities are reconstructed exclusively from completed `stock.move.line` records before the end of the selected report date.

## FIFO and value rules

The report runs an analytical FIFO simulation without writing to stock or accounting records:

1. External receipts and positive adjustments create dated quantity layers.
2. Outgoing moves consume the oldest available layer.
3. Internal and transit moves transfer the consumed layers with their original date and unit cost.
4. A linked return (`origin_returned_move_id`) restores the original layer when that layer can be traced. It does not acquire the return date.
5. Negative stock is retained as a deficit and assigned to the bucket in which the deficit arose, so the bucket sum still reconciles to the historical location balance.
6. Layer unit value uses the Odoo 19 `stock.move` valuation API (`value`, `is_in`, `is_out`, and `_get_value(at_date=...)`). Bucket value is remaining layer quantity multiplied by that historical incoming unit value.

The result is an ageing analysis, not an accounting revaluation. It never changes moves, quants, product costs, or journal entries.

## Installation

1. Put `custom_stock_ageing_report` in an Odoo 19 addons path.
2. Ensure the standard Odoo 19 `stock_account` module and Python package `xlsxwriter` are available.
3. Update the Apps list.
4. Install **Custom Stock Ageing Report**.
5. Give the user **Inventory / Administrator** access; Odoo's Reporting parent menu is manager-only.

## Upgrade from an earlier build

1. Replace the existing `custom_stock_ageing_report` directory; do not install a second addon.
2. Restart Odoo so the updated Python models are loaded.
3. Upgrade the addon from Apps, or run:

```text
odoo-bin -d DATABASE -u custom_stock_ageing_report --stop-after-init
```

4. Hard-refresh the browser, then open **Inventory > Reporting > Stock Ageing Report**.

Command-line example:

```text
odoo-bin -d DATABASE -i custom_stock_ageing_report --stop-after-init
```

## Automated tests

The `tests/test_stock_ageing.py` suite covers:

- exact bucket boundaries and the absence of gaps/overlaps;
- FIFO residual quantity and acquisition-layer value;
- a historical cutoff before a later delivery;
- warehouse-to-warehouse transfers retaining original age and cost;
- customer returns restoring the original layer;
- inventory-adjustment decreases consuming FIFO;
- XLSX generation and PDF report-data preparation.
- modal actions and the Excel download dialog;
- warehouse isolation, category/location filter modes, and the empty-result message.

Run after installing the module:

```text
odoo-bin -d TEST_DATABASE --test-enable --test-tags /custom_stock_ageing_report --stop-after-init
```

## Verification performed in this workspace

Actually executed against the local Odoo 19 source tree:

- imported the complete addon through Odoo 19's addon loader;
- parsed every Python source file and all three XML data/view files;
- checked the 0/30/31/60/61/90/91-day bucket boundaries;
- generated and opened the structure of an in-memory XLSX workbook (ZIP/XLSX signature and non-empty content);
- confirmed that the referenced Odoo 19 fields/APIs exist in the local 19.0 source, including
  `stock.move.value`, `is_in`, `is_out`, `remaining_qty`, `stock.move.line.quantity_product_uom`,
  `stock.location.warehouse_id`, and `stock.move.origin_returned_move_id`.

The database-backed `TransactionCase` suite was not executed in this workspace because no PostgreSQL
service, listener, or PostgreSQL binaries are installed here. Run the command above on the target Odoo
test database before production deployment; the limitation is environmental, not a missing test or stub.
