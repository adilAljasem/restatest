import logging

from dateutil.relativedelta import relativedelta

from werkzeug.exceptions import NotFound

from odoo import _, fields, http
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.http import request

from odoo.addons.bs_hr_ess_base.controllers.main import EssPortalBase

_logger = logging.getLogger(__name__)


def _to_float(value):
    try:
        return float(str(value or '0').replace(',', '').strip() or 0)
    except ValueError:
        return 0.0


def _to_int(value, default=0):
    try:
        return int(str(value or '').strip())
    except ValueError:
        return default


def _to_date(value):
    try:
        return fields.Date.to_date(value) if value else False
    except ValueError:
        return False


class EssRequestsPortal(EssPortalBase):

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _ess_create_and_submit(self, model, vals, error_url, files=None):
        """Create a request in sudo for the logged employee, attach files and submit.

        Returns (record, None) or (None, redirect response).
        """
        try:
            record = request.env[model].sudo().with_context(mail_create_nosubscribe=True).create(vals)
            if files:
                self._ess_attach(record, files)
            self._ess_submit(record)
        except (UserError, ValidationError, AccessError) as e:
            request.env.cr.rollback()
            return None, self._ess_error_redirect(error_url, e.args[0])
        return record, None

    def _ess_get_owned(self, model, rec_id, employee):
        return self._ess_get_own_record(model, rec_id, employee)

    # ------------------------------------------------------------------
    # LOANS
    # ------------------------------------------------------------------
    def _ess_loan_types(self, employee):
        return request.env['bs.hr.loan.type'].sudo().search(
            [('company_id', 'in', [False, employee.company_id.id])])

    @http.route(['/my/hr/loans'], type='http', auth='user', website=True)
    def ess_loans(self, **kw):
        employee = self._ess_employee()
        if not employee:
            return self._ess_no_employee()
        loans = request.env['bs.hr.loan'].sudo().search([('employee_id', '=', employee.id)], limit=100)
        remaining = sum(loans.filtered(lambda l: l.state == 'approved').mapped('remaining_amount'))
        return self._ess_render('bs_hr_ess_requests.ess_loans', employee, loans=loans, remaining=remaining,
                                currency=employee.company_id.currency_id, flash=self._ess_pop_flash(),
                                page_name='ess_loans')

    @http.route(['/my/hr/loan/new'], type='http', auth='user', website=True, methods=['GET', 'POST'])
    def ess_loan_new(self, **post):
        employee = self._ess_employee()
        if not employee:
            return self._ess_no_employee()
        types = self._ess_loan_types(employee)
        limits = {t.id: t._ess_limits(employee) for t in types}
        loan_type = types.filtered(lambda t: t.id == _to_int(post.get('loan_type_id')))[:1] or types[:1]
        values = {
            'amount': _to_float(post.get('amount')),
            'installment_count': max(_to_int(post.get('installment_count'), 1), 1),
            'first_installment_date': _to_date(post.get('first_installment_date'))
            or fields.Date.context_today(employee).replace(day=1) + relativedelta(months=1),
            'employee_note': post.get('employee_note') or '',
        }
        schedule = []
        if request.httprequest.method == 'POST' and loan_type:
            if post.get('action') == 'submit':
                record, error = self._ess_create_and_submit('bs.hr.loan', {
                    'employee_id': employee.id, 'loan_type_id': loan_type.id, 'amount': values['amount'],
                    'installment_count': values['installment_count'],
                    'first_installment_date': values['first_installment_date'],
                    'employee_note': values['employee_note'] or False,
                }, '/my/hr/loan/new')
                if error:
                    return error
                return self._ess_ok_redirect(record._ess_portal_url(), _('Your loan request was sent.'))
            schedule = request.env['bs.hr.loan.type']._ess_schedule(
                values['amount'], values['installment_count'], values['first_installment_date'])
        return self._ess_render('bs_hr_ess_requests.ess_loan_new', employee, types=types, limits=limits,
                                loan_type=loan_type, values=values, schedule=schedule,
                                currency=employee.company_id.currency_id, flash=self._ess_pop_flash())

    @http.route(['/my/hr/loan/<int:rec_id>'], type='http', auth='user', website=True)
    def ess_loan_detail(self, rec_id, **kw):
        employee = self._ess_employee()
        if not employee:
            return self._ess_no_employee()
        loan = self._ess_get_owned('bs.hr.loan', rec_id, employee)
        pending_postpone = request.env['bs.hr.loan.postpone'].sudo().search([
            ('loan_id', '=', loan.id), ('state', 'in', ('submitted', 'in_approval'))]).loan_line_id
        return self._ess_render('bs_hr_ess_requests.ess_loan_detail', employee, record=loan,
                                lines=loan._ess_lines(), pending_postpone=pending_postpone,
                                currency=loan.currency_id, flash=self._ess_pop_flash())

    @http.route(['/my/hr/loan/line/<int:line_id>/postpone'], type='http', auth='user', website=True,
                methods=['POST'])
    def ess_loan_postpone(self, line_id, reason=None, **kw):
        employee = self._ess_employee()
        if not employee:
            raise NotFound()
        line = request.env['bs.hr.loan.line'].sudo().browse(line_id).exists()
        if not line or line.loan_id.employee_id != employee:
            raise NotFound()
        url = line.loan_id._ess_portal_url()
        if not (reason or '').strip():
            return self._ess_error_redirect(url, _('Please give a reason for the postponement.'))
        record, error = self._ess_create_and_submit('bs.hr.loan.postpone', {
            'employee_id': employee.id, 'loan_line_id': line.id, 'reason': reason.strip()}, url)
        if error:
            return error
        return self._ess_ok_redirect(url, _('Your postponement request was sent.'))

    # ------------------------------------------------------------------
    # LEAVES
    # ------------------------------------------------------------------
    @http.route(['/my/hr/leaves'], type='http', auth='user', website=True)
    def ess_leaves(self, **kw):
        employee = self._ess_employee()
        if not employee:
            return self._ess_no_employee()
        balances = employee._ess_leave_balances()
        requests_ = request.env['bs.hr.leave.request'].sudo().search([('employee_id', '=', employee.id)], limit=100)
        return self._ess_render('bs_hr_ess_requests.ess_leaves', employee, balances=balances,
                                leave_requests=requests_, flash=self._ess_pop_flash(), page_name='ess_leaves')

    @http.route(['/my/hr/leave/new'], type='http', auth='user', website=True, methods=['GET', 'POST'])
    def ess_leave_new(self, **post):
        employee = self._ess_employee()
        if not employee:
            return self._ess_no_employee()
        leave_types = employee._ess_requestable_leave_types()
        if request.httprequest.method == 'POST':
            leave_type = leave_types.filtered(lambda t: t.id == _to_int(post.get('leave_type_id')))
            date_from, date_to = _to_date(post.get('date_from')), _to_date(post.get('date_to'))
            if not leave_type or not date_from or not date_to:
                return self._ess_error_redirect('/my/hr/leave/new', _('Please choose a type and the dates.'))
            if date_to < date_from:
                return self._ess_error_redirect('/my/hr/leave/new', _('The end date must be after the start date.'))
            try:
                files = self._ess_files(request.httprequest.files.getlist('attachments'))
            except ValidationError as e:
                return self._ess_error_redirect('/my/hr/leave/new', e.args[0])
            record, error = self._ess_create_and_submit('bs.hr.leave.request', {
                'employee_id': employee.id, 'leave_type_id': leave_type.id,
                'date_from': date_from, 'date_to': date_to,
                'need_ticket': bool(post.get('need_ticket')),
                'need_exit_reentry': bool(post.get('need_exit_reentry')),
                'advance_salary': bool(post.get('advance_salary')),
                'employee_note': post.get('employee_note') or False,
            }, '/my/hr/leave/new', files=files)
            if error:
                return error
            return self._ess_ok_redirect(record._ess_portal_url(), _('Your time off request was sent.'))
        balances = {lt.id: info for lt, info in employee._ess_leave_balances()}
        return self._ess_render('bs_hr_ess_requests.ess_leave_new', employee, leave_types=leave_types,
                                balances=balances, flash=self._ess_pop_flash())

    # ------------------------------------------------------------------
    # CUSTODY
    # ------------------------------------------------------------------
    def _ess_custody_products(self, employee):
        return request.env['product.product'].sudo().search([
            ('ess_is_custody', '=', True), ('company_id', 'in', [False, employee.company_id.id])])

    @http.route(['/my/hr/custody'], type='http', auth='user', website=True)
    def ess_custody(self, **kw):
        employee = self._ess_employee()
        if not employee:
            return self._ess_no_employee()
        holdings = request.env['bs.hr.custody.holding'].sudo().search([('employee_id', '=', employee.id)])
        custody_requests = request.env['bs.hr.custody.request'].sudo().search(
            [('employee_id', '=', employee.id)], limit=50)
        return self._ess_render('bs_hr_ess_requests.ess_custody', employee,
                                holdings=holdings.filtered(lambda h: h.state == 'held'),
                                past_holdings=holdings.filtered(lambda h: h.state != 'held')[:30],
                                custody_requests=custody_requests, flash=self._ess_pop_flash(),
                                page_name='ess_custody')

    @http.route(['/my/hr/custody/<int:holding_id>/ack'], type='http', auth='user', website=True, methods=['POST'])
    def ess_custody_ack(self, holding_id, **kw):
        employee = self._ess_employee()
        holding = self._ess_get_owned('bs.hr.custody.holding', holding_id, employee)
        if holding.state == 'held' and not holding.ack_date:
            holding.action_acknowledge()
            holding.message_post(body=_('Receipt acknowledged electronically by %s from the portal.',
                                        employee.name))
        return self._ess_ok_redirect('/my/hr/custody', _('Thank you, your acknowledgement is recorded.'))

    @http.route(['/my/hr/custody/new'], type='http', auth='user', website=True, methods=['GET', 'POST'])
    def ess_custody_new(self, **post):
        employee = self._ess_employee()
        if not employee:
            return self._ess_no_employee()
        products = self._ess_custody_products(employee)
        holdings = request.env['bs.hr.custody.holding'].sudo().search(
            [('employee_id', '=', employee.id), ('state', '=', 'held')])
        request_type = post.get('request_type') or request.httprequest.args.get('type') or 'new'
        if request_type not in ('new', 'replace', 'return', 'damage', 'loss'):
            request_type = 'new'
        if request.httprequest.method == 'POST':
            url = '/my/hr/custody/new?type=%s' % request_type
            line = {'quantity': max(_to_float(post.get('quantity')) or 1.0, 0.01), 'size': post.get('size') or False}
            if request_type == 'new':
                product = products.filtered(lambda p: p.id == _to_int(post.get('product_id')))
                if not product:
                    return self._ess_error_redirect(url, _('Please choose an item.'))
                line['product_id'] = product.id
            else:
                holding = holdings.filtered(lambda h: h.id == _to_int(post.get('holding_id')))
                if not holding:
                    return self._ess_error_redirect(url, _('Please choose the item you hold.'))
                line.update({'holding_id': holding.id, 'product_id': holding.product_id.id,
                             'quantity': holding.quantity, 'size': line['size'] or holding.size})
            try:
                files = self._ess_files(request.httprequest.files.getlist('attachments'))
            except ValidationError as e:
                return self._ess_error_redirect(url, e.args[0])
            record, error = self._ess_create_and_submit('bs.hr.custody.request', {
                'employee_id': employee.id, 'request_type': request_type,
                'reason': post.get('reason') or False, 'line_ids': [(0, 0, line)],
            }, url, files=files)
            if error:
                return error
            return self._ess_ok_redirect(record._ess_portal_url(), _('Your custody request was sent.'))
        return self._ess_render('bs_hr_ess_requests.ess_custody_new', employee, products=products,
                                holdings=holdings, request_type=request_type, flash=self._ess_pop_flash())

    # ------------------------------------------------------------------
    # EXPENSES (native hr.expense approval)
    # ------------------------------------------------------------------
    def _ess_expense_products(self, employee):
        return request.env['product.product'].sudo().search([
            ('can_be_expensed', '=', True), ('standard_price', '=', 0),
            ('company_id', 'in', [False, employee.company_id.id])])

    @http.route(['/my/hr/expenses'], type='http', auth='user', website=True)
    def ess_expenses(self, **kw):
        employee = self._ess_employee()
        if not employee:
            return self._ess_no_employee()
        expenses = request.env['hr.expense'].sudo().search([('employee_id', '=', employee.id)],
                                                           order='date desc, id desc', limit=100)
        return self._ess_render('bs_hr_ess_requests.ess_expenses', employee, expenses=expenses,
                                flash=self._ess_pop_flash(), page_name='ess_expenses')

    @http.route(['/my/hr/expense/new'], type='http', auth='user', website=True, methods=['GET', 'POST'])
    def ess_expense_new(self, **post):
        employee = self._ess_employee()
        if not employee:
            return self._ess_no_employee()
        products = self._ess_expense_products(employee)
        if request.httprequest.method == 'POST':
            url = '/my/hr/expense/new'
            product = products.filtered(lambda p: p.id == _to_int(post.get('product_id')))
            amount = _to_float(post.get('amount'))
            if not product or amount <= 0:
                return self._ess_error_redirect(url, _('Please choose a category and a positive amount.'))
            try:
                files = self._ess_files(request.httprequest.files.getlist('receipt'))
                if not files:
                    raise ValidationError(_('Please take a photo of the receipt.'))
                expense = request.env['hr.expense'].sudo().with_context(mail_create_nosubscribe=True).create({
                    'name': (post.get('description') or product.name or '').strip()[:250] or product.name,
                    'employee_id': employee.id,
                    'product_id': product.id,
                    'quantity': 1,
                    'total_amount_currency': amount,
                    'date': _to_date(post.get('date')) or fields.Date.context_today(employee),
                    'description': post.get('description') or False,
                    'payment_mode': 'own_account',
                    'company_id': employee.company_id.id,
                })
                attachments = self._ess_attach(expense, files)
                expense.message_main_attachment_id = attachments[:1]
                expense.with_user(request.env.user).sudo().action_submit()
            except (UserError, ValidationError, AccessError) as e:
                request.env.cr.rollback()
                return self._ess_error_redirect(url, e.args[0])
            return self._ess_ok_redirect('/my/hr/expenses', _('Your expense was submitted.'))
        return self._ess_render('bs_hr_ess_requests.ess_expense_new', employee, products=products,
                                currency=employee.company_id.currency_id, flash=self._ess_pop_flash())

    # ------------------------------------------------------------------
    # GENERIC REQUESTS
    # ------------------------------------------------------------------
    def _ess_request_type(self, type_id, employee):
        rtype = request.env['bs.hr.request.type'].sudo().browse(type_id).exists()
        if not rtype or not rtype.active or (rtype.company_id and rtype.company_id != employee.company_id):
            raise NotFound()
        return rtype

    @http.route(['/my/hr/request/new/<int:type_id>'], type='http', auth='user', website=True,
                methods=['GET', 'POST'])
    def ess_generic_request_new(self, type_id, **post):
        employee = self._ess_employee()
        if not employee:
            return self._ess_no_employee()
        rtype = self._ess_request_type(type_id, employee)
        url = '/my/hr/request/new/%s' % rtype.id
        if request.httprequest.method == 'POST':
            raw = {f.key: post.get('f_%s' % f.key) for f in rtype.field_ids}
            try:
                clean = {f.key: f._parse_value(raw.get(f.key)) for f in rtype.field_ids}
                files = self._ess_files(request.httprequest.files.getlist('attachments'))
            except ValidationError as e:
                return self._ess_error_redirect(url, e.args[0])
            record, error = self._ess_create_and_submit('bs.hr.request', {
                'employee_id': employee.id, 'type_id': rtype.id, 'values': clean,
                'employee_note': post.get('employee_note') or False,
            }, url, files=files)
            if error:
                return error
            return self._ess_ok_redirect(record._ess_portal_url(), _('Your request was sent.'))
        return self._ess_render('bs_hr_ess_requests.ess_generic_request_new', employee, rtype=rtype,
                                flash=self._ess_pop_flash())

    @http.route(['/my/hr/letter/<int:rec_id>'], type='http', auth='user', website=True)
    def ess_letter_download(self, rec_id, **kw):
        employee = self._ess_employee()
        record = self._ess_get_owned('bs.hr.request', rec_id, employee)
        attachment = record.letter_attachment_id.sudo()
        if not attachment:
            raise NotFound()
        return request.env['ir.binary']._get_stream_from(attachment).get_response(as_attachment=True)

    @http.route(['/hr/ess/verify/<string:token>'], type='http', auth='public', website=True, sitemap=False)
    def ess_letter_verify(self, token, **kw):
        record = request.env['bs.hr.request'].sudo()._ess_find_by_token(token)
        return request.render('bs_hr_ess_requests.ess_letter_verify', {
            'record': record,
            'issued': record.decision_date and fields.Datetime.context_timestamp(
                record, record.decision_date).date(),
        })
