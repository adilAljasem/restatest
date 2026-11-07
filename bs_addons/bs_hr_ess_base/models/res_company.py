from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    ess_otp_login = fields.Boolean(
        string='ESS: Login with mobile + OTP',
        help='Allow employees to sign in to the self-service portal with their '
             'mobile number and a one-time code sent by SMS.')
    ess_brand_color = fields.Char(string='ESS Brand Color', default='#FF5A1F')
    ess_app_name = fields.Char(string='ESS App Name', translate=True,
                               default='Employee Self-Service')
    ess_portal_landing = fields.Boolean(
        string='Employees land on Self-Service', default=True,
        help='Portal employees (also in the Odoo iOS / Android app) open their self-service home after login.')
    ess_default_sla_hours = fields.Integer(string='Default approval SLA (hours)', default=48)
