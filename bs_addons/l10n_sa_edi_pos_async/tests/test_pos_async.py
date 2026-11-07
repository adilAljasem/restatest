import json
from base64 import b64decode, b64encode
from unittest.mock import patch

from lxml import etree

from odoo.tests import tagged

from odoo.addons.l10n_sa_edi_pos.tests.test_sa_edi_pos import TestSAZATCAPosInvoice

CLEARANCE_API = 'odoo.addons.l10n_sa_edi.models.account_journal.AccountJournal._l10n_sa_api_clearance'
PIH_XPATH = '//*[local-name()="ID"][text()="PIH"]/following-sibling::*/*'
ICV_XPATH = '//*[local-name()="ID"][text()="ICV"]/following-sibling::*[local-name()="UUID"]'


@tagged('post_install_l10n', 'post_install', '-at_install')
class TestZatcaPosAsync(TestSAZATCAPosInvoice):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # non-sandbox so that the real PIH chaining is exercised
        # (changing the API mode resets the journal onboarding, so re-load the demo credentials)
        cls.env.company.l10n_sa_api_mode = 'preprod'
        journal = cls.config.invoice_journal_id
        journal._l10n_sa_load_edi_demo_data()
        pcsid = json.loads(journal.l10n_sa_production_csid_json)
        journal.l10n_sa_production_csid_certificate_id = cls.env['certificate.certificate'].create({
            'name': 'PCSID Certificate',
            'content': b64decode(pcsid['binarySecurityToken']),
        })
        cls.config.l10n_sa_zatca_async = True
        cls.hasher = cls.env['account.edi.xml.ubl_21.zatca']

    def test_generate_pdf_false_runs_zatca_skips_pdf(self):
        # inherited standard test: only valid with the background option disabled
        self.config.l10n_sa_zatca_async = False
        super().test_generate_pdf_false_runs_zatca_skips_pdf()

    def _checkout(self, n):
        orders = self.env['pos.order']
        for _i in range(n):
            data = self.create_ui_order_data(
                pos_order_lines_ui_args=[(self.sa_product, 1.0)],
                customer=self.sa_partner,
                is_invoiced=True,
            )
            res = self.env['pos.order'].with_context(generate_pdf=False).sync_from_ui([data])
            orders |= self.env['pos.order'].browse(res['pos.order'][0]['id'])
        return orders

    def _report_pending(self):
        # what the EDI cron does, without commits (not allowed in tests)
        docs = self.env['account.edi.document'].search([
            ('state', '=', 'to_send'), ('edi_format_id.code', '=', 'sa_zatca'), ('blocking_level', '!=', 'error')])
        docs._process_documents_web_services(with_commit=False)

    def _zatca_doc(self, move):
        return move.edi_document_ids.filtered(lambda d: d.edi_format_id.code == 'sa_zatca')

    def test_checkout_signs_locally_and_cron_reports_in_chain(self):
        submitted = []

        def fake_api(journal, invoice, signed_xml, pcsid):
            submitted.append((invoice.id, signed_xml))
            return {'reportingStatus': 'REPORTED'}

        def no_api(*args, **kwargs):
            raise AssertionError("ZATCA must not be called during checkout")

        with self.with_pos_session():
            journal = self.config.invoice_journal_id
            journal.l10n_sa_latest_submission_hash = False

            with patch(CLEARANCE_API, new=no_api):
                orders = self._checkout(3)
            moves = orders.account_move.sorted('l10n_sa_chain_index')
            self.assertEqual(len(moves), 3)

            # 1. checkout: signed + legal QR, nothing sent yet, consecutive ICV
            for move in moves:
                self.assertTrue(move.l10n_sa_async_signed)
                self.assertTrue(move.l10n_sa_invoice_signature)
                self.assertTrue(move.l10n_sa_qr_code_str)
                self.assertEqual(self._zatca_doc(move).state, 'to_send')
            idx = moves.mapped('l10n_sa_chain_index')
            self.assertEqual(idx, list(range(idx[0], idx[0] + 3)))
            # receipt QR exposed to the POS
            self.assertTrue(orders[0].l10n_sa_invoice_qr_code_str)

            qr_before = moves.mapped('l10n_sa_qr_code_str')
            pih_before = moves.mapped('l10n_sa_async_pih')
            head_after_checkout = journal.l10n_sa_latest_submission_hash

            # 2. background reporting (cron)
            with patch(CLEARANCE_API, new=fake_api):
                self._report_pending()

            for move in moves:
                self.assertEqual(self._zatca_doc(move).state, 'sent', self._zatca_doc(move).error)
            self.assertEqual([s[0] for s in submitted], moves.ids, "must be reported in ICV order")

            # 3. chain integrity of what ZATCA received
            hashes = [self.hasher._l10n_sa_generate_invoice_xml_hash(xml).decode() for _id, xml in submitted]
            for (_id, xml), move in zip(submitted, moves):
                root = etree.fromstring(xml)
                self.assertEqual(root.xpath(PIH_XPATH)[0].text, move.l10n_sa_async_pih)
                self.assertEqual(int(root.xpath(ICV_XPATH)[0].text), move.l10n_sa_chain_index)
            self.assertEqual(pih_before[1], hashes[0], "PIH of invoice 2 = hash of reported invoice 1")
            self.assertEqual(pih_before[2], hashes[1], "PIH of invoice 3 = hash of reported invoice 2")
            self.assertEqual(head_after_checkout, hashes[2], "chain head advanced at checkout = hash of invoice 3")
            self.assertEqual(journal.l10n_sa_latest_submission_hash, hashes[2], "reporting must not move the head back")
            self.assertEqual(moves.mapped('l10n_sa_qr_code_str'), qr_before, "QR printed at checkout stays valid")

            # 4. a new checkout chains onto the last one
            with patch(CLEARANCE_API, new=no_api):
                new_move = self._checkout(1).account_move
            self.assertEqual(new_move.l10n_sa_async_pih, hashes[2])

    def test_option_disabled_keeps_standard_sync(self):
        self.config.l10n_sa_zatca_async = False
        with self.with_pos_session():
            with patch(CLEARANCE_API, new=lambda *a, **k: {'reportingStatus': 'REPORTED'}):
                move = self._checkout(1).account_move
        self.assertFalse(move.l10n_sa_async_signed)
        self.assertEqual(self._zatca_doc(move).state, 'sent')

    def test_b2b_stays_synchronous(self):
        company_partner = self.env['res.partner'].create({
            'name': 'SA Company', 'company_type': 'company', 'country_id': self.env.ref('base.sa').id,
            'vat': '311111111111113', 'street': 'Street', 'city': 'Riyadh', 'zip': '12345',
            'l10n_sa_edi_building_number': '1234', 'l10n_sa_edi_plot_identification': '1234',
            'street2': 'District', 'state_id': self.env.company.state_id.id,
        })
        calls = []

        def fake_api(journal, invoice, signed_xml, pcsid):
            calls.append(invoice.id)
            # like ZATCA, return the cleared invoice with a QR code added
            root = etree.fromstring(signed_xml.encode())
            pih_ref = root.xpath('//*[local-name()="ID"][text()="PIH"]/..')[0]
            cac = pih_ref.nsmap.get('cac') or pih_ref.tag.split('}')[0][1:]
            cbc = 'urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2'
            qr_ref = etree.Element('{%s}AdditionalDocumentReference' % cac)
            etree.SubElement(qr_ref, '{%s}ID' % cbc).text = 'QR'
            att = etree.SubElement(qr_ref, '{%s}Attachment' % cac)
            etree.SubElement(att, '{%s}EmbeddedDocumentBinaryObject' % cbc).text = 'UVI='
            pih_ref.addnext(qr_ref)
            return {'clearanceStatus': 'CLEARED', 'clearedInvoice': b64encode(etree.tostring(root)).decode()}

        with self.with_pos_session():
            data = self.create_ui_order_data(
                pos_order_lines_ui_args=[(self.sa_product, 1.0)], customer=company_partner, is_invoiced=True)
            with patch(CLEARANCE_API, new=fake_api):
                res = self.env['pos.order'].with_context(generate_pdf=False).sync_from_ui([data])
        move = self.env['pos.order'].browse(res['pos.order'][0]['id']).account_move
        self.assertFalse(move.l10n_sa_async_signed)
        self.assertEqual(calls, [move.id], "B2B must be cleared in real time")
