# Copyright 2026 Mohamed Elmalky
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl.html).

from odoo import api, fields, models
from .preparation_summary import ENABLED_PARAMETER


TRANSFER_FLOW_PARAMETER = "stock_request.transfer_flow"
SHOW_AVAILABLE_QTY_PARAMETER = "stock_request.show_available_qty"
ALLOW_PURCHASE_LINE_WAREHOUSE_PARAMETER = (
    "stock_request.allow_warehouse_in_purchase_order_line"
)


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    stock_request_preparation_summary_enabled = fields.Boolean(
        string="Preparation Summary", default=True,
        config_parameter=ENABLED_PARAMETER,
    )

    @api.model
    def _sync_preparation_summary_menu(self):
        enabled = self.env["ir.config_parameter"].sudo().get_param(ENABLED_PARAMETER, "True")
        menu = self.env.ref("stock_request.preparation_summary_menu", raise_if_not_found=False)
        if menu:
            menu.sudo().write({"active": str(enabled).lower() in ("true", "1")})
        return True

    stock_request_transfer_flow = fields.Selection(
        selection=[
            ("one_step", "One Step"),
            ("two_step", "Two Step"),
            ("two_step_no_transit", "Two Step without Transit"),
        ],
        string="Transfer Flow",
        required=True,
        default="one_step",
        config_parameter=TRANSFER_FLOW_PARAMETER,
        help=(
            "Choose whether Stock Transfer creates one direct internal transfer "
            "immediately, uses separate delivery and receipt transfers through a "
            "transit location, or creates a direct transfer only when the receipt "
            "is confirmed."
        ),
    )
    stock_request_show_available_qty = fields.Boolean(
        string="Show Available Quantity",
        config_parameter=SHOW_AVAILABLE_QTY_PARAMETER,
        help=(
            "Show the Available Qty column on product lines for both One Step "
            "and Two Step flows, based on the selected Source Warehouse."
        ),
    )
    stock_request_allow_warehouse_in_purchase_order_line = fields.Boolean(
        string="Allow Warehouse in Purchase Order Line",
        config_parameter=ALLOW_PURCHASE_LINE_WAREHOUSE_PARAMETER,
        help="Allow a different warehouse for every purchase order line.",
    )

    @api.model
    def _sync_stock_request_transfer_flow_menus(self, transfer_flow=None):
        transfer_flow = transfer_flow or (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param(TRANSFER_FLOW_PARAMETER, "one_step")
        )
        one_step = transfer_flow == "one_step"
        menu_states = {
            "stock_request.menu_stock_request_operations": not one_step,
            "stock_request.stock_request_direct_transfer_menu": not one_step,
            "stock_request.stock_request_one_step_transfer_menu": one_step,
            "stock_request.stock_request_one_step_return_menu": one_step,
            "stock_request.stock_request_to_transfer_menu": False,
            "stock_request.stock_request_to_deliver_menu": not one_step,
            "stock_request.stock_request_to_receive_menu": not one_step,
            "stock_request.stock_request_in_transit_menu": not one_step,
            "stock_request.stock_request_warehouse_setup_menu": not one_step,
            "stock_request.menu_stock_request_reporting": True,
            "stock_request.menu_stock_request_configuration": True,
        }
        for xmlid, active in menu_states.items():
            menu = self.env.ref(xmlid, raise_if_not_found=False)
            if menu and menu.active != active:
                menu.sudo().write({"active": active})

        root_menu = self.env.ref(
            "stock_request.menu_stock_request_root",
            raise_if_not_found=False,
        )
        root_action = self.env.ref(
            "stock_request.stock_request_one_step_transfer_action"
            if one_step
            else "stock_request.stock_request_my_requests_action",
            raise_if_not_found=False,
        )
        if root_menu and root_action and root_menu.action != root_action:
            root_menu.sudo().write(
                {"action": f"{root_action._name},{root_action.id}"}
            )
        return True

    def set_values(self):
        result = super().set_values()
        # Persist False explicitly: deleting the parameter would restore default True.
        self.env["ir.config_parameter"].sudo().set_param(
            ENABLED_PARAMETER, "True" if self.stock_request_preparation_summary_enabled else "False")
        self._sync_preparation_summary_menu()
        self.env["ir.config_parameter"].sudo().set_param(
            SHOW_AVAILABLE_QTY_PARAMETER,
            bool(self.stock_request_show_available_qty),
        )
        self._sync_stock_request_transfer_flow_menus(
            self.stock_request_transfer_flow
        )
        return result
