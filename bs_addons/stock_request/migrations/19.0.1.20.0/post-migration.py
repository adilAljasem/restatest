# Copyright 2026 Mohamed Elmalky
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl.html).

from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Remove obsolete user-facing records without touching legacy data fields."""
    env = api.Environment(cr, SUPERUSER_ID, {})

    legacy_menu = env.ref(
        "stock_request.menu_stock_request", raise_if_not_found=False
    )
    if legacy_menu:
        legacy_menu.unlink()

    legacy_action = env.ref(
        "stock_request.action_stock_request_form", raise_if_not_found=False
    )
    if legacy_action:
        legacy_action.unlink()

    for xmlid in (
        "stock_request.view_stock_request_tree",
        "stock_request.stock_request_search",
        "stock_request.view_stock_request_form",
        "stock_request.stock_route_form_view_inherit_stock_request",
    ):
        legacy_view = env.ref(xmlid, raise_if_not_found=False)
        if legacy_view:
            legacy_view.unlink()
