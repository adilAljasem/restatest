from odoo import api, fields, models
from odoo.exceptions import UserError


class BsHrClearance(models.Model):
    _name = 'bs.hr.clearance'
    _description = 'ESS Employee Clearance'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date desc, id desc'

    name = fields.Char(compute='_compute_name', store=True)
    employee_id = fields.Many2one('hr.employee', required=True, index=True, tracking=True)
    company_id = fields.Many2one(related='employee_id.company_id', store=True, index=True)
    date = fields.Date(required=True, default=fields.Date.context_today, tracking=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('blocked', 'Blocked'),
        ('done', 'Cleared'),
    ], default='draft', required=True, tracking=True)
    blocker_ids = fields.One2many('bs.hr.clearance.blocker', 'clearance_id', string='Blockers', readonly=True)
    blocker_count = fields.Integer(compute='_compute_blocker_count')
    note = fields.Text()

    @api.depends('employee_id', 'date')
    def _compute_name(self):
        for rec in self:
            rec.name = self.env._('Clearance - %s', rec.employee_id.name) if rec.employee_id else '/'

    @api.depends('blocker_ids')
    def _compute_blocker_count(self):
        for rec in self:
            rec.blocker_count = len(rec.blocker_ids)

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._ess_refresh_blockers()
        return records

    def _ess_refresh_blockers(self):
        for rec in self:
            rec.blocker_ids = [(5, 0, 0)] + [(0, 0, v) for v in rec._ess_get_blockers()]

    def _ess_get_blockers(self):
        """List of dicts {category, name, res_model, res_id, amount} preventing the clearance."""
        self.ensure_one()
        employee = self.employee_id
        blockers = []
        holdings = self.env['bs.hr.custody.holding'].sudo().search(
            [('employee_id', '=', employee.id), ('state', '=', 'held')])
        for holding in holdings:
            blockers.append({'category': 'custody', 'name': holding.display_name, 'res_model': holding._name,
                             'res_id': holding.id, 'amount': holding.value})
        loans = self.env['bs.hr.loan'].sudo().search(
            [('employee_id', '=', employee.id), ('state', 'in', ('submitted', 'in_approval', 'approved'))])
        for loan in loans.filtered(lambda l: l.remaining_amount > 0 or l.state != 'approved'):
            blockers.append({'category': 'loan', 'name': loan.display_name, 'res_model': loan._name,
                             'res_id': loan.id, 'amount': loan.remaining_amount or loan.amount})
        for model_name, _label in self.env['bs.hr.approval.flow']._selection_res_model():
            if model_name == 'bs.hr.loan':
                continue
            records = self.env[model_name].sudo().search(
                [('employee_id', '=', employee.id), ('state', 'in', ('submitted', 'in_approval'))])
            for record in records:
                blockers.append({'category': 'request', 'name': '%s (%s)' % (record.display_name, record._description),
                                 'res_model': record._name, 'res_id': record.id, 'amount': 0.0})
        return blockers

    def action_check(self):
        for rec in self:
            if rec.state == 'done':
                continue
            rec._ess_refresh_blockers()
            rec.state = 'blocked' if rec.blocker_ids else 'draft'
        return True

    def action_complete(self):
        for rec in self:
            rec.action_check()
            if rec.blocker_ids:
                raise UserError(self.env._('The clearance of %s still has open items.', rec.employee_id.name))
            rec.state = 'done'
            rec.message_post(body=self.env._('Clearance completed.'))
        return True

    def action_reset(self):
        self.write({'state': 'draft'})
        return self.action_check()


class BsHrClearanceBlocker(models.Model):
    _name = 'bs.hr.clearance.blocker'
    _description = 'ESS Clearance Blocker'

    clearance_id = fields.Many2one('bs.hr.clearance', required=True, ondelete='cascade', index=True)
    category = fields.Selection([('custody', 'Custody'), ('loan', 'Loan'), ('request', 'Open request')],
                                required=True)
    name = fields.Char(required=True)
    res_model = fields.Char()
    res_id = fields.Many2oneReference(model_field='res_model')
    amount = fields.Float()

    def action_open(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': self.res_model, 'res_id': self.res_id,
                'view_mode': 'form'}
