from odoo import models


class PosConfig(models.Model):
    _inherit = "pos.config"

    def _get_forbidden_change_fields(self):
        return [*super()._get_forbidden_change_fields(), "journal_id"]
