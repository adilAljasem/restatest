import ast
import re
import unittest
from pathlib import Path

import sass
from lxml import etree


ADDON = Path(__file__).resolve().parents[1]


def compiled_css(relative_path):
    return sass.compile(filename=str(ADDON / relative_path), output_style='expanded')


def receipt_root(relative_path, record_id):
    document = etree.parse(str(ADDON / relative_path))
    template = document.xpath(
        f"//record[@id='{record_id}']/field[@name='design_receipt']"
    )[0].text
    return etree.fromstring(template.strip().encode())


class ReceiptPaperTest(unittest.TestCase):
    def test_legacy_designs_opt_in_to_shared_paper_layout(self):
        for filename, record_id in (
            ('pos_receipt_design1_data.xml', 'pos_receipt_design1'),
            ('pos_receipt_design2_data.xml', 'pos_receipt_design2_demo'),
        ):
            with self.subTest(record_id=record_id):
                root = receipt_root(f'data/{filename}', record_id)
                self.assertIn('branding-paper', root.get('class').split())

    def test_shared_paper_rules_cover_browser_and_raster_receipts(self):
        manifest = ast.literal_eval((ADDON / '__manifest__.py').read_text(encoding='utf-8'))
        self.assertIn(
            'odoo_pos_custom_branding/static/src/scss/receipt_paper.scss',
            manifest['assets']['point_of_sale._assets_pos'],
        )
        css = compiled_css('static/src/scss/receipt_paper.scss')
        for receipt_class in ('branding-paper', 'pino-receipt'):
            with self.subTest(receipt_class=receipt_class):
                self.assertTrue(
                    re.search(rf'\.pos-receipt\.{receipt_class}[^{{]*\{{[^}}]*width:\s*76mm[^}}]*padding-left:\s*1mm[^}}]*padding-right:\s*1mm', css),
                    f'{receipt_class} needs a 76mm base layout with 1mm inner padding',
                )
        self.assertRegex(
            css,
            r'\.pos-receipt\.branding-paper\.pos-receipt-print\s*\{[^}]*width:\s*512px[^}]*padding-left:\s*0[^}]*padding-right:\s*0',
            'legacy designs must not gain hardware raster side padding',
        )
        self.assertRegex(
            css,
            r'\.pos-receipt\.pino-receipt\.pos-receipt-print\s*\{[^}]*width:\s*512px[^}]*padding-left:\s*8px[^}]*padding-right:\s*8px',
            'Puls defaults to Odoo 18 512-dot raster so no printer cuts the right side',
        )
        self.assertRegex(
            css,
            r'\.pos-receipt\.pino-receipt\.pino-w-576\.pos-receipt-print\s*\{[^}]*width:\s*576px[^}]*padding-left:\s*16px[^}]*padding-right:\s*16px',
            '576 (default) fills the 576-dot printable width with equal side margins',
        )
        self.assertRegex(
            css,
            r'\.pos-receipt\.pino-receipt\.pino-w-420\.pos-receipt-print\s*\{[^}]*width:\s*420px',
            '58mm printers print a 420-dot image',
        )

    def test_bilingual_receipt_uses_same_paper_safe_area(self):
        css = compiled_css('static/src/scss/bilingual_receipt.scss')
        self.assertFalse('266px' in css, 'bilingual layout still uses Odoo 266px width')
        self.assertRegex(
            css,
            r'\.pos-receipt\.bilingual-receipt\s*\{[^}]*width:\s*72mm[^}]*padding:\s*2mm 1mm',
        )
        self.assertRegex(
            css,
            r'\.pos-receipt\.bilingual-receipt\.bilingual-receipt--thermal\.pos-receipt-print[^{}]*\{[^}]*width:\s*512px[^}]*padding:\s*0 8px',
        )

    def test_puls_browser_print_fits_the_printable_width(self):
        css = compiled_css('static/src/scss/receipt_paper.scss').split('@media print', 1)[1]
        rules = [
            (match.group(1), match.group(2))
            for match in re.finditer(r'([^{}]+)\{([^{}]+)\}', css)
        ]
        standard = [body for selector, body in rules
                    if '.render-container .pos-receipt.pino-receipt' in selector
                    and 'pino-w-' not in selector and 'pino-wide' not in selector]
        narrow = [body for selector, body in rules if 'pino-w-420' in selector]
        legacy_rules = [body for selector, body in rules if '.render-container .pos-receipt.branding-paper' in selector]

        self.assertTrue(standard, 'Puls needs a browser-print rule')
        self.assertIn('width: 72mm !important', standard[-1], 'must fit the 72mm printable area of 80mm drivers')
        self.assertIn('max-width: 72mm !important', standard[-1])
        # 576 dots = 72mm: never wider than the printable area.
        self.assertFalse([body for selector, body in rules if 'pino-wide' in selector and '78mm' in body])
        self.assertTrue(narrow and 'width: 48mm !important' in narrow[-1], '58mm option needs its own print rule')
        self.assertTrue(legacy_rules, 'legacy print layout must remain available')
        self.assertIn('width: 76mm !important', legacy_rules[-1])

if __name__ == '__main__':
    unittest.main()
