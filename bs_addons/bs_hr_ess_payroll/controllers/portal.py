import re

from werkzeug.exceptions import NotFound

from odoo import _, http
from odoo.exceptions import UserError, ValidationError
from odoo.http import content_disposition, request

from odoo.addons.bs_hr_ess_base.controllers.main import EssPortalBase


class EssPayrollPortal(EssPortalBase):

    def _ess_get_own_payslip(self, slip_id, employee):
        """The employee's payslip if it is visible in the portal (confirmed or published draft)."""
        slip = request.env['resta.payslip'].sudo().browse(int(slip_id)).exists()
        if not slip or slip.employee_id != employee or not slip._ess_portal_visible():
            raise NotFound()
        return slip

    def _ess_payslip_values(self, slip):
        compat = request.env['bs.hr.payroll.compat']
        lines = compat._slip_lines(slip)
        adjustments = request.env['bs.hr.payroll.adjustment'].sudo().search([('payslip_id', '=', slip.id)])
        objections = request.env['bs.hr.payslip.objection'].sudo().search([('payslip_id', '=', slip.id)])
        return {
            'slip': slip, 'lines': lines, 'adjustments': adjustments, 'objections': objections,
            'net': compat._net_amount(slip), 'currency': slip.company_id.currency_id,
            'reference': compat._slip_reference(slip), 'is_draft': slip._ess_is_draft_review(),
            'has_report': bool(compat._payslip_report()),
        }

    @http.route(['/my/hr/payslips'], type='http', auth='user', website=True)
    def ess_payslips(self, year=None, **kw):
        employee = self._ess_employee()
        if not employee:
            return self._ess_no_employee()
        Payslip = request.env['resta.payslip'].sudo()
        compat = request.env['bs.hr.payroll.compat']
        slips = Payslip.search(Payslip._ess_portal_domain(employee), order='date_to desc, id desc', limit=200)
        years = sorted({s.date_to.year for s in slips if s.date_to}, reverse=True)
        year = int(year) if year and str(year).isdigit() else (years[0] if years else None)
        shown = slips.filtered(lambda s: not year or (s.date_to and s.date_to.year == year))
        confirmed = shown.filtered(lambda s: not s._ess_is_draft_review())
        return self._ess_render('bs_hr_ess_payroll.ess_payslips', employee, slips=shown, years=years,
                                year=year, compat=compat,
                                year_total=sum(compat._net_amount(s) for s in confirmed),
                                currency=employee.company_id.currency_id,
                                flash=self._ess_pop_flash(), page_name='ess_payslips')

    @http.route(['/my/hr/payslip/<int:slip_id>'], type='http', auth='user', website=True)
    def ess_payslip_detail(self, slip_id, **kw):
        employee = self._ess_employee()
        if not employee:
            return self._ess_no_employee()
        slip = self._ess_get_own_payslip(slip_id, employee)
        return self._ess_render('bs_hr_ess_payroll.ess_payslip_detail', employee,
                                flash=self._ess_pop_flash(), **self._ess_payslip_values(slip))

    @http.route(['/my/hr/payslip/<int:slip_id>/pdf'], type='http', auth='user')
    def ess_payslip_pdf(self, slip_id, download=None, **kw):
        employee = self._ess_employee()
        if not employee:
            raise NotFound()
        slip = self._ess_get_own_payslip(slip_id, employee)
        report = request.env['bs.hr.payroll.compat']._payslip_report()
        if not report:
            raise NotFound()
        pdf, _type = request.env['ir.actions.report'].sudo().with_company(slip.company_id)._render_qweb_pdf(
            report, [slip.id])
        filename = '%s.pdf' % re.sub(r'\W+', '_', request.env['bs.hr.payroll.compat']._slip_reference(slip)
                                     or 'payslip')
        return request.make_response(pdf, headers=[
            ('Content-Type', 'application/pdf'), ('Content-Length', len(pdf)),
            ('Content-Disposition', content_disposition(filename, 'attachment' if download else 'inline')),
        ])

    @http.route(['/my/hr/payslip/<int:slip_id>/objection'], type='http', auth='user', website=True,
                methods=['GET', 'POST'])
    def ess_payslip_objection(self, slip_id, **post):
        employee = self._ess_employee()
        if not employee:
            return self._ess_no_employee()
        slip = self._ess_get_own_payslip(slip_id, employee)
        url = '/my/hr/payslip/%s/objection' % slip.id
        compat = request.env['bs.hr.payroll.compat']
        lines = compat._slip_lines(slip)
        if request.httprequest.method == 'POST':
            line_id = post.get('line_id')
            line = lines.filtered(lambda l: str(l.id) == str(line_id)) if line_id else lines.browse()
            if line_id and not line:
                raise NotFound()
            try:
                claimed = float(post.get('claimed_amount') or 0.0)
            except ValueError:
                return self._ess_error_redirect(url, _('Please enter a valid amount.'))
            try:
                rec = request.env['bs.hr.payslip.objection'].sudo().create({
                    'employee_id': employee.id, 'payslip_id': slip.id, 'line_id': line.id or False,
                    'claimed_amount': claimed, 'reason': (post.get('reason') or '').strip(),
                })
                self._ess_attach(rec, self._ess_files(request.httprequest.files.getlist('attachments')))
                self._ess_submit(rec)
            except (UserError, ValidationError) as e:
                request.env.cr.rollback()
                return self._ess_error_redirect(url, e.args[0])
            return self._ess_ok_redirect(rec._ess_portal_url(), _('Your objection was sent.'))
        return self._ess_render('bs_hr_ess_payroll.ess_payslip_objection', employee, slip=slip, lines=lines,
                                reference=compat._slip_reference(slip), selected=post.get('line'),
                                flash=self._ess_pop_flash())
