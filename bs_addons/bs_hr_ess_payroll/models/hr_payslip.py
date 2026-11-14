import logging

from odoo import api, fields, models
from odoo.fields import Domain

_logger = logging.getLogger(__name__)


class HrPayslipInput(models.Model):
    _inherit = 'resta.payslip.input'

    ess_adjustment_id = fields.Many2one('bs.hr.payroll.adjustment', string='ESS adjustment', index=True,
                                        ondelete='set null', copy=False,
                                        help='Self-service adjustment (loan, custody, penalty, objection...) '
                                             'at the origin of this input.')
    ess_source_ref = fields.Char(related='ess_adjustment_id.source_ref', string='Source')


class HrPayslip(models.Model):
    _inherit = 'resta.payslip'

    ess_adjustment_ids = fields.One2many('bs.hr.payroll.adjustment', 'payslip_id', string='ESS adjustments')
    ess_published = fields.Boolean(
        string='Published for review', copy=False, index=True,
        help='The draft payslip is visible in the employee portal as "Draft for review" so that the '
             'employee can raise an objection before the payroll is closed.')
    ess_review_until = fields.Date(string='Review until', copy=False)
    ess_objection_ids = fields.One2many('bs.hr.payslip.objection', 'payslip_id', string='Objections')

    # ------------------------------------------------------------------
    # adjustments -> inputs
    # ------------------------------------------------------------------
    def _ess_adjustment_domain(self):
        self.ensure_one()
        return Domain([
            ('employee_id', '=', self.employee_id.id),
            ('company_id', '=', self.company_id.id),
            ('state', '=', 'pending'),
            ('date', '<=', self.date_to),
        ]) & (Domain('payslip_id', '=', False) | Domain('payslip_id', '=', self.id))

    def _ess_input_vals(self, adjustment, input_type):
        vals = {
            'slip_id': self.id,
            'type_id': input_type.id,
            'amount': adjustment.amount,
            'ess_adjustment_id': adjustment.id,
        }
        Input = self.env['resta.payslip.input']
        label = adjustment.name
        if adjustment.source_ref:
            label = '%s (%s)' % (adjustment.name, adjustment.source_ref)
        name_field = Input._fields.get('name')
        if name_field and not (name_field.compute and name_field.readonly):
            vals['name'] = label
        elif 'note' in Input._fields:
            vals['note'] = label
        return vals

    def _ess_sync_adjustment_inputs(self):
        """Turn the pending ESS adjustments into payslip input lines (idempotent)."""
        compat = self.env['bs.hr.payroll.compat']
        draft_states = compat._draft_states()
        Adjustment = self.env['bs.hr.payroll.adjustment'].sudo().with_context(ess_payroll_sync=True)
        Input = self.env['resta.payslip.input'].sudo()
        for slip in self.sudo().filtered(lambda s: s.state in draft_states and s.employee_id and s.date_to):
            existing = Input.search([('slip_id', '=', slip.id), ('ess_adjustment_id', '!=', False)])
            wanted = Adjustment.search(slip._ess_adjustment_domain())
            # adjustments no longer applicable (cancelled, moved, amount changed) -> drop their input
            stale = existing.filtered(
                lambda i: i.ess_adjustment_id not in wanted or i.amount != i.ess_adjustment_id.amount)
            if stale:
                stale.ess_adjustment_id.filtered(lambda a: a.payslip_id == slip).write(
                    {'payslip_id': False, 'payslip_input_id': False})
                stale.unlink()
            already = (existing - stale).ess_adjustment_id
            vals_list, todo = [], Adjustment.browse()
            for adj in wanted - already:
                input_type = compat._input_type(adj.adj_type)
                if not input_type:
                    _logger.warning('No payslip input type for ESS %s adjustments: %s skipped',
                                    adj.adj_type, adj.display_name)
                    continue
                vals_list.append(slip._ess_input_vals(adj, input_type))
                todo |= adj
            inputs = Input.create(vals_list) if vals_list else Input
            for adj, inp in zip(todo, inputs):
                adj.write({'payslip_id': slip.id, 'payslip_input_id': inp.id})
            # keep links consistent for adjustments already injected
            (existing - stale).filtered(lambda i: i.ess_adjustment_id.payslip_id != slip).ess_adjustment_id.write(
                {'payslip_id': slip.id})
        return True

    def compute_sheet(self):
        self._ess_sync_adjustment_inputs()
        return super().compute_sheet()

    # ------------------------------------------------------------------
    # state follow-up: done -> applied, cancel / draft -> pending
    # ------------------------------------------------------------------
    def _ess_sync_adjustment_states(self):
        compat = self.env['bs.hr.payroll.compat']
        confirmed = compat._confirmed_states()
        cancelled = compat._cancel_states()
        Adjustment = self.env['bs.hr.payroll.adjustment'].sudo().with_context(ess_payroll_sync=True)
        for slip in self.sudo():
            adjustments = Adjustment.search([('payslip_id', '=', slip.id)])
            if not adjustments:
                continue
            if slip.state in confirmed:
                adjustments.filtered(lambda a: a.state == 'pending').write({
                    'state': 'applied', 'applied_date': slip.date_to or fields.Date.context_today(slip)})
            elif slip.state in cancelled:
                adjustments.filtered(lambda a: a.state != 'cancelled').write({
                    'state': 'pending', 'applied_date': False, 'payslip_id': False, 'payslip_input_id': False})
            else:  # back to draft: pending again, still linked to this payslip
                adjustments.filtered(lambda a: a.state == 'applied').write(
                    {'state': 'pending', 'applied_date': False})

    def write(self, vals):
        res = super().write(vals)
        if 'state' in vals:
            self._ess_sync_adjustment_states()
            if vals['state'] not in self.env['bs.hr.payroll.compat']._draft_states():
                self.filtered('ess_published').write({'ess_published': False})
        return res


    def unlink(self):
        adjustments = self.env['bs.hr.payroll.adjustment'].sudo().search([('payslip_id', 'in', self.ids)])
        adjustments.with_context(ess_payroll_sync=True).filtered(lambda a: a.state == 'pending').write(
            {'payslip_id': False, 'payslip_input_id': False})
        return super().unlink()

    # ------------------------------------------------------------------
    # portal helpers
    # ------------------------------------------------------------------
    def _ess_portal_visible(self):
        """Confirmed payslips, or drafts published for review."""
        self.ensure_one()
        compat = self.env['bs.hr.payroll.compat']
        return self.state in compat._confirmed_states() or (
            self.ess_published and self.state in compat._draft_states())

    @api.model
    def _ess_portal_domain(self, employee):
        compat = self.env['bs.hr.payroll.compat']
        return Domain('employee_id', '=', employee.id) & (
            Domain('state', 'in', compat._confirmed_states())
            | (Domain('ess_published', '=', True) & Domain('state', 'in', compat._draft_states())))

    def _ess_is_draft_review(self):
        self.ensure_one()
        return self.state in self.env['bs.hr.payroll.compat']._draft_states()

    def action_ess_open_adjustments(self):
        action = self.env['ir.actions.act_window']._for_xml_id('bs_hr_ess_base.action_adjustment')
        action['domain'] = [('payslip_id', 'in', self.ids)]
        action['context'] = {}
        return action
