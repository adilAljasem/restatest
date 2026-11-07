from unittest.mock import patch

from odoo.exceptions import ValidationError
from odoo.tests import tagged

from .test_payment_clearing import PosClearingCommon


@tagged("post_install", "-at_install")
class TestPaymentMethodPartner(PosClearingCommon):
    def test_split_payment_requires_configured_partner(self):
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.cash_payment_method.split_transactions = True

        self.cash_payment_method.write({
            "split_transactions": True,
            "partner_id": self.partner_mobt.id,
        })
        self.assertEqual(self.cash_payment_method.partner_id, self.partner_mobt)

        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.cash_payment_method.partner_id = False

    def test_configured_partner_is_loaded_for_pos(self):
        partner = self.partner_mobt.copy({"name": "Payment Method Customer"})
        self.cash_payment_method.write({
            "split_transactions": True,
            "partner_id": partner.id,
        })
        fields = self.cash_payment_method._load_pos_data_fields(self.config)
        self.assertIn("partner_id", fields)

        data = {"pos.order": []}
        with patch.object(type(self.config), "get_limited_partners_loading", return_value=[]):
            domain = self.env["res.partner"]._load_pos_data_domain(data, self.config)
        self.assertIn(partner, self.env["res.partner"].search(domain))

    def test_pay_later_becomes_bank_requires_partner(self):
        self.assertTrue(self.credit_payment_method.split_transactions)
        self.assertFalse(self.credit_payment_method.partner_id)
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.credit_payment_method.write({
                "journal_id": self.bank_payment_method.journal_id.id,
                "outstanding_account_id": self.bank_outstanding.id,
            })
