from odoo import models


class PosSession(models.Model):
    _inherit = "pos.session"

    def _set_opening_control_data(self, cashbox_value: int, notes: str):
        result = super()._set_opening_control_data(cashbox_value, notes)
        cron = self.env.ref(
            "bs_pos_delivery_core.ir_cron_bs_delivery_resume_pos_sessions",
            raise_if_not_found=False,
        )
        if cron and any(session.state == "opened" for session in self):
            cron.sudo()._trigger()
        return result
