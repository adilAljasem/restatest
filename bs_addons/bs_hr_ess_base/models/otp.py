import hashlib
import hmac
import logging
import re
import secrets
from datetime import timedelta

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

OTP_TTL_MINUTES = 5
OTP_MAX_ATTEMPTS = 5
OTP_MAX_PER_USER_15MIN = 3
OTP_MAX_PER_IP_HOUR = 10


def normalize_phone(phone):
    """Normalize Saudi numbers to 9665XXXXXXXX (digits only)."""
    digits = re.sub(r'\D', '', phone or '')
    if digits.startswith('00'):
        digits = digits[2:]
    if digits.startswith('05') and len(digits) == 10:
        digits = '966' + digits[1:]
    elif digits.startswith('5') and len(digits) == 9:
        digits = '966' + digits
    return digits


class BsHrEssOtp(models.Model):
    _name = 'bs.hr.ess.otp'
    _description = 'ESS One-Time Login Code'
    _order = 'id desc'

    user_id = fields.Many2one('res.users', required=True, ondelete='cascade', index=True)
    code_hash = fields.Char(required=True)
    expire_at = fields.Datetime(required=True)
    attempts = fields.Integer(default=0)
    used = fields.Boolean()
    ip_address = fields.Char()

    def _hash(self, code):
        secret = self.env['ir.config_parameter'].sudo().get_param('database.secret', '')
        return hmac.new(secret.encode(), code.encode(), hashlib.sha256).hexdigest()

    @api.model
    def _find_employee_by_phone(self, phone):
        target = normalize_phone(phone)
        if len(target) < 9:
            return self.env['hr.employee']
        tail = target[-9:]
        candidates = self.env['hr.employee'].sudo().search([
            ('user_id', '!=', False), '|', '|',
            ('mobile_phone', 'like', tail[-7:]),
            ('private_phone', 'like', tail[-7:]),
            ('work_phone', 'like', tail[-7:]),
        ])
        for emp in candidates:
            for value in (emp.mobile_phone, emp.private_phone, emp.work_phone):
                if value and normalize_phone(value) == target and emp.company_id.ess_otp_login:
                    return emp
        return self.env['hr.employee']

    @api.model
    def _issue(self, employee, ip_address):
        """Create an OTP and send it. Returns (otp, error_message)."""
        now = fields.Datetime.now()
        user = employee.user_id
        recent_user = self.sudo().search_count([
            ('user_id', '=', user.id), ('create_date', '>', now - timedelta(minutes=15))])
        recent_ip = self.sudo().search_count([
            ('ip_address', '=', ip_address), ('create_date', '>', now - timedelta(hours=1))])
        if recent_user >= OTP_MAX_PER_USER_15MIN or recent_ip >= OTP_MAX_PER_IP_HOUR:
            return None, self.env._('Too many codes requested. Please try again later.')
        code = '%06d' % secrets.randbelow(1000000)
        otp = self.sudo().create({
            'user_id': user.id, 'code_hash': self._hash(code),
            'expire_at': now + timedelta(minutes=OTP_TTL_MINUTES), 'ip_address': ip_address,
        })
        self._send(employee, code)
        return otp, None

    def _send(self, employee, code):
        lang = employee.user_id.lang or self.env.lang
        body = self.with_context(lang=lang).env._(
            'Your self-service login code is %(code)s. It expires in %(min)s minutes.',
            code=code, min=OTP_TTL_MINUTES)
        number = employee.mobile_phone or employee.private_phone
        if 'sms.sms' in self.env and number:
            self.env['sms.sms'].sudo().create({'number': number, 'body': body})._send()
        else:
            _logger.warning('ESS OTP generated for %s but the "sms" module is not installed; '
                            'no SMS could be sent.', employee.user_id.login)

    def _verify(self, user, code):
        self.ensure_one()
        if self.used or self.user_id != user or self.expire_at < fields.Datetime.now():
            return False
        if self.attempts >= OTP_MAX_ATTEMPTS:
            return False
        if hmac.compare_digest(self.code_hash, self._hash(code or '')):
            self.used = True
            return True
        # persist the failed attempt even though the login transaction rolls back
        with self.env.registry.cursor() as cr:
            cr.execute('UPDATE bs_hr_ess_otp SET attempts = attempts + 1 WHERE id = %s', [self.id])
        return False

    @api.autovacuum
    def _gc_expired(self):
        self.sudo().search([('expire_at', '<', fields.Datetime.now() - timedelta(days=1))]).unlink()
