from datetime import timedelta

from odoo import fields, models
from odoo.exceptions import UserError


class HrPayslipRun(models.Model):
    _inherit = 'resta.payslip.batch'

    ess_published = fields.Boolean(string='Published for review', copy=False)
    ess_review_days = fields.Integer(string='Review period (days)', default=3)

    def _ess_slips(self):
        """Payslips of the batch; ``slip_ids`` is the historical field name (guarded)."""
        if 'slip_ids' in self._fields:
            return self.slip_ids
        return self.env['resta.payslip'].search([('batch_id', 'in', self.ids)])

    def action_ess_publish(self):
        compat = self.env['bs.hr.payroll.compat']
        draft_states = compat._draft_states()
        for run in self:
            slips = run._ess_slips().filtered(lambda s: s.state in draft_states)
            if not slips:
                raise UserError(self.env._('There is no draft payslip to publish in %s.', run.display_name))
            # inputs must be up to date before the employees look at them
            slips._ess_sync_adjustment_inputs()
            until = fields.Date.context_today(run) + timedelta(days=max(run.ess_review_days, 0))
            slips.write({'ess_published': True, 'ess_review_until': until})
            run.ess_published = True
            Notification = self.env['bs.hr.notification'].sudo()
            for slip in slips:
                lang = slip.employee_id.sudo().user_id.lang or self.env.lang
                lang_env = self.with_context(lang=lang).env
                Notification.create({
                    'employee_id': slip.employee_id.id,
                    'title': lang_env._('Your payslip draft is available'),
                    'body': lang_env._('Please review it before %s and raise an objection if something is wrong.',
                                       until),
                    'url': '/my/hr/payslip/%s' % slip.id, 'res_model': 'resta.payslip', 'res_id': slip.id,
                })
        return True

    def action_ess_unpublish(self):
        self._ess_slips().filtered('ess_published').write({'ess_published': False})
        self.write({'ess_published': False})
        return True
