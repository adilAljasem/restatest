from werkzeug.exceptions import NotFound

from odoo import _, http
from odoo.exceptions import UserError, ValidationError
from odoo.http import request

from odoo.addons.bs_hr_ess_base.controllers.main import EssPortalBase

from ..models.grievance import PORTAL_TYPES


class EssGrievancePortal(EssPortalBase):

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _ess_get_own_grievance(self, grievance_id, employee):
        """Case of the logged employee (anonymous included), 404 otherwise."""
        if not str(grievance_id).isdigit():
            raise NotFound()
        grievance = request.env['bs.hr.grievance'].sudo().browse(int(grievance_id)).exists()
        if not grievance or not grievance._is_owner(employee):
            raise NotFound()
        return grievance

    def _ess_grievance_types(self):
        selection = request.env['bs.hr.grievance']._fields['grievance_type']._description_selection(request.env)
        return [(key, label) for key, label in selection if key in PORTAL_TYPES]

    def _ess_colleagues(self, employee):
        return request.env['hr.employee'].sudo().search_fetch(
            [('company_id', '=', employee.company_id.id), ('id', '!=', employee.id)], ['name'], order='name')

    # ------------------------------------------------------------------
    # grievances
    # ------------------------------------------------------------------
    @http.route(['/my/hr/grievances'], type='http', auth='user', website=True)
    def ess_grievances(self, **kw):
        employee = self._ess_employee()
        if not employee:
            return self._ess_no_employee()
        grievances = request.env['bs.hr.grievance']._ess_search_owned(employee)
        return self._ess_render('bs_hr_ess_grievance.ess_grievances', employee, grievances=grievances,
                                flash=self._ess_pop_flash(), page_name='ess_grievances')

    @http.route(['/my/hr/grievance/new'], type='http', auth='user', website=True, methods=['GET', 'POST'])
    def ess_grievance_new(self, **post):
        employee = self._ess_employee()
        if not employee:
            return self._ess_no_employee()
        types = self._ess_grievance_types()
        if request.httprequest.method == 'POST':
            gtype = post.get('grievance_type')
            subject = (post.get('subject') or '').strip()
            description = (post.get('description') or '').strip()
            if gtype not in dict(types) or not subject or not description:
                return self._ess_error_redirect('/my/hr/grievance/new', _('Please fill the type, the subject and the description.'))
            colleagues = self._ess_colleagues(employee)
            against_ids = [int(x) for x in request.httprequest.form.getlist('against_employee_ids') if x.isdigit()]
            against = colleagues.filtered(lambda e: e.id in against_ids)
            try:
                grievance = request.env['bs.hr.grievance']._ess_create_for_employee(employee, {
                    'grievance_type': gtype, 'subject': subject, 'description': description,
                    'anonymous': bool(post.get('anonymous')),
                    'against_employee_ids': [(6, 0, against.ids)],
                })
            except (UserError, ValidationError) as e:
                request.env.cr.rollback()
                return self._ess_error_redirect('/my/hr/grievance/new', e.args[0])
            # the tracking code is displayed only on this response
            return self._ess_render('bs_hr_ess_grievance.ess_grievance_submitted', employee, grievance=grievance,
                                    tracking_code=grievance.sudo().tracking_code)
        return self._ess_render('bs_hr_ess_grievance.ess_grievance_new', employee, types=types,
                                colleagues=self._ess_colleagues(employee), flash=self._ess_pop_flash())

    @http.route(['/my/hr/grievance/track'], type='http', auth='user', website=True, methods=['GET', 'POST'])
    def ess_grievance_track(self, code=None, **kw):
        employee = self._ess_employee()
        if not employee:
            return self._ess_no_employee()
        code = (code or '').strip().upper()
        if code:
            grievance = request.env['bs.hr.grievance'].sudo().search([('tracking_code', '=', code)], limit=1)
            if grievance and grievance._is_owner(employee):
                return request.redirect('/my/hr/grievance/%s' % grievance.id)
            return self._ess_error_redirect('/my/hr/grievances', _('No case of yours matches this tracking code.'))
        return request.redirect('/my/hr/grievances')

    @http.route(['/my/hr/grievance/<int:grievance_id>'], type='http', auth='user', website=True)
    def ess_grievance_detail(self, grievance_id, **kw):
        employee = self._ess_employee()
        if not employee:
            return self._ess_no_employee()
        grievance = self._ess_get_own_grievance(grievance_id, employee)
        return self._ess_render('bs_hr_ess_grievance.ess_grievance_detail', employee, grievance=grievance,
                                messages=grievance.message_ids, can_appeal=grievance._can_appeal(),
                                flash=self._ess_pop_flash())

    @http.route(['/my/hr/grievance/<int:grievance_id>/reply'], type='http', auth='user', website=True,
                methods=['POST'])
    def ess_grievance_reply(self, grievance_id, body=None, **kw):
        employee = self._ess_employee()
        grievance = self._ess_get_own_grievance(grievance_id, employee)
        url = '/my/hr/grievance/%s' % grievance.id
        if grievance.state == 'closed':
            return self._ess_error_redirect(url, _('This case is closed.'))
        if body and body.strip():
            grievance._ess_post_employee_message(body)
        return self._ess_ok_redirect(url, _('Your message was sent to the committee.'))

    @http.route(['/my/hr/grievance/<int:grievance_id>/appeal'], type='http', auth='user', website=True,
                methods=['POST'])
    def ess_grievance_appeal(self, grievance_id, reason=None, **kw):
        employee = self._ess_employee()
        grievance = self._ess_get_own_grievance(grievance_id, employee)
        url = '/my/hr/grievance/%s' % grievance.id
        try:
            grievance._ess_appeal(reason)
        except UserError as e:
            request.env.cr.rollback()
            return self._ess_error_redirect(url, e.args[0])
        return self._ess_ok_redirect(url, _('Your appeal was sent to the committee (level 2).'))

    # ------------------------------------------------------------------
    # disciplinary actions
    # ------------------------------------------------------------------
    @http.route(['/my/hr/disciplinary'], type='http', auth='user', website=True)
    def ess_disciplinary(self, **kw):
        employee = self._ess_employee()
        if not employee:
            return self._ess_no_employee()
        actions = request.env['bs.hr.disciplinary'].sudo().search(
            [('employee_id', '=', employee.id), ('state', '!=', 'draft')])
        return self._ess_render('bs_hr_ess_grievance.ess_disciplinary', employee, actions=actions,
                                flash=self._ess_pop_flash(), page_name='ess_disciplinary')

    def _ess_get_own_disciplinary(self, rec_id, employee):
        record = self._ess_get_own_record('bs.hr.disciplinary', rec_id, employee)
        if record.state == 'draft':
            raise NotFound()
        return record

    @http.route(['/my/hr/disciplinary/<int:rec_id>/ack'], type='http', auth='user', website=True, methods=['POST'])
    def ess_disciplinary_ack(self, rec_id, **kw):
        employee = self._ess_employee()
        record = self._ess_get_own_disciplinary(rec_id, employee)
        record._ess_acknowledge()
        return self._ess_ok_redirect('/my/hr/disciplinary', _('Acknowledgement recorded.'))

    @http.route(['/my/hr/disciplinary/<int:rec_id>/appeal'], type='http', auth='user', website=True,
                methods=['POST'])
    def ess_disciplinary_appeal(self, rec_id, reason=None, **kw):
        employee = self._ess_employee()
        record = self._ess_get_own_disciplinary(rec_id, employee)
        try:
            grievance = record._ess_appeal(reason)
        except UserError as e:
            request.env.cr.rollback()
            return self._ess_error_redirect('/my/hr/disciplinary', e.args[0])
        return self._ess_ok_redirect('/my/hr/grievance/%s' % grievance.id,
                                     _('Your appeal was sent to the grievance committee.'))
