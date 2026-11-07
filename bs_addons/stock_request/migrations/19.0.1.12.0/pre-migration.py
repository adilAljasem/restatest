# Copyright 2026 Mohamed Elmalky
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl.html).


def migrate(cr, version):
    """Back up legacy order-based templates before changing template_id's FK."""
    cr.execute(
        """
        SELECT 1
          FROM information_schema.columns
         WHERE table_name = 'stock_request_order'
           AND column_name = 'is_template'
        """
    )
    if not cr.fetchone():
        return

    cr.execute(
        """
        CREATE TABLE IF NOT EXISTS stock_request_template_legacy_backup (
            legacy_id INTEGER PRIMARY KEY,
            template_name VARCHAR,
            source_warehouse_id INTEGER,
            destination_warehouse_id INTEGER,
            priority VARCHAR,
            notes TEXT,
            company_id INTEGER
        )
        """
    )
    cr.execute(
        """
        CREATE TABLE IF NOT EXISTS stock_request_template_line_legacy_backup (
            legacy_line_id INTEGER PRIMARY KEY,
            legacy_template_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            product_uom_id INTEGER NOT NULL,
            product_uom_qty DOUBLE PRECISION NOT NULL
        )
        """
    )
    cr.execute(
        """
        CREATE TABLE IF NOT EXISTS stock_request_template_usage_legacy_backup (
            order_id INTEGER PRIMARY KEY,
            legacy_template_id INTEGER NOT NULL
        )
        """
    )
    cr.execute(
        """
        INSERT INTO stock_request_template_legacy_backup (
            legacy_id,
            template_name,
            source_warehouse_id,
            destination_warehouse_id,
            priority,
            notes,
            company_id
        )
        SELECT id,
               COALESCE(template_name, name),
               source_warehouse_id,
               warehouse_id,
               priority,
               reason,
               company_id
          FROM stock_request_order
         WHERE is_template = TRUE
        ON CONFLICT (legacy_id) DO NOTHING
        """
    )
    cr.execute(
        """
        INSERT INTO stock_request_template_line_legacy_backup (
            legacy_line_id,
            legacy_template_id,
            product_id,
            product_uom_id,
            product_uom_qty
        )
        SELECT request.id,
               request.order_id,
               request.product_id,
               request.product_uom_id,
               request.product_uom_qty
          FROM stock_request AS request
          JOIN stock_request_order AS request_order
            ON request_order.id = request.order_id
         WHERE request_order.is_template = TRUE
        ON CONFLICT (legacy_line_id) DO NOTHING
        """
    )
    cr.execute(
        """
        INSERT INTO stock_request_template_usage_legacy_backup (
            order_id,
            legacy_template_id
        )
        SELECT id, template_id
          FROM stock_request_order
         WHERE is_template = FALSE
           AND template_id IS NOT NULL
        ON CONFLICT (order_id) DO NOTHING
        """
    )
    cr.execute(
        """
        UPDATE stock_request_order
           SET template_id = NULL
         WHERE template_id IS NOT NULL
        """
    )
    cr.execute(
        """
        UPDATE ir_rule AS rule
           SET active = FALSE,
               domain_force = '[(0, ''='', 1)]'
          FROM ir_model_data AS data
         WHERE data.model = 'ir.rule'
           AND data.res_id = rule.id
           AND data.module = 'stock_request'
           AND data.name IN (
               'stock_request_template_read_rule',
               'stock_request_order_template_read_rule'
           )
        """
    )
    cr.execute(
        """
        UPDATE ir_ui_view AS view
           SET active = FALSE
          FROM ir_model_data AS data
         WHERE data.model = 'ir.ui.view'
           AND data.res_id = view.id
           AND data.module = 'stock_request'
           AND data.name IN (
               'stock_request_template_tree',
               'stock_request_template_form',
               'stock_request_template_manager_form'
           )
        """
    )
