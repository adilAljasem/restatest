from odoo import _, models
from odoo.exceptions import UserError


class PosConfig(models.Model):
    _inherit = "pos.config"

    def open_ui(self):
        if self.env["resta.saas.client"].sudo().blocked_state():
            icp = self.env["ir.config_parameter"].sudo()
            state = icp.get_param("resta.subscription_state")
            msg = _("انتهى اشتراك ريستا لهذا المطعم.") if state == "expired" else _("تم إيقاف حساب ريستا لهذا المطعم.")
            raise UserError(msg + "\n" + _("لا يمكن فتح نقطة البيع. تواصل مع مدير حسابك لتفعيل الاشتراك."))
        return super().open_ui()
