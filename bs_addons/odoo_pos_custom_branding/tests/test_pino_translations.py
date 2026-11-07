import importlib.util
import sys
import unittest
from pathlib import Path


odoo_root = Path(__file__).resolve().parents[3] / 'odoo'
sys.path.insert(0, str(odoo_root))
source = Path(__file__).resolve().parents[1] / 'models/product_template.py'
spec = importlib.util.spec_from_file_location(
    'odoo.addons.odoo_pos_custom_branding.models.pino_receipt_product_template', source,
)
product_template = importlib.util.module_from_spec(spec)
spec.loader.exec_module(product_template)


class Langs:
    def __init__(self, installed):
        self.installed = installed

    def get_installed(self):
        return [(code, code) for code in self.installed]


class TranslationRows:
    """Minimal recordset: one saved record (id 7) with Arabic/English names."""

    def __init__(self, language=None, installed=('ar_001', 'en_US')):
        self.language = language
        self.installed = installed
        self.id = 7
        self.env = {'res.lang': Langs(installed)}

    def __iter__(self):
        return iter([self])

    def __bool__(self):
        return True

    def filtered(self, predicate):
        return self if predicate(self) else []

    def with_context(self, lang):
        return TranslationRows(lang, self.installed)

    def read(self, fields):
        if self.language == 'ar_001':
            return [{'id': 7, 'name': 'قهوة'}]
        if self.language == 'en_US':
            return [{'id': 7, 'name': 'Coffee'}]
        raise AssertionError(f'unexpected language: {self.language}')


class PinoTranslationsTest(unittest.TestCase):
    def test_reads_product_name_from_odoo_translation_contexts(self):
        names = product_template._read_pos_receipt_translations(TranslationRows())

        self.assertEqual(names, {7: {'ar': 'قهوة', 'en': 'Coffee'}})

    def test_inactive_arabic_is_skipped_instead_of_raising(self):
        names = product_template._read_pos_receipt_translations(
            TranslationRows(installed=('en_US',)))

        self.assertEqual(names, {7: {'ar': '', 'en': 'Coffee'}})


if __name__ == '__main__':
    unittest.main()
