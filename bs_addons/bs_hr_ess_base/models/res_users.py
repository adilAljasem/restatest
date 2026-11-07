from odoo import models
from odoo.exceptions import AccessDenied


class ResUsers(models.Model):
    _inherit = 'res.users'

    def _check_credentials(self, credential, env):
        if credential.get('type') == 'bs_ess_otp':
            otp = self.env['bs.hr.ess.otp'].sudo().browse(int(credential.get('otp_id') or 0)).exists()
            if not otp or not otp._verify(self.env.user, str(credential.get('code') or '')):
                raise AccessDenied()
            return {'uid': self.env.user.id, 'auth_method': 'bs_ess_otp', 'mfa': 'default'}
        return super()._check_credentials(credential, env)
