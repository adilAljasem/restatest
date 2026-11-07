from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ResCompany(models.Model):
    _inherit = "res.company"

    bs_delivery_use_company_defaults = fields.Boolean(
        string="Use Company Defaults on New Connections"
    )
    bs_delivery_default_commission_pct = fields.Float(default=25)
    bs_delivery_default_sound_alert = fields.Boolean(default=True)
    bs_delivery_default_auto_print = fields.Boolean(default=True)
    bs_delivery_default_polling_minutes = fields.Integer(default=2)

    @api.constrains("bs_delivery_default_commission_pct", "bs_delivery_default_polling_minutes")
    def _check_delivery_defaults(self):
        for company in self:
            if (
                not 0 <= company.bs_delivery_default_commission_pct <= 100
                or company.bs_delivery_default_polling_minutes <= 0
            ):
                raise ValidationError(
                    _(
                        "Delivery defaults require commission between 0 and 100 and a positive polling interval."
                    )
                )


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    bs_delivery_api_user_id = fields.Many2one(
        "res.users",
        string="API User",
        config_parameter="bs_pos_delivery_core.api_user_id",
        default=lambda self: self.env.ref(
            "bs_pos_delivery_core.user_delivery_api", raise_if_not_found=False
        ),
    )

    bs_delivery_use_company_defaults = fields.Boolean(
        related="company_id.bs_delivery_use_company_defaults", readonly=False
    )
    bs_delivery_default_commission_pct = fields.Float(
        related="company_id.bs_delivery_default_commission_pct", readonly=False
    )
    bs_delivery_default_sound_alert = fields.Boolean(
        related="company_id.bs_delivery_default_sound_alert", readonly=False
    )
    bs_delivery_default_auto_print = fields.Boolean(
        related="company_id.bs_delivery_default_auto_print", readonly=False
    )
    bs_delivery_default_polling_minutes = fields.Integer(
        related="company_id.bs_delivery_default_polling_minutes", readonly=False
    )


class DeliveryConnection(models.Model):
    _inherit = "bs.delivery.connection"

    @api.model_create_multi
    def create(self, vals_list):
        prepared = []
        for incoming in vals_list:
            values = dict(incoming)
            company = self.env["res.company"].browse(values.get("company_id")) or self.env.company
            if company.bs_delivery_use_company_defaults:
                for field_name, value in self._company_delivery_defaults(company).items():
                    values.setdefault(field_name, value)
            prepared.append(values)
        return super().create(prepared)

    @api.model
    def _company_delivery_defaults(self, company):
        return {
            "commission_pct": company.bs_delivery_default_commission_pct,
            "sound_alert": company.bs_delivery_default_sound_alert,
            "auto_print_kitchen": company.bs_delivery_default_auto_print,
            "polling_interval_minutes": company.bs_delivery_default_polling_minutes,
        }
