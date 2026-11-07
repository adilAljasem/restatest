import base64
import hashlib
import json
import logging
import math
from datetime import timedelta

import pytz

from cryptography.fernet import Fernet, InvalidToken

from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.tools.image import base64_to_image

from .hr_work_location import haversine

_logger = logging.getLogger(__name__)

DESCRIPTOR_SIZE = 128
RATE_LIMIT_PER_MINUTE = 8


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    ess_att_override = fields.Boolean(string='Specific check-in rules',
                                      groups='hr.group_hr_user',
                                      help='Override the check-in methods of the branch for this employee.')
    ess_use_geo = fields.Boolean(string='Location (geofence)', default=True, groups='hr.group_hr_user')
    ess_use_qr = fields.Boolean(string='Rotating QR code', groups='hr.group_hr_user')
    ess_use_face = fields.Boolean(string='Face recognition', groups='hr.group_hr_user')
    ess_method_logic = fields.Selection([
        ('all', 'All selected methods are required'),
        ('any', 'Any selected method is enough'),
    ], string='Combination', default='all', groups='hr.group_hr_user')
    ess_allowed_location_ids = fields.Many2many(
        'hr.work.location', 'bs_hr_employee_ess_location_rel', 'employee_id', 'location_id',
        string='Allowed branches', groups='hr.group_hr_user',
        help='Branches where the employee may check in. Empty = his work location only.')
    ess_device_ids = fields.One2many('bs.hr.ess.device', 'employee_id', string='Devices', groups='hr.group_hr_user')
    ess_face_descriptor = fields.Text(groups='base.group_system', copy=False)
    ess_face_enrolled = fields.Boolean(string='Face enrolled', readonly=True, copy=False, groups='hr.group_hr_user')
    ess_face_enroll_allowed = fields.Boolean(
        string='Allow face enrollment', copy=False, groups='hr.group_hr_user',
        help='Unlock (re)enrollment from the portal, under the supervision of HR or the branch supervisor. '
             'Locked again automatically once done.')
    ess_face_consent_date = fields.Datetime(string='Biometric consent on', readonly=True, copy=False,
                                            groups='hr.group_hr_user')
    ess_face_consent_ip = fields.Char(string='Consent IP', readonly=True, copy=False, groups='hr.group_hr_user')

    # ------------------------------------------------------------------
    # configuration
    # ------------------------------------------------------------------
    def _ess_allowed_locations(self):
        self.ensure_one()
        emp = self.sudo()
        return emp.ess_allowed_location_ids or emp.work_location_id

    def _ess_att_config(self, location=None):
        """Return {'geo': bool, 'qr': bool, 'face': bool, 'logic': 'all'|'any'}."""
        self.ensure_one()
        emp = self.sudo()
        if emp.ess_att_override:
            src = emp
        else:
            src = location or emp.work_location_id
        if not src:
            return {'geo': False, 'qr': False, 'face': False, 'logic': 'all'}
        return {'geo': src.ess_use_geo, 'qr': src.ess_use_qr, 'face': src.ess_use_face,
                'logic': src.ess_method_logic or 'all'}

    # ------------------------------------------------------------------
    # face descriptors (encrypted at rest)
    # ------------------------------------------------------------------
    def _ess_fernet(self):
        secret = self.env['ir.config_parameter'].sudo().get_param('database.secret', '')
        key = base64.urlsafe_b64encode(hashlib.sha256(('bs_ess_face:' + secret).encode()).digest())
        return Fernet(key)

    @staticmethod
    def _ess_clean_descriptor(descriptor):
        if not isinstance(descriptor, (list, tuple)) or len(descriptor) != DESCRIPTOR_SIZE:
            return None
        try:
            values = [float(v) for v in descriptor]
        except (TypeError, ValueError):
            return None
        if not all(math.isfinite(v) and -2.0 < v < 2.0 for v in values):
            return None
        return values

    def _ess_face_enroll(self, descriptors, ip=None):
        self.ensure_one()
        clean = [d for d in (self._ess_clean_descriptor(x) for x in descriptors or []) if d]
        if len(clean) < 3:
            raise UserError(self.env._('At least 3 clear captures of the face are required.'))
        mean = [sum(col) / len(clean) for col in zip(*clean)]
        # reject inconsistent captures (several people / bad frames)
        spread = max(self._ess_distance(d, mean) for d in clean)
        if spread > 0.45:
            raise UserError(self.env._('The captures are too different from each other. Retry in good light, '
                                       'facing the camera.'))
        token = self._ess_fernet().encrypt(json.dumps(mean).encode()).decode()
        self.sudo().write({
            'ess_face_descriptor': token, 'ess_face_enrolled': True, 'ess_face_enroll_allowed': False,
            'ess_face_consent_date': fields.Datetime.now(), 'ess_face_consent_ip': ip,
        })
        self.sudo().message_post(body=self.env._('Face enrolled for self-service attendance (biometric consent given).'))
        return True

    def action_ess_face_reset(self):
        self.sudo().write({'ess_face_descriptor': False, 'ess_face_enrolled': False,
                           'ess_face_enroll_allowed': True})

    def _ess_face_reference(self):
        self.ensure_one()
        token = self.sudo().ess_face_descriptor
        if not token:
            return None
        try:
            return json.loads(self._ess_fernet().decrypt(token.encode()))
        except (InvalidToken, ValueError):
            _logger.warning('ESS: cannot decrypt the face descriptor of employee %s', self.id)
            return None

    @staticmethod
    def _ess_distance(a, b):
        return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))

    # ------------------------------------------------------------------
    # punch
    # ------------------------------------------------------------------
    def _ess_notify_hr_new_device(self, device):
        self.ensure_one()
        users = self.env.ref('bs_hr_ess_base.group_ess_hr_officer').sudo().all_user_ids.filtered(
            lambda u: self.company_id in u.company_ids)
        for user in users[:5]:
            device.sudo().activity_schedule(
                'mail.mail_activity_data_todo', user_id=user.id,
                summary=self.env._('New phone for %s: approve or revoke', self.name))
        self.sudo().message_post(body=self.env._('New attendance device waiting for approval: %s', device.name))

    def _ess_rate_limited(self):
        since = fields.Datetime.now() - timedelta(minutes=1)
        return self.env['bs.hr.attendance.attempt'].sudo().search_count([
            ('employee_id', '=', self.id), ('create_date', '>=', since)]) >= RATE_LIMIT_PER_MINUTE

    def _ess_punch(self, payload, ip=None, user_agent=None):
        """Self-service check-in / check-out. All checks are done here, server side.

        ``payload`` keys: latitude, longitude, accuracy, qr_code, face_descriptor (128 floats),
        liveness (bool), photo (base64 jpeg), device_token, method (requested single method for 'any').
        Returns a dict {'ok': bool, 'message': str, 'action': 'check_in'|'check_out', 'time': str}.
        """
        self.ensure_one()
        employee = self.sudo()
        company = employee.company_id
        Attempt = self.env['bs.hr.attendance.attempt'].sudo()
        action = 'check_out' if employee.attendance_state == 'checked_in' else 'check_in'
        log = {'employee_id': employee.id, 'action': action, 'ip_address': ip,
               'user_agent': (user_agent or '')[:250]}

        def fail(message, **extra):
            Attempt.create({**log, **extra, 'success': False, 'reason': message[:250]})
            return {'ok': False, 'message': message, 'action': action}

        if employee._ess_rate_limited():
            return {'ok': False, 'action': action,
                    'message': self.env._('Too many attempts. Wait one minute and try again.')}

        # ---- device binding
        device = self.env['bs.hr.ess.device']
        if company.ess_device_binding:
            device, ok, message = self.env['bs.hr.ess.device'].sudo()._ess_resolve(
                employee, payload.get('device_token'), user_agent, ip)
            log['device_id'] = device.id
            if not ok:
                return fail(message)

        # ---- QR code (also determines the branch)
        location = self.env['hr.work.location']
        qr_step = None
        qr_code = payload.get('qr_code')
        if qr_code:
            location, qr_step = self.env['hr.work.location']._ess_qr_parse(qr_code)
            if not location:
                return fail(self.env._('This QR code is invalid or expired. Scan the code currently shown '
                                       'on the branch screen.'))
        allowed = employee._ess_allowed_locations()

        # ---- geolocation
        lat, lng, accuracy = payload.get('latitude'), payload.get('longitude'), payload.get('accuracy')
        has_geo = lat is not None and lng is not None
        if has_geo:
            try:
                lat, lng = float(lat), float(lng)
                accuracy = float(accuracy or 0)
            except (TypeError, ValueError):
                has_geo = False
        if has_geo:
            log.update(latitude=lat, longitude=lng, accuracy=accuracy)
        if not location:
            # choose the nearest allowed branch
            if has_geo and allowed:
                ranked = sorted(allowed, key=lambda loc: loc._ess_geo_check(lat, lng)[1] or 1e12)
                location = ranked[0]
            else:
                location = allowed[:1]
        if not location:
            return fail(self.env._('No branch is assigned to you. Contact HR.'))
        if location not in allowed:
            return fail(self.env._('You are not allowed to check in at %s.', location.name))
        log['location_id'] = location.id

        config = employee._ess_att_config(location)
        enabled = [m for m in ('geo', 'qr', 'face') if config[m]]
        if not enabled:
            return fail(self.env._('Self-service check-in is not enabled for your branch.'))
        if config['logic'] == 'any':
            requested = payload.get('method')
            required = [requested] if requested in enabled else enabled[:1]
        else:
            required = enabled

        # ---- blocking rules from other modules (expired health certificate, ...)
        blockers = employee._ess_attendance_precheck(location)
        if blockers and action == 'check_in':
            return fail(' '.join(blockers))

        suspicious = []
        vals = {}
        # geofence
        if 'geo' in required:
            if not has_geo:
                return fail(self.env._('Your location is required. Allow location access and retry.'))
            max_acc = location.ess_max_accuracy or 150
            if accuracy and accuracy > max_acc:
                return fail(self.env._('GPS accuracy is too low (%(acc)s m). Move outside or enable precise '
                                       'location, then retry.', acc=int(accuracy)))
            if not location._ess_geo_points():
                return fail(self.env._('The branch %s has no coordinates yet. Contact HR.', location.name))
            inside, distance = location._ess_geo_check(lat, lng)
            log['distance'] = distance or 0
            if not inside:
                return fail(self.env._('You are %(dist)s m away from %(branch)s. Get closer to check in.',
                                       dist=int(distance or 0), branch=location.name), distance=distance or 0)
            vals['ess_distance'] = distance or 0
            if accuracy is not None and accuracy < 1:
                suspicious.append(self.env._('GPS accuracy reported as %s m (mock location?)', accuracy))
            speed_reason = employee._ess_check_travel(lat, lng)
            if speed_reason:
                suspicious.append(speed_reason)
        if has_geo:
            vals.update(latitude=lat, longitude=lng, ess_accuracy=accuracy or 0)
        # QR
        if 'qr' in required:
            if qr_step is None:
                return fail(self.env._('Scan the QR code shown on the branch screen.'))
            QrUse = self.env['bs.hr.attendance.qr.use'].sudo()
            use_vals = {'employee_id': employee.id, 'location_id': location.id, 'step': qr_step}
            if QrUse.search_count([(k, '=', v) for k, v in use_vals.items()]):
                return fail(self.env._('This QR code was already used. Wait for the next code.'))
            QrUse.create(use_vals)
        # face
        if 'face' in required:
            reference = employee._ess_face_reference()
            if not reference:
                return fail(self.env._('Your face is not enrolled yet. Ask HR or your supervisor to enroll it.'))
            live = self._ess_clean_descriptor(payload.get('face_descriptor'))
            if not live:
                return fail(self.env._('No face detected. Look at the camera and retry.'))
            if not payload.get('liveness'):
                if payload.get('photo_mode') and company.ess_face_photo_fallback:
                    suspicious.append(self.env._('Face checked on a selfie (no liveness: live camera unavailable)'))
                else:
                    return fail(self.env._('Liveness check failed. Follow the instruction on screen.'))
            face_distance = self._ess_distance(live, reference)
            log['face_distance'] = face_distance
            threshold = company.ess_face_threshold or 0.5
            if face_distance > threshold:
                return fail(self.env._('Face not recognized. Retry in good light.'), face_distance=face_distance)
            vals.update(ess_face_distance=face_distance, ess_liveness=bool(payload.get('liveness')))
            if company.ess_face_keep_photo and payload.get('photo'):
                vals['ess_photo'] = employee._ess_clean_photo(payload['photo'])

        # ---- write the attendance
        method_label = '+'.join(required)
        attendance = employee._ess_write_attendance(action, location, method_label, vals, device, ip,
                                                    user_agent, suspicious)
        Attempt.create({**log, 'success': True, 'methods': method_label, 'attendance_id': attendance.id,
                        'reason': '; '.join(suspicious)[:250] if suspicious else False})
        local = fields.Datetime.context_timestamp(employee.with_context(tz=employee.tz),
                                                  attendance.check_out if action == 'check_out' else attendance.check_in)
        if action == 'check_in':
            message = self.env._('Checked in at %(time)s - %(branch)s', time=local.strftime('%H:%M'),
                                 branch=location.name)
        else:
            message = self.env._('Checked out at %(time)s. Worked %(hours)s h today.', time=local.strftime('%H:%M'),
                                 hours='%.2f' % attendance.worked_hours)
        return {'ok': True, 'message': message, 'action': action, 'time': local.strftime('%H:%M'),
                'suspicious': bool(suspicious)}

    @staticmethod
    def _ess_clean_photo(photo):
        if not isinstance(photo, str):
            return False
        if ',' in photo:
            photo = photo.split(',', 1)[1]
        if len(photo) > 400000:  # ~300 KB max
            return False
        try:
            base64_to_image(photo)
        except (ValueError, TypeError, UserError):
            return False
        return photo

    def _ess_check_travel(self, lat, lng):
        self.ensure_one()
        company = self.company_id
        since = fields.Datetime.now() - timedelta(hours=6)
        last = self.env['hr.attendance'].sudo().search([
            ('employee_id', '=', self.id), ('check_in', '>=', since), ('in_latitude', '!=', 0)],
            order='check_in desc', limit=1)
        if not last:
            return None
        ref_time = last.check_out or last.check_in
        ref_lat, ref_lng = (last.out_latitude, last.out_longitude) if last.check_out and last.out_latitude \
            else (last.in_latitude, last.in_longitude)
        hours = max((fields.Datetime.now() - ref_time).total_seconds() / 3600.0, 1 / 60.0)
        km = haversine(lat, lng, ref_lat, ref_lng) / 1000.0
        speed = km / hours
        if km > 1 and speed > (company.ess_max_speed_kmh or 200):
            return self.env._('Impossible travel: %(km)s km in %(min)s min', km=round(km, 1), min=int(hours * 60))
        return None

    def _ess_write_attendance(self, action, location, method, vals, device, ip, user_agent, suspicious):
        self.ensure_one()
        Attendance = self.env['hr.attendance'].sudo()
        now = fields.Datetime.now()
        browser = (user_agent or '')[:60]
        prefix = 'in' if action == 'check_in' else 'out'
        geo = {}
        if 'latitude' in vals:
            geo = {'%s_latitude' % prefix: vals.pop('latitude'), '%s_longitude' % prefix: vals.pop('longitude')}
        common = {'%s_mode' % prefix: 'ess_portal', '%s_ip_address' % prefix: ip,
                  '%s_browser' % prefix: browser, **geo}
        if action == 'check_in':
            attendance = Attendance.create({
                'employee_id': self.id, 'check_in': now, 'ess_location_id': location.id,
                'ess_method': method, 'ess_device_id': device.id or False,
                'ess_suspicious': bool(suspicious),
                'ess_suspicious_reason': '; '.join(suspicious) if suspicious else False,
                **common, **vals,
            })
        else:
            attendance = Attendance.search([('employee_id', '=', self.id), ('check_out', '=', False)],
                                           order='check_in desc', limit=1)
            if not attendance:
                raise UserError(self.env._('No open attendance to check out.'))
            write_vals = {'check_out': now, 'ess_out_method': method, **common}
            if suspicious:
                write_vals.update(ess_suspicious=True, ess_suspicious_reason='; '.join(
                    filter(None, [attendance.ess_suspicious_reason] + suspicious)))
            attendance.write(write_vals)
        return attendance

    # ------------------------------------------------------------------
    # portal extension points
    # ------------------------------------------------------------------
    def _ess_today_attendances(self):
        self.ensure_one()
        tz_now = fields.Datetime.context_timestamp(self.with_context(tz=self.tz), fields.Datetime.now())
        start_local = tz_now.replace(hour=0, minute=0, second=0, microsecond=0)
        start_utc = start_local.astimezone(pytz.utc).replace(tzinfo=None)
        return self.env['hr.attendance'].sudo().search(
            [('employee_id', '=', self.id), ('check_in', '>=', start_utc - timedelta(hours=6))],
            order='check_in')

    def _ess_dashboard_cards(self):
        cards = super()._ess_dashboard_cards()
        emp = self.sudo()
        today = emp._ess_today_attendances()
        if emp.attendance_state == 'checked_in' and today:
            last = today.filtered(lambda a: not a.check_out)[-1:] or today[-1:]
            local = fields.Datetime.context_timestamp(emp.with_context(tz=emp.tz), last.check_in)
            value, sub, tone = local.strftime('%H:%M'), self.env._('Checked in'), 'success'
        elif today:
            value = '%.1f h' % sum(today.mapped('worked_hours'))
            sub, tone = self.env._('Worked today'), 'info'
        else:
            value, sub, tone = '--:--', self.env._('Not checked in'), 'warning'
        cards.append({'key': 'attendance', 'title': self.env._('Attendance'), 'value': value,
                      'subtitle': sub, 'url': '/my/hr/attendance', 'icon': 'fa-clock-o',
                      'tone': tone, 'sequence': 1})
        return cards

    def _ess_menu_items(self):
        items = super()._ess_menu_items()
        items.append({'key': 'attendance', 'title': self.env._('Attendance'), 'url': '/my/hr/attendance',
                      'icon': 'fa-clock-o', 'sequence': 5})
        items.append({'key': 'att_correction', 'title': self.env._('Attendance correction'),
                      'url': '/my/hr/attendance/correction', 'icon': 'fa-history', 'sequence': 15,
                      'new_request': True})
        return items
