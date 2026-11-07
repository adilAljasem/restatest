"""Check receipt compute dependencies without requiring a database."""

import importlib.util
import unittest
from pathlib import Path

from odoo.addons.base.models import res_partner

# Odoo 18 names the class `Partner`, Odoo 19 `ResPartner`.
ResPartner = getattr(res_partner, 'ResPartner', None) or res_partner.Partner


RECEIPT_CONFIG = Path(__file__).resolve().parents[1] / 'models' / 'receipt_config.py'
spec = importlib.util.spec_from_file_location(
    'odoo.addons.odoo_pos_custom_branding.models.receipt_config', RECEIPT_CONFIG
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class TestReceiptDependencies(unittest.TestCase):
    def test_partner_dependencies_exist_in_base(self):
        """An optional partner field must not prevent module installation."""
        depends = module.PosConfig._compute_bilingual_receipt_branch_contact._depends
        partner_fields = {
            name.rsplit('.', 1)[-1]
            for name in depends
            if name.startswith('warehouse_id.partner_id.')
        }
        missing = partner_fields - ResPartner.__dict__.keys()
        self.assertFalse(missing, f'Unsupported base res.partner dependencies: {missing}')


if __name__ == '__main__':
    unittest.main()
