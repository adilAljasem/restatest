from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    rs_logo_light = fields.Image(
        string="Sidebar Logo (Light Mode)", max_width=1024, max_height=1024,
        help="Full wordmark shown at the top of the sidebar in light mode.",
    )
    rs_logo_dark = fields.Image(
        string="Sidebar Logo (Dark Mode)", max_width=1024, max_height=1024,
        help="Light-colored version of the wordmark for dark mode. "
             "If empty, the light logo is adapted automatically.",
    )
    rs_logo_mark = fields.Image(
        string="Collapsed Sidebar Mark", max_width=256, max_height=256,
        help="Small square mark shown when the sidebar is collapsed.",
    )
    rs_logo_dark_treatment = fields.Selection(
        [
            ("smart", "Invert dark parts, keep brand colors"),
            ("white", "Make the logo fully white"),
            ("none", "Show as is"),
        ],
        string="Dark Mode Logo Treatment", default="smart", required=True,
        help="Used only when no dark-mode logo is uploaded.",
    )
    rs_default_scheme = fields.Selection(
        [("system", "Follow device"), ("light", "Light"), ("dark", "Dark")],
        string="Default Color Mode", default="system", required=True,
    )
    rs_sidebar_default = fields.Selection(
        [("expanded", "Expanded"), ("collapsed", "Collapsed")],
        string="Sidebar Default State", default="expanded", required=True,
    )
    rs_icon_style = fields.Selection(
        [("resta", "Resta line icons (black & white)"), ("odoo", "Original app icons")],
        string="App Icon Style", default="resta", required=True,
    )
    rs_group_sections = fields.Boolean(
        string="Group Apps into Sections", default=True,
    )
