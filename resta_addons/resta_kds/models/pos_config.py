from odoo import api, fields, models


class PosConfig(models.Model):
    _inherit = "pos.config"

    resta_kds_display_ids = fields.Many2many("resta.kds.display", "resta_kds_display_pos_config_rel",
                                             "config_id", "display_id", string="شاشات المطبخ")
    resta_kds_send_on_payment = fields.Boolean(
        "أرسل للمطبخ عند الدفع", default=True,
        help="عند دفع طلب لم يُرسل للمطبخ بعد (أو جزء منه) يُرسل تلقائياً. مناسب للوجبات السريعة والكاونتر.")

    def _ensure_kds_display(self, name=None):
        """Create a default kitchen screen for configs that have none."""
        Display = self.env["resta.kds.display"].sudo()
        for config in self:
            if not config.resta_kds_display_ids:
                Display.create({"name": name or "المطبخ - %s" % config.name,
                                "company_id": config.company_id.id,
                                "pos_config_ids": [(6, 0, config.ids)]})
        return True


class PosSession(models.Model):
    _inherit = "pos.session"

    @api.model
    def _load_pos_data_models(self, config):
        return super()._load_pos_data_models(config) + ["resta.kds.display"]


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    pos_resta_kds_display_ids = fields.Many2many(related="pos_config_id.resta_kds_display_ids", readonly=False)
    pos_resta_kds_send_on_payment = fields.Boolean(related="pos_config_id.resta_kds_send_on_payment", readonly=False)
