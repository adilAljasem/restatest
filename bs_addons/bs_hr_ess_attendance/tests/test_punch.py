import pytz
import random
from datetime import datetime, timedelta
from unittest.mock import patch

from odoo import fields
from odoo.tests import tagged

from odoo.addons.bs_hr_ess_base.tests.common import EssCommon

BRANCH_LAT, BRANCH_LNG = 24.6950, 46.6850
NEAR = {'latitude': 24.6953, 'longitude': 46.6852, 'accuracy': 15}       # ~40 m
FAR = {'latitude': 24.7100, 'longitude': 46.6850, 'accuracy': 15}        # ~1.6 km


def face(seed, noise=0.0):
    rnd = random.Random(seed)
    base = [rnd.uniform(-0.2, 0.2) for _ in range(128)]
    if noise:
        jitter = random.Random(seed * 7 + 1)
        base = [v + jitter.uniform(-noise, noise) for v in base]
    return base


@tagged('post_install', '-at_install', 'bs_hr_ess')
class TestEssPunch(EssCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.branch.write({'ess_latitude': BRANCH_LAT, 'ess_longitude': BRANCH_LNG, 'ess_radius': 100,
                          'ess_use_geo': True, 'ess_use_qr': False, 'ess_use_face': False,
                          'ess_method_logic': 'all'})
        cls.company.ess_device_binding = True
        cls.emp = cls.emp_employee
        cls.token = 'a' * 32

    def punch(self, **payload):
        payload.setdefault('device_token', self.token)
        return self.emp.with_user(self.user_employee).sudo()._ess_punch(payload, ip='10.0.0.1', user_agent='test')

    def test_geofence_in_and_out(self):
        res = self.punch(**NEAR)
        self.assertTrue(res['ok'], res['message'])
        self.assertEqual(res['action'], 'check_in')
        att = self.env['hr.attendance'].search([('employee_id', '=', self.emp.id)])
        self.assertEqual(att.ess_location_id, self.branch)
        self.assertEqual(att.in_mode, 'ess_portal')
        self.assertLess(att.ess_distance, 100)
        res = self.punch(**NEAR)
        self.assertTrue(res['ok'], res['message'])
        self.assertEqual(res['action'], 'check_out')
        self.assertTrue(att.check_out)

    def test_geofence_refused_far_and_low_accuracy(self):
        res = self.punch(**FAR)
        self.assertFalse(res['ok'])
        self.assertIn('m away', res['message'])
        res = self.punch(latitude=NEAR['latitude'], longitude=NEAR['longitude'], accuracy=900)
        self.assertFalse(res['ok'])
        res = self.punch()
        self.assertFalse(res['ok'])
        self.assertFalse(self.env['hr.attendance'].search([('employee_id', '=', self.emp.id)]))
        attempts = self.env['bs.hr.attendance.attempt'].search([('employee_id', '=', self.emp.id)])
        self.assertEqual(len(attempts), 3)
        self.assertFalse(any(attempts.mapped('success')))

    def test_extra_geofence_point(self):
        self.env['bs.hr.geofence.point'].create({
            'location_id': self.branch.id, 'name': 'Parking', 'latitude': FAR['latitude'],
            'longitude': FAR['longitude'], 'radius': 80})
        self.assertTrue(self.punch(**FAR)['ok'])

    def test_device_binding(self):
        self.assertTrue(self.punch(**NEAR)['ok'])
        # another phone -> pending, refused
        res = self.punch(device_token='b' * 32, **NEAR)
        self.assertFalse(res['ok'])
        devices = self.emp.sudo().ess_device_ids
        self.assertEqual(sorted(devices.mapped('state')), ['active', 'pending'])
        pending = devices.filtered(lambda d: d.state == 'pending')
        pending.action_approve()
        self.assertEqual(devices.filtered(lambda d: d.state == 'active'), pending)
        self.assertTrue(self.punch(device_token='b' * 32, **NEAR)['ok'])
        self.assertFalse(self.punch(**NEAR)['ok'])  # old phone revoked

    def test_qr_code(self):
        self.branch.write({'ess_use_qr': True, 'ess_use_geo': False})
        code = self.branch._ess_qr_code()
        loc, step = self.env['hr.work.location']._ess_qr_parse(code)
        self.assertEqual(loc, self.branch)
        # full URL is accepted too
        loc, _s = self.env['hr.work.location']._ess_qr_parse('https://x/my/hr/attendance/qr?c=%s' % code)
        self.assertEqual(loc, self.branch)
        # tampered signature / other branch secret
        self.assertFalse(self.env['hr.work.location']._ess_qr_parse(code[:-1] + ('0' if code[-1] != '0' else '1'))[0])
        res = self.punch(qr_code=code)
        self.assertTrue(res['ok'], res['message'])
        # replay of the same code by the same employee
        res = self.punch(qr_code=code)
        self.assertFalse(res['ok'])
        self.assertIn('already used', res['message'])

    def test_qr_expired(self):
        self.branch.write({'ess_use_qr': True, 'ess_use_geo': False, 'ess_qr_period': 10})
        old_step = self.branch._ess_qr_step() - 3  # 30 s old
        code = self.branch._ess_qr_code(old_step)
        res = self.punch(qr_code=code)
        self.assertFalse(res['ok'])
        self.assertIn('expired', res['message'])

    def test_qr_and_geo_both_required(self):
        self.branch.write({'ess_use_qr': True, 'ess_use_geo': True, 'ess_method_logic': 'all'})
        code = self.branch._ess_qr_code()
        # photographed code sent to someone far away
        res = self.punch(qr_code=code, **FAR)
        self.assertFalse(res['ok'])
        res = self.punch(qr_code=self.branch._ess_qr_code(), **NEAR)
        self.assertTrue(res['ok'], res['message'])

    def test_any_logic(self):
        self.branch.write({'ess_use_qr': True, 'ess_use_geo': True, 'ess_method_logic': 'any'})
        res = self.punch(method='geo', **NEAR)
        self.assertTrue(res['ok'], res['message'])
        att = self.env['hr.attendance'].search([('employee_id', '=', self.emp.id)])
        self.assertEqual(att.ess_method, 'geo')

    def test_face(self):
        self.branch.write({'ess_use_face': True, 'ess_use_geo': False})
        res = self.punch(face_descriptor=face(1), liveness=True)
        self.assertFalse(res['ok'])  # not enrolled
        self.emp.sudo().ess_face_enroll_allowed = True
        self.emp._ess_face_enroll([face(1, 0.01), face(1, 0.012), face(1, 0.008)], ip='1.1.1.1')
        self.assertTrue(self.emp.sudo().ess_face_enrolled)
        self.assertFalse(self.emp.sudo().ess_face_enroll_allowed)
        # stored encrypted (not a JSON list)
        self.assertFalse(self.emp.sudo().ess_face_descriptor.startswith('['))
        # someone else
        res = self.punch(face_descriptor=face(99), liveness=True)
        self.assertFalse(res['ok'])
        # no liveness
        res = self.punch(face_descriptor=face(1, 0.01), liveness=False)
        self.assertFalse(res['ok'])
        # selfie fallback (Odoo app without live camera): accepted but flagged
        res = self.punch(face_descriptor=face(1, 0.01), liveness=False, photo_mode=True)
        self.assertTrue(res['ok'], res['message'])
        self.assertTrue(res['suspicious'])
        self.punch(face_descriptor=face(1, 0.01), liveness=True)  # check out
        self.env['bs.hr.attendance.attempt'].search([]).unlink()  # reset rate limit
        self.env['hr.attendance'].search([('employee_id', '=', self.emp.id)]).unlink()
        self.company.ess_face_photo_fallback = False
        res = self.punch(face_descriptor=face(1, 0.01), liveness=False, photo_mode=True)
        self.assertFalse(res['ok'])
        # an invalid photo never blocks the punch, it is just dropped
        res = self.punch(face_descriptor=face(1, 0.01), liveness=True, photo='aGVsbG8=')
        self.assertTrue(res['ok'], res['message'])
        att = self.env['hr.attendance'].search([('employee_id', '=', self.emp.id)])
        self.assertTrue(att.ess_liveness)
        self.assertLess(att.ess_face_distance, 0.5)

    def test_face_enroll_inconsistent(self):
        self.emp.sudo().ess_face_enroll_allowed = True
        with self.assertRaises(Exception):
            self.emp._ess_face_enroll([face(1), face(2), face(3)])

    def test_not_allowed_branch(self):
        partner = self.env['res.partner'].create({'name': 'Other branch'})
        other = self.env['hr.work.location'].create({
            'name': 'Other', 'address_id': partner.id, 'ess_use_qr': True, 'ess_use_geo': False})
        res = self.punch(qr_code=other._ess_qr_code())
        self.assertFalse(res['ok'])
        self.emp.sudo().ess_allowed_location_ids = [(6, 0, (self.branch | other).ids)]
        self.assertTrue(self.punch(qr_code=other._ess_qr_code())['ok'])

    def test_rate_limit(self):
        for _i in range(8):
            self.punch(**FAR)
        res = self.punch(**NEAR)
        self.assertFalse(res['ok'])
        self.assertIn('Too many', res['message'])

    def test_precheck_hook_blocks(self):
        with patch.object(type(self.env['hr.employee']), '_ess_attendance_precheck',
                          return_value=['Health certificate expired']):
            res = self.punch(**NEAR)
        self.assertFalse(res['ok'])
        self.assertIn('Health certificate', res['message'])

    def test_impossible_travel_flagged(self):
        self.env['hr.attendance'].create({
            'employee_id': self.emp.id, 'check_in': fields.Datetime.now() - timedelta(minutes=30),
            'check_out': fields.Datetime.now() - timedelta(minutes=20),
            'in_latitude': 21.5490, 'in_longitude': 39.1530})  # Jeddah, 20 min ago
        res = self.punch(**NEAR)  # Riyadh now
        self.assertTrue(res['ok'])
        self.assertTrue(res['suspicious'])

    def test_correction_flow(self):
        check_in = fields.Datetime.now() - timedelta(hours=10)
        att = self.env['hr.attendance'].create({'employee_id': self.emp.id, 'check_in': check_in})
        corr = self.env['bs.hr.attendance.correction'].create({
            'employee_id': self.emp.id, 'correction_type': 'missing_out',
            'check_out': check_in + timedelta(hours=8), 'reason': 'Forgot'})
        corr.action_submit()
        self.assertEqual(corr.attendance_id, att)
        corr.with_user(self.user_manager).sudo().action_ess_approve()
        corr.with_user(self.user_hr).sudo().action_ess_approve()
        self.assertEqual(corr.state, 'done')
        self.assertEqual(att.check_out, check_in + timedelta(hours=8))

    def test_lateness(self):
        self.company.ess_late_grace_minutes = 10
        calendar = self.emp.resource_calendar_id
        (self.emp | self.emp_other).write({'tz': 'UTC'})
        calendar.tz = 'UTC'
        # next Monday 08:00 schedule (standard 40h calendar starts 08:00)
        today = fields.Date.today()
        monday = today - timedelta(days=today.weekday()) - timedelta(days=7)
        intervals = calendar._work_intervals_batch(
            datetime.combine(monday, datetime.min.time()).replace(tzinfo=pytz.utc),
            datetime.combine(monday, datetime.max.time()).replace(tzinfo=pytz.utc),
            resources=self.emp.resource_id)[self.emp.resource_id.id]
        start = list(intervals)[0][0].replace(tzinfo=None)
        att = self.env['hr.attendance'].create({
            'employee_id': self.emp.id, 'check_in': start + timedelta(minutes=25),
            'check_out': start + timedelta(hours=4)})
        self.assertEqual(att.ess_late_minutes, 25)
        att2 = self.env['hr.attendance'].create({
            'employee_id': self.emp_other.id, 'check_in': start + timedelta(minutes=5)})
        self.assertEqual(att2.ess_late_minutes, 0)
