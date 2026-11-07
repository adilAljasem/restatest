from odoo.exceptions import ValidationError
from odoo.tests import tagged

from odoo.addons.bs_hr_ess_base.tests.common import EssCommon


@tagged('post_install', '-at_install', 'bs_hr_ess')
class TestEssGenericRequest(EssCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user_admin = cls.env.ref('base.user_admin')
        cls.rtype = cls.env['bs.hr.request.type'].create({
            'name': 'Test certificate', 'code': 'test_cert', 'generate_letter': True,
            'letter_body': '<p>{{employee_name}} earns {{wage}} at {{company}} for {{bank}}. '
                           '{{unknown}} <script>alert(1)</script></p>',
            'field_ids': [
                (0, 0, {'name': 'Bank', 'key': 'bank', 'field_type': 'char', 'required': True}),
                (0, 0, {'name': 'Copies', 'key': 'copies', 'field_type': 'number'}),
                (0, 0, {'name': 'Lang', 'key': 'lang', 'field_type': 'selection', 'options': 'Arabic, English'}),
            ],
        })
        cls.emp_employee.name = 'ESS <b>Employee</b>'

    def _request(self, values):
        req = self.env['bs.hr.request'].create({'employee_id': self.emp_employee.id, 'type_id': self.rtype.id})
        req._ess_set_values(values)
        return req

    def test_field_validation(self):
        with self.assertRaises(ValidationError):
            self._request({'copies': '2'})
        with self.assertRaises(ValidationError):
            self._request({'bank': 'Rajhi', 'copies': 'abc'})
        with self.assertRaises(ValidationError):
            self._request({'bank': 'Rajhi', 'lang': 'French'})
        req = self._request({'bank': 'Rajhi', 'copies': '2', 'lang': 'English'})
        self.assertEqual(req.values, {'bank': 'Rajhi', 'copies': 2.0, 'lang': 'English'})
        self.assertIn('Bank: Rajhi', req.values_display)

    def test_placeholders_safe(self):
        req = self._request({'bank': 'Rajhi & <Co>'})
        body = str(req._ess_render_letter_body())
        self.assertIn('ESS &lt;b&gt;Employee&lt;/b&gt;', body)
        self.assertIn('6,000.00', body)
        self.assertIn('Rajhi &amp; &lt;Co&gt;', body)
        self.assertIn('{{unknown}}', body)
        self.assertNotIn('<script>', body)

    def test_flow_by_type_and_letter(self):
        flow = self.env['bs.hr.approval.flow'].create({
            'name': 'Cert flow', 'res_model': 'bs.hr.request', 'sequence': 1, 'request_type_id': self.rtype.id,
            'step_ids': [(0, 0, {'name': 'HR only', 'approver_type': 'users',
                                 'user_ids': [(6, 0, self.user_hr.ids)]})]})
        other_type = self.env['bs.hr.request.type'].create({'name': 'Other'})
        other = self.env['bs.hr.request'].create({'employee_id': self.emp_employee.id, 'type_id': other_type.id})
        other.action_submit()
        self.assertNotEqual(other.flow_id, flow)

        req = self._request({'bank': 'Rajhi'})
        req.action_submit()
        self.assertEqual(req.flow_id, flow)
        self.assertEqual(req._ess_lines().mapped('name'), ['HR only'])
        req.with_user(self.user_hr).sudo().action_ess_approve()
        self.assertEqual(req.state, 'done')
        self.assertTrue(req.letter_attachment_id)
        self.assertTrue(req.sudo().verify_token)
        self.assertEqual(self.env['bs.hr.request']._ess_find_by_token(req.sudo().verify_token), req)
        self.assertFalse(self.env['bs.hr.request']._ess_find_by_token('x' * 32))
        self.assertIn('/hr/ess/verify/', req._ess_verify_url())
        self.assertTrue(req._ess_qr_src().startswith('data:image/svg+xml;base64,'))
        html = req.letter_attachment_id.raw.decode()
        self.assertIn('Rajhi', html)  # tests render HTML instead of calling wkhtmltopdf

    def test_required_attachment(self):
        self.rtype.require_attachment = True
        req = self._request({'bank': 'Rajhi'})
        with self.assertRaises(ValidationError):
            req.action_submit()
