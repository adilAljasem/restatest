"""Defensive helpers around the Odoo Enterprise ``hr_payroll`` module.

The Enterprise source is not available to the developers of this module, so every name that
was not verified (see README) goes through these helpers, which check the registry / the
selection of the ``state`` field at runtime instead of assuming it.
"""
from odoo import api, models

# States in which a payslip can still be (re)computed.
DRAFT_STATES = ('draft', 'verify')
# States of a confirmed payslip, in the order they happen ("validated" is used by some versions).
CONFIRMED_STATES = ('validated', 'done', 'paid')
CANCEL_STATES = ('cancel', 'cancelled')

# Input types (xmlid, code) tried in order.
DEDUCTION_TYPES = [('hr_payroll.input_deduction', 'DEDUCTION'),
                   ('bs_hr_ess_payroll.input_type_bs_deduction', 'BSDED')]
ADDITION_TYPES = [('hr_payroll.input_reimbursement', 'REIMBURSEMENT'),
                  ('bs_hr_ess_payroll.input_type_bs_addition', 'BSADD')]

REPORT_XMLIDS = ('hr_payroll.action_report_payslip', 'hr_payroll.action_report_light_payslip')


class BsHrPayrollCompat(models.AbstractModel):
    _name = 'bs.hr.payroll.compat'
    _description = 'ESS Payroll compatibility helpers'

    @api.model
    def _state_keys(self):
        field = self.env['hr.payslip']._fields['state']
        return [key for key, _label in field._description_selection(self.env)]

    @api.model
    def _filter_states(self, wanted):
        keys = set(self._state_keys())
        return [s for s in wanted if s in keys]

    @api.model
    def _draft_states(self):
        return self._filter_states(DRAFT_STATES) or ['draft']

    @api.model
    def _confirmed_states(self):
        return self._filter_states(CONFIRMED_STATES)

    @api.model
    def _cancel_states(self):
        return self._filter_states(CANCEL_STATES)

    @api.model
    def _input_type(self, adj_type):
        """The hr.payslip.input.type used for an adjustment type ('deduction' / 'addition')."""
        env = self.sudo().env
        InputType = env['hr.payslip.input.type'].with_context(active_test=False)
        for xmlid, code in (DEDUCTION_TYPES if adj_type == 'deduction' else ADDITION_TYPES):
            rec = env.ref(xmlid, raise_if_not_found=False)
            if rec and rec._name == 'hr.payslip.input.type' and self._is_active(rec):
                return rec.sudo()
            rec = InputType.search([('code', '=', code)], limit=1)
            if rec and self._is_active(rec):
                return rec
        return InputType.browse()

    @api.model
    def _is_active(self, record):
        return 'active' not in record._fields or record.active

    @api.model
    def _payslip_report(self):
        env = self.sudo().env  # portal users cannot read ir.actions.report
        for xmlid in REPORT_XMLIDS:
            report = env.ref(xmlid, raise_if_not_found=False)
            if report and report._name == 'ir.actions.report' and report.report_type == 'qweb-pdf':
                return report.sudo()
        return env['ir.actions.report'].search(
            [('model', '=', 'hr.payslip'), ('report_type', '=', 'qweb-pdf')], order='id', limit=1)

    @api.model
    def _net_amount(self, slip):
        if 'net_wage' in slip._fields:
            return slip.net_wage
        line = slip.line_ids.filtered(lambda l: l.code == 'NET')[:1]
        return line.total if line else 0.0

    @api.model
    def _slip_lines(self, slip):
        lines = slip.sudo().line_ids
        if 'appears_on_payslip' in lines._fields:
            lines = lines.filtered('appears_on_payslip')
        if 'sequence' in lines._fields:
            lines = lines.sorted(lambda l: (l.sequence, l.id))
        return lines

    @api.model
    def _slip_reference(self, slip):
        return ('number' in slip._fields and slip.number) or slip.name or ''
