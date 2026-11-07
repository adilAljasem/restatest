from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Allow standard stock moves while preserving the installed rule domain."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    rule = env.ref(
        "stock_request.stock_request_inventory_move_rule",
        raise_if_not_found=False,
    )
    missing_request_line = "('stock_request_line_id', '=', False)"
    if rule and missing_request_line not in rule.domain_force:
        rule.domain_force = (
            f"['|', {missing_request_line}] + ({rule.domain_force})"
        )
