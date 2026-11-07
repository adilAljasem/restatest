import base64
from datetime import timedelta

from odoo import fields
from odoo.tests import HttpCase, new_test_user, tagged


@tagged('post_install', '-at_install', 'bs_hr_ess')
class TestEssDocumentsPortal(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user_a = new_test_user(cls.env, login='ess_doc_a', password='ess_doc_a', groups='base.group_portal')
        cls.user_b = new_test_user(cls.env, login='ess_doc_b', password='ess_doc_b', groups='base.group_portal')
        cls.emp_a = cls.env['hr.employee'].create({'name': 'Doc A', 'user_id': cls.user_a.id,
                                                   'contract_date_start': '2021-01-01'})
        cls.emp_b = cls.env['hr.employee'].create({'name': 'Doc B', 'user_id': cls.user_b.id})
        today = fields.Date.today()
        health = cls.env.ref('bs_hr_ess_documents.doc_type_health')
        cls.doc_a = cls.env['bs.hr.document'].create({
            'employee_id': cls.emp_a.id, 'type_id': health.id, 'number': 'A1',
            'expiry_date': today + timedelta(days=10)})
        cls.doc_b = cls.env['bs.hr.document'].create({
            'employee_id': cls.emp_b.id, 'type_id': health.id, 'number': 'B1',
            'expiry_date': today - timedelta(days=10)})
        cls.renewal_b = cls.env['bs.hr.contract.renewal'].create({'employee_id': cls.emp_b.id})

    def csrf_token(self):
        return self.opener.get(self.base_url() + '/my/hr').text.split('csrf_token: "')[1].split('"')[0]

    def test_pages_render(self):
        self.authenticate('ess_doc_a', 'ess_doc_a')
        for url in ('/my/hr', '/my/hr/documents', '/my/hr/document/%s' % self.doc_a.id,
                    '/my/hr/document/%s/renew' % self.doc_a.id, '/my/hr/contract'):
            res = self.url_open(url)
            self.assertEqual(res.status_code, 200, url)
        res = self.url_open('/my/hr/documents')
        self.assertIn('ess-badge-expiring', res.text)
        self.assertIn('A1', res.text)
        self.assertNotIn('B1', res.text)

    def test_other_employee_document_404(self):
        self.authenticate('ess_doc_a', 'ess_doc_a')
        for url in ('/my/hr/document/%s' % self.doc_b.id, '/my/hr/document/%s/renew' % self.doc_b.id,
                    '/my/hr/document/999999'):
            self.assertEqual(self.url_open(url).status_code, 404, url)
        res = self.url_open('/my/hr/document/%s/renew' % self.doc_b.id, data={
            'csrf_token': self.csrf_token(), 'new_expiry_date': '2099-01-01'},
            files={'attachments': ('x.pdf', b'%PDF', 'application/pdf')})
        self.assertEqual(res.status_code, 404)
        res = self.url_open('/my/hr/contract/%s/acknowledge' % self.renewal_b.id,
                            data={'csrf_token': self.csrf_token()})
        self.assertEqual(res.status_code, 404)
        self.assertFalse(self.env['bs.hr.document.renewal'].search([('document_id', '=', self.doc_b.id)]))
        res = self.url_open('/my/hr/request/bs.hr.contract.renewal/%s' % self.renewal_b.id)
        self.assertEqual(res.status_code, 404)

    def test_contract_renewal_is_internal(self):
        renewal_a = self.env['bs.hr.contract.renewal'].create({'employee_id': self.emp_a.id})
        self.authenticate('ess_doc_a', 'ess_doc_a')
        res = self.url_open('/my/hr/request/bs.hr.contract.renewal/%s' % renewal_a.id, allow_redirects=False)
        self.assertIn(res.status_code, (302, 303))
        self.assertTrue(res.headers['Location'].endswith('/my/hr/contract'))
        res = self.url_open('/my/hr/request/bs.hr.contract.renewal/%s/cancel' % renewal_a.id,
                            data={'csrf_token': self.csrf_token()})
        self.assertEqual(res.status_code, 404)
        self.assertEqual(renewal_a.state, 'draft')

    def test_upload_renewal(self):
        self.authenticate('ess_doc_a', 'ess_doc_a')
        new_expiry = fields.Date.today() + timedelta(days=365)
        res = self.url_open('/my/hr/document/%s/renew' % self.doc_a.id, data={
            'csrf_token': self.csrf_token(), 'new_number': 'A2',
            'new_expiry_date': fields.Date.to_string(new_expiry)},
            files={'attachments': ('card.pdf', b'%PDF-1.4 card', 'application/pdf')})
        self.assertEqual(res.status_code, 200)
        renewal = self.env['bs.hr.document.renewal'].search([('document_id', '=', self.doc_a.id)])
        self.assertEqual(len(renewal), 1)
        self.assertEqual(renewal.state, 'in_approval')
        self.assertTrue(renewal.portal_source)
        att = self.env['ir.attachment'].search([('res_model', '=', renewal._name), ('res_id', '=', renewal.id)])
        self.assertEqual(base64.b64decode(att.datas), b'%PDF-1.4 card')
        # without a file it is refused
        res = self.url_open('/my/hr/document/%s/renew' % self.doc_a.id, data={
            'csrf_token': self.csrf_token(), 'new_expiry_date': fields.Date.to_string(new_expiry)})
        self.assertEqual(self.env['bs.hr.document.renewal'].search_count([('document_id', '=', self.doc_a.id)]), 1)
