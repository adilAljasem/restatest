# Copyright 2026 Mohamed Elmalky
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl.html).

from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Remove obsolete create menus/actions and retired menu containers."""
    env = api.Environment(cr, SUPERUSER_ID, {})

    for xmlid in (
        "stock_request.stock_request_all_operations_menu",
        "stock_request.menu_stock_request_master_data",
    ):
        menu = env.ref(xmlid, raise_if_not_found=False)
        if menu:
            menu.unlink()

    root_menu = env.ref(
        "stock_request.menu_stock_request_root", raise_if_not_found=False
    )
    legacy_names = {"Create Stock Request", "Create Direct Transfer"}
    if root_menu:
        legacy_menus = env["ir.ui.menu"].with_context(lang="en_US").search(
            [
                ("id", "child_of", root_menu.id),
                ("name", "in", list(legacy_names)),
            ]
        )
        legacy_menus.unlink()

    legacy_actions = (
        env["ir.actions.act_window"]
        .with_context(lang="en_US")
        .search(
            [
                ("res_model", "=", "stock.request.order"),
                ("name", "in", list(legacy_names)),
            ]
        )
    )
    legacy_actions.unlink()
