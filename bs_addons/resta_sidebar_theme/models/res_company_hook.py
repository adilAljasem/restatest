from odoo import models

from ..hooks import DONE_PARAM, apply_email_button_color


class ResCompany(models.Model):
    _inherit = "res.company"

    def _register_hook(self):
        # Covers installs where the `mail` app arrives after this theme.
        super()._register_hook()
        cr = self.env.cr
        cr.execute("SELECT 1 FROM ir_config_parameter WHERE key = %s", [DONE_PARAM])
        if not cr.fetchone():
            apply_email_button_color(self.env)
