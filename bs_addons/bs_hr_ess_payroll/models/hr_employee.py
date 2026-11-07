from odoo import models


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    def _ess_last_payslip(self):
        self.ensure_one()
        Payslip = self.env['hr.payslip'].sudo()
        compat = self.env['bs.hr.payroll.compat']
        return Payslip.search([('employee_id', '=', self.id), ('state', 'in', compat._confirmed_states())],
                              order='date_to desc, id desc', limit=1)

    def _ess_dashboard_cards(self):
        cards = super()._ess_dashboard_cards()
        compat = self.env['bs.hr.payroll.compat']
        slip = self._ess_last_payslip()
        if slip:
            currency = slip.company_id.currency_id
            value = '{:,.0f} {}'.format(compat._net_amount(slip), currency.symbol or '')
            subtitle = self.env._('Net · %s', slip.date_to.strftime('%m/%Y') if slip.date_to else '')
            url = '/my/hr/payslip/%s' % slip.id
        else:
            value, subtitle, url = '-', self.env._('No payslip yet'), '/my/hr/payslips'
        cards.append({'key': 'payslip', 'title': self.env._('Last payslip'), 'value': value,
                      'subtitle': subtitle, 'url': url, 'icon': 'fa-money', 'tone': 'info', 'sequence': 20})
        return cards

    def _ess_menu_items(self):
        items = super()._ess_menu_items()
        items.append({'key': 'payslips', 'title': self.env._('My payslips'), 'url': '/my/hr/payslips',
                      'icon': 'fa-money', 'sequence': 20})
        return items

