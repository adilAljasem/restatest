from lxml import etree

from odoo.exceptions import UserError, ValidationError
from odoo.addons.base.tests.common import BaseCommon


class TestCompanyVendorBillValidation(BaseCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.localized_country = cls.env.ref("base.sa")
        cls.localized_state = cls.env["res.country.state"].search(
            [("country_id", "=", cls.localized_country.id)], limit=1
        )
        if not cls.localized_state:
            cls.localized_state = cls.env["res.country.state"].create(
                {
                    "name": "Test Riyadh",
                    "code": "TRY",
                    "country_id": cls.localized_country.id,
                }
            )

        cls.test_country = cls.env["res.country"].search(
            [("code", "=", "XZ")], limit=1
        ) or cls.env["res.country"].create(
            {"name": "Mandatory Fields Test Country", "code": "XZ"}
        )
        cls.test_state = cls.env["res.country.state"].create(
            {
                "name": "Mandatory Fields Test State",
                "code": "MFS",
                "country_id": cls.test_country.id,
            }
        )

        cls.purchase_journal = cls.env["account.journal"].create(
            {
                "name": "Mandatory Fields Purchase Journal",
                "code": "MFPJ",
                "type": "purchase",
                "company_id": cls.env.company.id,
            }
        )
        cls.sale_journal = cls.env["account.journal"].create(
            {
                "name": "Mandatory Fields Sale Journal",
                "code": "MFSJ",
                "type": "sale",
                "company_id": cls.env.company.id,
            }
        )
        cls.general_journal = cls.env["account.journal"].create(
            {
                "name": "Mandatory Fields General Journal",
                "code": "MFGJ",
                "type": "general",
                "company_id": cls.env.company.id,
            }
        )

    def _company_values(self, localized=False):
        country = self.localized_country if localized else self.test_country
        state = self.localized_state if localized else self.test_state
        values = {
            "name": "Complete Company",
            "company_type": "company",
            "street": "King Fahd Road",
            "street2": "Al Olaya District",
            "vat": "311111111111113" if localized else "VAT-001",
            "city": "Riyadh" if localized else "Test City",
            "state_id": state.id,
            "zip": "12345",
            "country_id": country.id,
        }
        if localized:
            values.update(
                {
                    "l10n_sa_edi_building_number": "1234",
                    "l10n_sa_edi_plot_identification": "5678",
                }
            )
        return values

    def test_required_localization_modules_are_installed(self):
        modules = self.env["ir.module.module"].search(
            [("name", "in", ["l10n_sa_edi", "l10n_sa_pos"])]
        )
        self.assertEqual(set(modules.mapped("name")), {"l10n_sa_edi", "l10n_sa_pos"})
        self.assertEqual(set(modules.mapped("state")), {"installed"})

    def test_company_base_fields_are_required(self):
        required_fields = (
            "street",
            "street2",
            "vat",
            "city",
            "state_id",
            "zip",
            "country_id",
        )
        for field_name in required_fields:
            values = self._company_values()
            values.pop(field_name)
            with self.subTest(field_name=field_name), self.assertRaises(
                ValidationError
            ):
                self.env["res.partner"].create(values)

    def test_company_error_lists_only_empty_fields(self):
        """Error must mention only fields that are still empty."""
        values = self._company_values()
        values.pop("city")
        values.pop("zip")
        with self.assertRaises(ValidationError) as error:
            self.env["res.partner"].create(values)

        message = error.exception.args[0]
        partner_fields = self.env["res.partner"]._fields
        listed_labels = {
            line[2:].strip()
            for line in message.splitlines()
            if line.startswith("- ")
        }
        self.assertEqual(
            listed_labels,
            {partner_fields["city"].string, partner_fields["zip"].string},
        )

    def test_localized_company_edi_address_fields_are_required(self):
        for field_name in (
            "l10n_sa_edi_building_number",
            "l10n_sa_edi_plot_identification",
        ):
            values = self._company_values(localized=True)
            values.pop(field_name)
            with self.subTest(field_name=field_name), self.assertRaises(
                ValidationError
            ):
                self.env["res.partner"].create(values)

    def test_complete_company_can_be_saved(self):
        company = self.env["res.partner"].create(self._company_values(localized=True))
        self.assertEqual(company.company_type, "company")

    def test_company_cannot_clear_a_required_field(self):
        company = self.env["res.partner"].create(self._company_values())
        with self.assertRaises(ValidationError):
            company.street = False

    def test_individual_is_not_affected(self):
        individual = self.env["res.partner"].create(
            {
                "name": "Localized Individual",
                "company_type": "person",
                "country_id": self.localized_country.id,
            }
        )
        self.assertEqual(individual.company_type, "person")

    def test_person_selected_from_contacts_action_is_not_affected(self):
        individual = self.env["res.partner"].with_context(
            default_is_company=True
        ).create(
            {
                "name": "Person Selected From Contacts",
                "company_type": "person",
                "country_id": self.localized_country.id,
            }
        )
        self.assertFalse(individual.is_company)
        self.assertEqual(individual.company_type, "person")

    def test_company_can_be_changed_to_person_without_required_fields(self):
        company = self.env["res.partner"].create(
            self._company_values(localized=True)
        )
        company.write(
            {
                "company_type": "person",
                "street": False,
                "street2": False,
                "vat": False,
                "city": False,
                "state_id": False,
                "zip": False,
                "l10n_sa_edi_building_number": False,
                "l10n_sa_edi_plot_identification": False,
            }
        )
        self.assertFalse(company.is_company)
        self.assertEqual(company.company_type, "person")

    def test_vendor_bill_requires_invoice_date(self):
        with self.assertRaises(ValidationError):
            self.env["account.move"].create(
                {
                    "move_type": "in_invoice",
                    "journal_id": self.purchase_journal.id,
                }
            )

        vendor_bill = self.env["account.move"].create(
            {
                "move_type": "in_invoice",
                "journal_id": self.purchase_journal.id,
                "invoice_date": "2026-09-05",
            }
        )
        with self.assertRaises(ValidationError):
            vendor_bill.invoice_date = False

    def test_vendor_bill_cannot_be_confirmed_without_payment_terms(self):
        vendor_bill = self.env["account.move"].create(
            {
                "move_type": "in_invoice",
                "journal_id": self.purchase_journal.id,
                "invoice_date": "2026-09-05",
            }
        )
        with self.assertRaisesRegex(
            UserError,
            "Payment Terms are required before confirming a Vendor Bill",
        ):
            vendor_bill.action_post()

    def test_other_account_move_types_are_not_affected(self):
        move_values = (
            {"move_type": "out_invoice", "journal_id": self.sale_journal.id},
            {"move_type": "in_refund", "journal_id": self.purchase_journal.id},
            {"move_type": "out_refund", "journal_id": self.sale_journal.id},
            {"move_type": "entry", "journal_id": self.general_journal.id},
        )
        moves = self.env["account.move"]
        for values in move_values:
            moves |= self.env["account.move"].create(values)
        self.assertTrue(all(not move.invoice_date for move in moves))

    def test_vendor_bill_due_date_is_hidden_in_views(self):
        form_arch = self.env["account.move"].get_view(
            self.env.ref("account.view_move_form").id, "form"
        )["arch"]
        form_root = etree.fromstring(form_arch)
        due_date_field = form_root.xpath(
            "//div[@name='due_date']/field[@name='invoice_date_due']"
        )[0]
        self.assertIn("move_type == 'in_invoice'", due_date_field.get("invisible"))
        payment_term_field = form_root.xpath(
            "//div[@name='due_date']/field[@name='invoice_payment_term_id']"
        )[0]
        self.assertEqual(
            " ".join(payment_term_field.get("required").split()),
            "move_type == 'in_invoice' and state == 'draft'",
        )

        list_arch = self.env["account.move"].get_view(
            self.env.ref("account.view_invoice_tree").id, "list"
        )["arch"]
        list_root = etree.fromstring(list_arch)
        list_due_date_field = list_root.xpath("//field[@name='invoice_date_due']")[0]
        self.assertIn(
            "default_move_type') == 'in_invoice'",
            list_due_date_field.get("column_invisible"),
        )
