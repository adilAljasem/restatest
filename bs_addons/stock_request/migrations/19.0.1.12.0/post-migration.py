# Copyright 2026 Mohamed Elmalky
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl.html).

from odoo import Command, SUPERUSER_ID, api


def _unique_template_name(template_model, name, company_id):
    template_model = template_model.with_context(active_test=False)
    base_name = name or "Migrated Stock Request Template"
    candidate = base_name
    sequence = 2
    while template_model.search_count(
        [("name", "=", candidate), ("company_id", "=", company_id)]
    ):
        candidate = f"{base_name} ({sequence})"
        sequence += 1
    return candidate


def migrate(cr, version):
    """Move legacy template orders into the dedicated template models."""
    cr.execute("SELECT to_regclass('stock_request_template_legacy_backup')")
    if not cr.fetchone()[0]:
        return

    env = api.Environment(cr, SUPERUSER_ID, {})
    template_model = env["stock.request.template"]
    product_model = env["product.product"]
    uom_model = env["uom.uom"]
    legacy_to_template = {}

    cr.execute(
        """
        SELECT legacy_id,
               template_name,
               source_warehouse_id,
               destination_warehouse_id,
               priority,
               notes,
               company_id
          FROM stock_request_template_legacy_backup
      ORDER BY legacy_id
        """
    )
    legacy_templates = cr.dictfetchall()
    for legacy in legacy_templates:
        source_warehouse_id = (
            legacy["source_warehouse_id"]
            or legacy["destination_warehouse_id"]
        )
        if not source_warehouse_id or not legacy["destination_warehouse_id"]:
            continue

        cr.execute(
            """
            SELECT product_id, product_uom_id, product_uom_qty
              FROM stock_request_template_line_legacy_backup
             WHERE legacy_template_id = %s
          ORDER BY legacy_line_id
            """,
            [legacy["legacy_id"]],
        )
        quantities_by_product = {}
        for legacy_line in cr.dictfetchall():
            product = product_model.browse(legacy_line["product_id"]).exists()
            source_uom = uom_model.browse(
                legacy_line["product_uom_id"]
            ).exists()
            if not product or not source_uom:
                continue
            quantity = source_uom._compute_quantity(
                legacy_line["product_uom_qty"], product.uom_id
            )
            quantities_by_product[product.id] = (
                quantities_by_product.get(product.id, 0.0) + quantity
            )

        template = template_model.create(
            {
                "name": _unique_template_name(
                    template_model,
                    legacy["template_name"],
                    legacy["company_id"],
                ),
                "source_warehouse_id": source_warehouse_id,
                "destination_warehouse_id": legacy[
                    "destination_warehouse_id"
                ],
                "priority": legacy["priority"] or "0",
                "notes": legacy["notes"],
                "company_id": legacy["company_id"],
                "line_ids": [
                    Command.create(
                        {
                            "product_id": product_id,
                            "product_uom_qty": quantity,
                        }
                    )
                    for product_id, quantity in quantities_by_product.items()
                ],
            }
        )
        legacy_to_template[legacy["legacy_id"]] = template.id

    cr.execute(
        """
        SELECT order_id, legacy_template_id
          FROM stock_request_template_usage_legacy_backup
        """
    )
    for usage in cr.dictfetchall():
        template_id = legacy_to_template.get(usage["legacy_template_id"])
        if template_id:
            cr.execute(
                "UPDATE stock_request_order SET template_id = %s WHERE id = %s",
                [template_id, usage["order_id"]],
            )

    legacy_order_ids = [legacy["legacy_id"] for legacy in legacy_templates]
    if legacy_order_ids:
        legacy_orders = env["stock.request.order"].browse(legacy_order_ids).exists()
        legacy_orders.write({"state": "draft"})
        legacy_lines = legacy_orders.mapped("stock_request_ids")
        legacy_lines.write({"state": "draft"})
        legacy_lines.unlink()
        legacy_orders.unlink()

    cr.execute("DROP TABLE stock_request_template_usage_legacy_backup")
    cr.execute("DROP TABLE stock_request_template_line_legacy_backup")
    cr.execute("DROP TABLE stock_request_template_legacy_backup")
