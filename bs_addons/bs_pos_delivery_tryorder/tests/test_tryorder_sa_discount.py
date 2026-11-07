from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestTryOrderSaudiDiscountTax(TransactionCase):
    def test_sa_discount_needs_company_out_of_scope_zero_tax(self):
        Tax = self.env["account.tax"]
        if "l10n_sa_exemption_reason_code" not in Tax._fields:
            self.skipTest("Saudi EDI is not installed")

        company = self.env.company
        company.country_id = self.env.ref("base.sa")
        provider = self.env["bs.delivery.provider"].search([
            ("code", "=", "tryorder"),
        ], limit=1)
        order = self.env["bs.delivery.order"].new({
            "company_id": company.id,
            "provider_id": provider.id,
        })
        taxes = Tax.with_company(company).with_context(active_test=False).search([
            ("company_id", "=", company.id),
            ("type_tax_use", "=", "sale"),
            ("amount_type", "=", "percent"),
            ("amount", "=", 0),
            ("l10n_sa_exemption_reason_code", "=", "VATEX-SA-OOS"),
        ])
        taxes.write({"active": False})
        with self.assertRaisesRegex(UserError, "out-of-scope"):
            order._untaxed_discount_line_taxes()

        tax = Tax.with_company(company).create({
            "name": "Test Saudi Out of Scope Discount",
            "company_id": company.id,
            "type_tax_use": "sale",
            "amount": 0,
            "l10n_sa_exemption_reason_code": "VATEX-SA-OOS",
        })
        self.assertEqual(order._untaxed_discount_line_taxes(), tax)
        blend = self.env["bs.delivery.provider"].search([("code", "=", "blend")], limit=1)
        order.provider_id = blend
        self.assertFalse(order._untaxed_discount_line_taxes())
