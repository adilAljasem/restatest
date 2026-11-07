import unittest
from pathlib import Path

from lxml import etree


class ReceiptRootTest(unittest.TestCase):
    def _thermal_template(self):
        source = Path(__file__).resolve().parents[1] / 'data/bilingual_thermal_receipt_data.xml'
        data = etree.parse(str(source))
        template = data.xpath("//record[@id='bilingual_thermal_receipt']/field[@name='design_receipt']")[0].text
        etree.fromstring(template.encode())
        return etree.HTML(template)

    def test_custom_design_is_receipt_root_for_printer_raster(self):
        template = Path(__file__).resolve().parents[1] / 'static/src/xml/order_receipt.xml'
        document = etree.parse(str(template))
        custom_branch = document.xpath("//t[@t-if='isTrue']")[0]

        self.assertEqual(len(custom_branch), 1)
        self.assertEqual(custom_branch[0].get('t-component'), 'templateComponent')

    def test_thermal_receipt_keeps_compact_flow_and_zatca_details(self):
        receipt = self._thermal_template()

        self.assertEqual(len(receipt.xpath("//header//section[contains(@class, 'bilingual-order-wrap')]")), 1)
        self.assertEqual(len(receipt.xpath("//table[contains(@class, 'classic-items-table')]/colgroup/col")), 2)
        self.assertEqual(len(receipt.xpath("//footer//*[contains(@class, 'bilingual-contact')]")), 1)
        self.assertEqual(len(receipt.xpath("//*[contains(@class, 'bilingual-zatca')]/img")), 1)
        self.assertEqual(len(receipt.xpath("//*[contains(@class, 'bilingual-legal-vat')]")), 1)

    def test_thermal_receipt_has_only_the_sa_tax_qr(self):
        receipt = self._thermal_template()

        self.assertEqual(len(receipt.xpath("//img[@id='posqrcode']")), 0)
        self.assertEqual(len(receipt.xpath("//*[contains(@class, 'bilingual-zatca')]/img")), 1)

    def test_thermal_design_has_ryvk_display_name(self):
        source = Path(__file__).resolve().parents[1] / 'data/bilingual_thermal_receipt_data.xml'
        document = etree.parse(str(source))
        name = document.xpath("//record[@id='bilingual_thermal_receipt']/field[@name='name']")[0].text

        self.assertEqual(name, 'RYVK Thermal Receipt')

    def test_pino_sales_design_is_selectable_and_keeps_its_sales_sections(self):
        source = Path(__file__).resolve().parents[1] / 'data/pino_sales_receipt_data.xml'
        document = etree.parse(str(source))
        records = document.xpath("//record[@id='pino_sales_receipt' and @model='pos.receipt']")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].xpath("field[@name='name']")[0].text, 'Puls Thermal Receipt')
        template = records[0].xpath("field[@name='design_receipt']")[0].text
        receipt = etree.fromstring(template.encode())

        self.assertEqual(receipt.get('class'), 'pos-receipt pino-receipt')
        self.assertEqual(len(receipt.xpath("//*[contains(@class, 'pino-totals')]")), 1)
        self.assertEqual(len(receipt.xpath("//*[contains(@class, 'pino-qr-img')]")), 1)
        self.assertEqual(len(receipt.xpath("//*[contains(@class, 'pino-barcode')]")), 0)
        self.assertEqual(len(receipt.xpath("//*[contains(@class, 'pino-slogan')][@t-foreach='p.bottomLines']")), 1)

    def test_pino_layout_matches_requested_thermal_layout(self):
        source = Path(__file__).resolve().parents[1] / 'data/pino_sales_receipt_data.xml'
        template = etree.parse(str(source)).xpath(
            "//record[@id='pino_sales_receipt']/field[@name='design_receipt']")[0].text
        receipt = etree.fromstring(template.encode())

        self.assertEqual(len(receipt.xpath("//*[contains(@class, 'pino-order-box')]")), 1)
        self.assertEqual(len(receipt.xpath("//*[contains(@class, 'pino-payments')]")), 1)
        # Layout of 19.0.1.1.0: English lines only behind their settings.
        for css_class, condition in (('pino-head-en', 'p.show.itemEnglish'),
                                     ('pino-total-en', None), ('pino-title-en', 'p.show.totalsEnglish')):
            nodes = receipt.xpath(f"//*[contains(@class, '{css_class}')]")
            self.assertTrue(nodes, css_class)
            if condition:
                self.assertTrue(all(node.get('t-if') == condition for node in nodes), css_class)
        # Customer service phone, order type with table, customer on its own row.
        self.assertIn('خدمة العملاء', template)
        self.assertIn('نوع الطلب / الطاولة', template)
        self.assertIn('المتبقي للعميل', template)
        # Original design of 19.0.1.1.0: no pattern band, no dashed lines, no
        # branch lines and no product / dish counts.
        for css_class in ('pino-pattern', 'pino-rule', 'pino-branch', 'pino-counts'):
            self.assertEqual(receipt.xpath(f"//*[contains(@class, '{css_class}')]"), [], css_class)
        # Functional features kept: delivery card, attribute values and notes, print width.
        for css_class in ('pino-delivery', 'pino-attr-row', 'pino-note'):
            self.assertTrue(receipt.xpath(f"//*[contains(@class, '{css_class}')]"), css_class)
        # Delivery card: only the app name and the app order number.
        card = etree.tostring(receipt.xpath("//*[@class='pino-delivery']")[0], encoding='unicode')
        self.assertIn('p.delivery.platform', card)
        self.assertIn('p.delivery.number', card)
        for removed in ('details', 'externalId', 'p.delivery.type'):
            self.assertNotIn(removed, card)
        self.assertIn('rootClass', receipt.get('t-att-class'))
        # POS name in the same row as the customer.
        row = receipt.xpath("//*[contains(@class, 'pino-customer-row')]")[0]
        self.assertIn('العميل', etree.tostring(row, encoding='unicode'))
        self.assertIn('p.posName', etree.tostring(row, encoding='unicode'))
        # Cashiers who opened / closed the order, right under the POS row.
        cashiers = receipt.xpath("//*[contains(@class, 'pino-cashier-row')]")[0]
        self.assertEqual(cashiers.getprevious(), row)
        cashiers_xml = etree.tostring(cashiers, encoding='unicode')
        for expected in ('p.openedBy', 'p.closedBy', 'فتح الطلب', 'أغلق الطلب'):
            self.assertIn(expected, cashiers_xml)
        self.assertEqual(receipt.xpath("//t[@t-set='p']")[0].get('t-value'), 'props.data.pino_receipt_data')
        self.assertTrue(all(node.get('t-if') == 'p.show.unitPrice'
                            for node in receipt.xpath("//*[contains(@class, 'pino-unit')]")))
        qr = receipt.xpath("//*[contains(@class, 'pino-qr')][img]")[0]
        self.assertEqual(len(qr), 1, 'QR block must hold only the QR image')
        self.assertNotIn('هيئة الزكاة', template)
        self.assertEqual(receipt.xpath("string(//*[contains(@class, 'pino-powered')])"), 'Powered by basicsolution.net')
        head = receipt.xpath("//*[contains(@class, 'pino-line-head')]")[0]
        self.assertEqual(
            [span.get('class') for span in head],
            ['pino-qty', 'pino-name', 'pino-unit', 'pino-price'],
        )
        self.assertEqual(len(receipt.xpath("//*[contains(@class, 'pino-line-main')]/*[contains(@class, 'pino-unit')]")), 1)


if __name__ == '__main__':
    unittest.main()
