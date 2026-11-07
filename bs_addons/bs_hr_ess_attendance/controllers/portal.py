import json
from datetime import datetime, timedelta

import pytz

from odoo import _, fields, http
from odoo.exceptions import UserError, ValidationError
from odoo.http import request
from odoo.tools import format_date

from odoo.addons.bs_hr_ess_base.controllers.main import EssPortal, EssPortalBase


class EssAttendancePortal(EssPortalBase):

    def _ess_att_page_config(self, employee, qr_code=None):
        location = employee._ess_allowed_locations()[:1]
        if qr_code:
            loc, _step = request.env['hr.work.location']._ess_qr_parse(qr_code)
            location = loc or location
        config = employee._ess_att_config(location)
        return {
            'geo': bool(config['geo']), 'qr': bool(config['qr']), 'face': bool(config['face']),
            'logic': config['logic'], 'qr_code': qr_code or '',
            'face_enrolled': bool(employee.ess_face_enrolled),
            'face_photo_fallback': bool(employee.company_id.ess_face_photo_fallback),
            'checked_in': employee.attendance_state == 'checked_in',
            'models_url': '/bs_hr_ess_attendance/static/lib/face-api/models',
            'branch': location.name or '',
        }

    @http.route(['/my/hr/attendance', '/my/hr/attendance/qr'], type='http', auth='user', website=True)
    def ess_attendance(self, c=None, **kw):
        employee = self._ess_employee()
        if not employee:
            return self._ess_no_employee()
        config = self._ess_att_page_config(employee, qr_code=c)
        since = fields.Datetime.now() - timedelta(days=14)
        history = request.env['hr.attendance'].sudo().search(
            [('employee_id', '=', employee.id), ('check_in', '>=', since)], order='check_in desc', limit=40)
        tz = pytz.timezone(employee.tz or 'UTC')

        def local(dt):
            return pytz.utc.localize(dt).astimezone(tz) if dt else None

        return self._ess_render('bs_hr_ess_attendance.ess_attendance', employee,
                                config=config, config_json=json.dumps(config), history=history,
                                local=local, today=employee._ess_today_attendances(),
                                ess_day=lambda dt: format_date(request.env, dt.date(), date_format='EEEE d/M'),
                                flash=self._ess_pop_flash(), page_name='ess_attendance')

    @http.route('/my/hr/attendance/punch', type='jsonrpc', auth='user', methods=['POST'])
    def ess_attendance_punch(self, **payload):
        employee = self._ess_employee()
        if not employee:
            return {'ok': False, 'message': _('Your account is not linked to an employee.')}
        ip = request.httprequest.remote_addr
        ua = request.httprequest.headers.get('User-Agent', '')
        try:
            return employee._ess_punch(payload, ip=ip, user_agent=ua)
        except (UserError, ValidationError) as e:
            return {'ok': False, 'message': e.args[0]}

    # ------------------------------------------------------------------
    # face enrollment
    # ------------------------------------------------------------------
    @http.route('/my/hr/attendance/face', type='http', auth='user', website=True)
    def ess_face(self, **kw):
        employee = self._ess_employee()
        if not employee:
            return self._ess_no_employee()
        return self._ess_render('bs_hr_ess_attendance.ess_face_enroll', employee,
                                allowed=employee.ess_face_enroll_allowed,
                                models_url='/bs_hr_ess_attendance/static/lib/face-api/models')

    @http.route('/my/hr/attendance/face/enroll', type='jsonrpc', auth='user', methods=['POST'])
    def ess_face_enroll(self, descriptors=None, consent=False, **kw):
        employee = self._ess_employee()
        if not employee or not employee.ess_face_enroll_allowed:
            return {'ok': False, 'message': _('Face enrollment is locked. Ask HR or your supervisor to unlock it.')}
        if not consent:
            return {'ok': False, 'message': _('Your consent is required.')}
        try:
            employee._ess_face_enroll(descriptors, ip=request.httprequest.remote_addr)
        except UserError as e:
            return {'ok': False, 'message': e.args[0]}
        return {'ok': True, 'message': _('Your face is enrolled. You can now check in.')}

    # ------------------------------------------------------------------
    # corrections
    # ------------------------------------------------------------------
    @http.route('/my/hr/attendance/correction', type='http', auth='user', website=True,
                methods=['GET', 'POST'])
    def ess_attendance_correction(self, **post):
        employee = self._ess_employee()
        if not employee:
            return self._ess_no_employee()
        if request.httprequest.method == 'POST':
            tz = pytz.timezone(employee.tz or 'UTC')

            def to_utc(value):
                if not value:
                    return False
                local = datetime.strptime(value[:16], '%Y-%m-%dT%H:%M')
                return tz.localize(local).astimezone(pytz.utc).replace(tzinfo=None)

            try:
                rec = request.env['bs.hr.attendance.correction'].sudo().create({
                    'employee_id': employee.id,
                    'correction_type': post.get('correction_type') or 'missing_out',
                    'check_in': to_utc(post.get('check_in')),
                    'check_out': to_utc(post.get('check_out')),
                    'reason': (post.get('reason') or '').strip() or _('(no reason)'),
                })
                self._ess_attach(rec, self._ess_files(request.httprequest.files.getlist('attachments')))
                self._ess_submit(rec)
            except (UserError, ValidationError, ValueError) as e:
                request.env.cr.rollback()
                msg = e.args[0] if e.args else _('Invalid data.')
                return self._ess_error_redirect('/my/hr/attendance/correction', msg)
            return self._ess_ok_redirect(rec._ess_portal_url(), _('Correction request sent.'))
        types = request.env['bs.hr.attendance.correction']._fields['correction_type']._description_selection(
            request.env)
        return self._ess_render('bs_hr_ess_attendance.ess_correction', employee, types=types,
                                flash=self._ess_pop_flash())


class EssPortalAttendanceTeam(EssPortal):

    def _ess_team_extra(self, employee, team):
        extra = super()._ess_team_extra(employee, team)
        if not team:
            return extra
        open_att = request.env['hr.attendance'].sudo().search([
            ('employee_id', 'in', team.ids), ('check_out', '=', False),
            ('check_in', '>=', fields.Datetime.now() - timedelta(hours=16))])
        by_emp = {a.employee_id.id: a for a in open_att}
        for member in team:
            att = by_emp.get(member.id)
            if att:
                local = fields.Datetime.context_timestamp(member.with_context(tz=member.tz), att.check_in)
                extra.setdefault(member.id, []).append(
                    (_('Attendance'), _('In since %s', local.strftime('%H:%M')), 'success'))
            else:
                extra.setdefault(member.id, []).append((_('Attendance'), _('Not checked in'), 'warning'))
        return extra
