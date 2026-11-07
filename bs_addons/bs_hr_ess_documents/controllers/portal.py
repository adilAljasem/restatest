from werkzeug.exceptions import NotFound

from odoo import _, fields, http
from odoo.exceptions import UserError, ValidationError
from odoo.http import request

from odoo.addons.bs_hr_ess_base.controllers.main import EssPortal, EssPortalBase


class EssDocumentsPortal(EssPortalBase):

    def _ess_get_own_document(self, doc_id, employee):
        return self._ess_get_own_record('bs.hr.document', doc_id, employee)

    # ------------------------------------------------------------------
    # documents
    # ------------------------------------------------------------------
    @http.route(['/my/hr/documents'], type='http', auth='user', website=True)
    def ess_documents(self, **kw):
        employee = self._ess_employee()
        if not employee:
            return self._ess_no_employee()
        Document = request.env['bs.hr.document'].sudo()
        documents = Document.search([('employee_id', '=', employee.id)])
        renewals = request.env['bs.hr.document.renewal'].sudo().search([
            ('employee_id', '=', employee.id), ('state', 'in', ('submitted', 'in_approval'))])
        pending = {r.document_id.id: r for r in renewals}
        return self._ess_render('bs_hr_ess_documents.ess_documents', employee, documents=documents,
                                pending=pending, missing_types=employee._ess_missing_document_types(),
                                flash=self._ess_pop_flash(), page_name='ess_documents')

    @http.route(['/my/hr/document/<int:doc_id>'], type='http', auth='user', website=True)
    def ess_document_detail(self, doc_id, **kw):
        employee = self._ess_employee()
        if not employee:
            return self._ess_no_employee()
        document = self._ess_get_own_document(doc_id, employee)
        renewals = request.env['bs.hr.document.renewal'].sudo().search([('document_id', '=', document.id)])
        return self._ess_render('bs_hr_ess_documents.ess_document_detail', employee, document=document,
                                renewals=renewals, flash=self._ess_pop_flash())

    @http.route(['/my/hr/document/<int:doc_id>/file/<int:att_id>'], type='http', auth='user')
    def ess_document_file(self, doc_id, att_id, **kw):
        employee = self._ess_employee()
        if not employee:
            raise NotFound()
        document = self._ess_get_own_document(doc_id, employee)
        attachment = document.attachment_ids.filtered(lambda a: a.id == att_id)
        if not attachment:
            raise NotFound()
        return request.env['ir.binary']._get_stream_from(attachment).get_response(as_attachment=True)

    @http.route(['/my/hr/document/<int:doc_id>/renew'], type='http', auth='user', website=True,
                methods=['GET', 'POST'])
    def ess_document_renew(self, doc_id, **post):
        employee = self._ess_employee()
        if not employee:
            return self._ess_no_employee()
        document = self._ess_get_own_document(doc_id, employee)
        url = '/my/hr/document/%s/renew' % document.id
        Renewal = request.env['bs.hr.document.renewal'].sudo()
        if request.httprequest.method == 'POST':
            if Renewal.search_count([('document_id', '=', document.id),
                                     ('state', 'in', ('submitted', 'in_approval'))]):
                return self._ess_error_redirect('/my/hr/documents',
                                                _('A renewal of this document is already in progress.'))
            try:
                files = self._ess_files(request.httprequest.files.getlist('attachments'))
                if not files:
                    raise ValidationError(_('Please attach a copy of the renewed document.'))
                new_expiry = fields.Date.to_date(post.get('new_expiry_date') or None)
                if not new_expiry:
                    raise ValidationError(_('Please enter the new expiry date.'))
                rec = Renewal.create({
                    'employee_id': employee.id,
                    'document_id': document.id,
                    'new_number': (post.get('new_number') or '').strip() or False,
                    'new_issue_date': fields.Date.to_date(post.get('new_issue_date') or None) or False,
                    'new_expiry_date': new_expiry,
                    'employee_note': post.get('employee_note') or False,
                })
                self._ess_attach(rec, files)
                self._ess_submit(rec)
            except (UserError, ValidationError, ValueError) as e:
                request.env.cr.rollback()
                return self._ess_error_redirect(url, e.args[0] if e.args else str(e))
            return self._ess_ok_redirect(rec._ess_portal_url(),
                                         _('Your renewed document was sent to HR for verification.'))
        return self._ess_render('bs_hr_ess_documents.ess_document_renew', employee, document=document,
                                flash=self._ess_pop_flash())

    # ------------------------------------------------------------------
    # contract
    # ------------------------------------------------------------------
    @http.route(['/my/hr/contract'], type='http', auth='user', website=True)
    def ess_contract(self, **kw):
        employee = self._ess_employee()
        if not employee:
            return self._ess_no_employee()
        version = employee.sudo().version_id
        Renewal = request.env['bs.hr.contract.renewal'].sudo()
        # renewals waiting for the employee's acknowledgement
        to_ack = Renewal.search([('employee_id', '=', employee.id), ('state', '=', 'done'),
                                 ('new_version_id', '!=', False), ('employee_ack_date', '=', False)])
        in_progress = Renewal.search([('employee_id', '=', employee.id),
                                      ('state', 'in', ('submitted', 'in_approval'))], limit=1)
        history = Renewal.search([('employee_id', '=', employee.id), ('state', '=', 'done'),
                                  ('new_version_id', '!=', False)], limit=10)
        future = request.env['hr.version'].sudo().search([
            ('employee_id', '=', employee.id), ('date_version', '>', fields.Date.context_today(employee))],
            order='date_version')
        return self._ess_render('bs_hr_ess_documents.ess_contract', employee, version=version,
                                to_ack=to_ack, in_progress=in_progress, history=history,
                                future_versions=future, flash=self._ess_pop_flash(),
                                page_name='ess_contract')

    @http.route(['/my/hr/contract/<int:renewal_id>/acknowledge'], type='http', auth='user',
                website=True, methods=['POST'])
    def ess_contract_acknowledge(self, renewal_id, **kw):
        employee = self._ess_employee()
        if not employee:
            raise NotFound()
        renewal = self._ess_get_own_record('bs.hr.contract.renewal', renewal_id, employee)
        try:
            renewal.with_user(request.env.user).sudo().action_ess_acknowledge()
        except UserError as e:
            request.env.cr.rollback()
            return self._ess_error_redirect('/my/hr/contract', e.args[0])
        return self._ess_ok_redirect('/my/hr/contract', _('Thank you, your acknowledgement was recorded.'))


class EssPortalContractGuard(EssPortal):
    """Contract renewals are internal workflows about the employee: the employee only sees the
    outcome on /my/hr/contract, never the approval timeline / comments, and cannot cancel them."""

    _ess_internal_models = ('bs.hr.contract.renewal',)

    @http.route()
    def ess_request_detail(self, model, rec_id, **kw):
        if model in self._ess_internal_models:
            employee = self._ess_employee()
            self._ess_get_own_record(model, rec_id, employee)
            return request.redirect('/my/hr/contract')
        return super().ess_request_detail(model, rec_id, **kw)

    @http.route()
    def ess_request_cancel(self, model, rec_id, **kw):
        if model in self._ess_internal_models:
            raise NotFound()
        return super().ess_request_cancel(model, rec_id, **kw)

    @http.route()
    def ess_request_comment(self, model, rec_id, body=None, **kw):
        if model in self._ess_internal_models:
            raise NotFound()
        return super().ess_request_comment(model, rec_id, body=body, **kw)
