from odoo import api, fields, models
from odoo.exceptions import ValidationError


class BsHrDocumentRenewal(models.Model):
    _name = 'bs.hr.document.renewal'
    _description = 'Document Renewal'
    _inherit = ['bs.hr.request.mixin']
    _ess_sequence_code = 'bs.hr.document.renewal'
    _ess_auto_done = True

    document_id = fields.Many2one('bs.hr.document', required=True, index=True, ondelete='cascade',
                                  domain="[('employee_id', '=', employee_id)]", tracking=True)
    type_id = fields.Many2one(related='document_id.type_id', store=True, string='Document type')
    old_number = fields.Char(related='document_id.number', string='Current number')
    old_expiry_date = fields.Date(related='document_id.expiry_date', string='Current expiry date')
    new_number = fields.Char(string='New number', tracking=True)
    new_issue_date = fields.Date(string='New issue date')
    new_expiry_date = fields.Date(string='New expiry date', required=True, tracking=True)
    currency_id = fields.Many2one(related='company_id.currency_id')
    renewal_cost = fields.Monetary(string='Renewal cost', help='Fees paid for this renewal (filled by HR).')
    attachment_count = fields.Integer(compute='_compute_attachment_count', string='Files')

    @api.constrains('document_id', 'employee_id')
    def _check_document_employee(self):
        for rec in self:
            if rec.document_id.employee_id != rec.employee_id:
                raise ValidationError(self.env._('The document must belong to the employee.'))

    @api.constrains('new_issue_date', 'new_expiry_date')
    def _check_dates(self):
        for rec in self:
            if rec.new_issue_date and rec.new_expiry_date and rec.new_issue_date > rec.new_expiry_date:
                raise ValidationError(self.env._('The expiry date must be after the issue date.'))

    def _ess_attachments(self):
        self.ensure_one()
        return self.env['ir.attachment'].sudo().search([('res_model', '=', self._name), ('res_id', '=', self.id)])

    def _compute_attachment_count(self):
        for rec in self:
            rec.attachment_count = len(rec._ess_attachments()) if rec.id else 0

    def _ess_check_submit(self):
        for rec in self:
            if not rec._ess_attachments():
                raise ValidationError(self.env._('Please attach a copy of the renewed document.'))
            if rec.new_expiry_date and rec.new_expiry_date <= fields.Date.context_today(rec):
                raise ValidationError(self.env._('The new expiry date must be in the future.'))

    def _ess_portal_summary(self):
        self.ensure_one()
        return '%s - %s' % (self.document_id.type_id.name or '', self.new_expiry_date or '')

    def _ess_portal_detail_fields(self):
        return ['document_id', 'old_expiry_date', 'new_number', 'new_issue_date', 'new_expiry_date']

    def _ess_on_approved(self):
        for rec in self:
            doc = rec.document_id.sudo()
            vals = {'expiry_date': rec.new_expiry_date}
            if rec.new_number:
                vals['number'] = rec.new_number
            if rec.new_issue_date:
                vals['issue_date'] = rec.new_issue_date
            if rec.renewal_cost:
                vals['renewal_cost'] = rec.renewal_cost
            doc.write(vals)
            doc._ess_post_files(
                rec._ess_attachments(),
                self.env._('Document renewed by request %(name)s: new expiry date %(date)s.',
                           name=rec.name, date=rec.new_expiry_date))
