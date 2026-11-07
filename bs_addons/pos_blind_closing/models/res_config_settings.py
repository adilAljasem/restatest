from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    pos_blind_closing = fields.Boolean(related="pos_config_id.blind_closing", readonly=False)
    pos_blind_closing_manager_visible = fields.Boolean(
        related="pos_config_id.blind_closing_manager_visible", readonly=False
    )
    pos_blind_closing_empty_inputs = fields.Boolean(
        related="pos_config_id.blind_closing_empty_inputs", readonly=False
    )
    pos_blind_closing_show_all_banks = fields.Boolean(
        related="pos_config_id.blind_closing_show_all_banks", readonly=False
    )
    pos_blind_closing_hide_reports = fields.Boolean(
        related="pos_config_id.blind_closing_hide_reports", readonly=False
    )
    pos_blind_closing_confirm = fields.Boolean(
        related="pos_config_id.blind_closing_confirm", readonly=False
    )
    pos_blind_closing_log = fields.Boolean(related="pos_config_id.blind_closing_log", readonly=False)
