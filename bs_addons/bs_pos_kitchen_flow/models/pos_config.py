from odoo import fields, models


KITCHEN_FLOW_PRIORITIES = [
    ("printer_first", "Kitchen Printers First"),
    ("display_first", "Kitchen Display First"),
    ("parallel", "Display and Printers in Parallel"),
]


class PosConfig(models.Model):
    _inherit = "pos.config"

    kitchen_flow_debug = fields.Boolean(
        string="Kitchen Flow Diagnostics",
        default=True,
        help="Store a detailed timing trace for every order sent to the kitchen.",
    )
    kitchen_flow_notifications = fields.Boolean(
        string="Kitchen Sending Notifications",
        default=True,
        help="Show progress and completion messages while an order is sent to the kitchen.",
    )
    kitchen_flow_priority = fields.Selection(
        selection=KITCHEN_FLOW_PRIORITIES,
        string="Kitchen Delivery Priority",
        required=True,
        default="printer_first",
        help=(
            "Choose whether physical kitchen printers or the preparation display are handled "
            "first, or run both operations in parallel."
        ),
    )


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    pos_kitchen_flow_debug = fields.Boolean(
        related="pos_config_id.kitchen_flow_debug",
        readonly=False,
    )
    pos_kitchen_flow_notifications = fields.Boolean(
        related="pos_config_id.kitchen_flow_notifications",
        readonly=False,
    )
    pos_kitchen_flow_priority = fields.Selection(
        related="pos_config_id.kitchen_flow_priority",
        readonly=False,
    )

