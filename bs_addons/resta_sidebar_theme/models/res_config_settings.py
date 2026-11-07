from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    rs_logo_light = fields.Image(related="company_id.rs_logo_light", readonly=False)
    rs_logo_dark = fields.Image(related="company_id.rs_logo_dark", readonly=False)
    rs_logo_mark = fields.Image(related="company_id.rs_logo_mark", readonly=False)
    rs_logo_dark_treatment = fields.Selection(
        related="company_id.rs_logo_dark_treatment", readonly=False,
    )
    rs_default_scheme = fields.Selection(related="company_id.rs_default_scheme", readonly=False)
    rs_sidebar_default = fields.Selection(related="company_id.rs_sidebar_default", readonly=False)
    rs_group_sections = fields.Boolean(related="company_id.rs_group_sections", readonly=False)
    rs_icon_style = fields.Selection(related="company_id.rs_icon_style", readonly=False)
