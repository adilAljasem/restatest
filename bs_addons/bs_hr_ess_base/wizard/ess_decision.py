from odoo import fields, models


class BsHrEssDecisionWizard(models.TransientModel):
    _name = 'bs.hr.ess.decision.wizard'
    _description = 'ESS approve / reject wizard'

    res_model = fields.Char(required=True)
    res_ids = fields.Char(required=True)
    decision = fields.Selection([('approve', 'Approve'), ('reject', 'Reject')], required=True, default='approve')
    comment = fields.Text(string='Comment / Reason')

    def action_confirm(self):
        self.ensure_one()
        ids = [int(i) for i in self.res_ids.split(',') if i]
        records = self.env[self.res_model].browse(ids)
        if self.decision == 'approve':
            records.action_ess_approve(self.comment)
        else:
            records.action_ess_reject(self.comment)
        return {'type': 'ir.actions.act_window_close'}
