import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from lxml import etree


WORKSPACE = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(WORKSPACE / 'odoo'))

model_source = Path(__file__).resolve().parents[1] / 'models/pos_receipt.py'
spec = importlib.util.spec_from_file_location(
    'odoo.addons.odoo_pos_custom_branding.models.pos_receipt', model_source,
)
pos_receipt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pos_receipt)


class ExistingReceipt:
    def __init__(self, template):
        self.name = 'Bilingual Thermal Receipt'
        self.design_receipt = template
        self.written = None

    def exists(self):
        return self

    def write(self, values):
        self.written = values


class ReceiptModel:
    def __init__(self, receipt):
        self.receipt = receipt

    def browse(self, receipt_id):
        if receipt_id != 42:
            raise AssertionError('unexpected receipt id')
        return self.receipt


class ReceiptNameMigrationTest(unittest.TestCase):
    def test_existing_noupdate_receipt_gets_new_name_without_replacing_template(self):
        source = Path(__file__).resolve().parents[1] / 'data/bilingual_thermal_receipt_data.xml'
        template = etree.parse(str(source)).xpath(
            "//record[@id='bilingual_thermal_receipt']/field[@name='design_receipt']"
        )[0].text
        receipt = ExistingReceipt(template)
        model = ReceiptModel(receipt)

        with patch.object(pos_receipt, 'file_open', side_effect=lambda _path, _mode: source.open('rb')):
            pos_receipt.PosReceipt.migrate_bilingual_thermal_receipt(model, 42)

        self.assertEqual(receipt.written, {'name': 'RYVK Thermal Receipt'})


if __name__ == '__main__':
    unittest.main()
