# Copyright 2026 Mohamed Elmalky
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl.html).


def migrate(cr, version):
    """Rename the operational group and reload the tightened record rules."""
    cr.execute(
        """
        UPDATE ir_model_data old_data
           SET name = 'group_stock_request_warehouse_responsible'
         WHERE old_data.module = 'stock_request'
           AND old_data.name = 'group_stock_request_direct_transfer'
           AND NOT EXISTS (
               SELECT 1
                 FROM ir_model_data new_data
                WHERE new_data.module = 'stock_request'
                  AND new_data.name =
                      'group_stock_request_warehouse_responsible'
           )
        """
    )
    rule_xml_ids = (
        "stock_request_followers_rule",
        "stock_request_rule",
        "stock_request_source_responsible_rule",
        "stock_request_destination_responsible_rule",
        "stock_request_manager_rule",
        "stock_request_order_followers_rule",
        "stock_request_order_rule",
        "stock_request_order_source_responsible_rule",
        "stock_request_order_destination_responsible_rule",
        "stock_request_order_manager_rule",
    )
    cr.execute(
        """
        UPDATE ir_model_data
           SET noupdate = FALSE
         WHERE module = 'stock_request'
           AND name IN %s
        """,
        (rule_xml_ids,),
    )
    cr.execute(
        """
        UPDATE ir_rule rule
           SET active = FALSE
          FROM ir_model_data data
         WHERE data.module = 'stock_request'
           AND data.model = 'ir.rule'
           AND data.name IN (
               'stock_request_followers_rule',
               'stock_request_order_followers_rule'
           )
           AND rule.id = data.res_id
        """
    )
