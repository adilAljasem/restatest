# -*- coding: utf-8 -*-

from odoo import fields, models


class PosConfig(models.Model):
    _inherit = "pos.config"

    pino_bill_printed_color = fields.Char(
        string="Bill Printed Table Color",
        default="#f59e0b",
        help="Color of a table whose bill was printed and is waiting for payment.",
    )


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    pos_pino_bill_printed_color = fields.Char(
        related="pos_config_id.pino_bill_printed_color",
        readonly=False,
    )
