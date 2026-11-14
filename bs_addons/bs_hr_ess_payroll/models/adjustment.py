from odoo import fields, models
from odoo.exceptions import UserError


class BsHrPayrollAdjustment(models.Model):
    _inherit = 'bs.hr.payroll.adjustment'

    payslip_id = fields.Many2one('resta.payslip', string='Payslip', readonly=True, copy=False, index=True,
                                 ondelete='set null',
                                 help='Payslip in which this adjustment was injected as an input line.')
    payslip_input_id = fields.Many2one('resta.payslip.input', string='Payslip input', readonly=True, copy=False,
                                       ondelete='set null')

    def action_open_payslip(self):
        self.ensure_one()
        if not self.payslip_id:
            return False
        return {'type': 'ir.actions.act_window', 'res_model': 'resta.payslip', 'res_id': self.payslip_id.id,
                'view_mode': 'form'}

    def action_cancel(self):
        # a pending adjustment already injected in a draft payslip: remove the input line too
        for adj in self.filtered(lambda a: a.state == 'pending' and a.payslip_input_id):
            adj.payslip_input_id.sudo().unlink()
        res = super().action_cancel()
        self.filtered('payslip_id').write({'payslip_id': False})
        return res

    def write(self, vals):
        if self.env.context.get('ess_payroll_sync'):
            return super().write(vals)
        protected = {'amount', 'adj_type', 'employee_id'}
        if protected & set(vals) and any(a.state == 'applied' for a in self):
            raise UserError(self.env._('An applied adjustment cannot be modified: create a new one.'))
        return super().write(vals)
