# -*- coding: utf-8 -*-
from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase
from lxml import etree


@tagged("post_install", "-at_install")
class TestPosEmployeeAccessRights(TransactionCase):
    ACCESS_BOOLEAN_FIELDS = (
        "sbl_hide_pos_new_order_button",
        "sbl_hide_pos_delete_order_button",
        "sbl_hide_pos_customer_selection_button",
        "sbl_hide_pos_actions_button",
        "sbl_hide_pos_numpad",
        "sbl_disable_pos_numpad_plus_minus",
        "sbl_disable_pos_qty",
        "sbl_disable_pos_discount_button",
        "sbl_hide_pos_payment",
        "sbl_disable_pos_change_price",
        "sbl_hide_pos_finance_from_product_info",
        "sbl_hide_pos_cash_in_out",
        "sbl_hide_pos_create_customer_button",
        "sbl_hide_pos_edit_customer_button",
        "sbl_hide_pos_payment_customer_button",
        "sbl_hide_pos_payment_invoice_button",
        "sbl_hide_pos_payment_validate_button",
        "sbl_hide_pos_payment_ship_later_button",
        "sbl_hide_pos_tip_button",
        "sbl_hide_pos_open_cashbox_button",
        "sbl_hide_pos_install_app",
        "sbl_hide_pos_orders_menu",
        "sbl_hide_pos_backend_menu",
        "sbl_hide_pos_close_register",
        "sbl_show_pos_closing_register_details",
        "sbl_hide_pos_clear_cache",
        "sbl_hide_pos_debug_window",
        "sbl_hide_pos_action_general_note",
        "sbl_hide_pos_action_customer_note",
        "sbl_hide_pos_action_pricelist",
        "sbl_hide_pos_action_refund",
        "sbl_hide_pos_action_fiscal_position",
        "sbl_hide_pos_action_cancel_order",
        "sbl_hide_pos_action_product_info",
    )

    def _get_accounting_journal(self):
        journal = self.env["account.journal"].search([
            ("company_id", "=", self.env.company.id),
            ("type", "=", "general"),
        ], limit=1)
        journal = journal or self.env["account.journal"].search([
            ("company_id", "=", self.env.company.id),
        ], limit=1)
        if not journal:
            journal = self.env["account.journal"].create({
                "name": "POS Employee Access Test Miscellaneous",
                "code": "PEAT",
                "type": "general",
                "company_id": self.env.company.id,
            })
        return journal

    def _get_move_line_account(self, journal):
        account = journal.default_account_id or self.env["account.account"].search([
            ("active", "=", True),
            ("company_ids", "in", self.env.company.id),
        ], limit=1)
        if not account:
            account = self.env["account.account"].create({
                "name": "POS Employee Access Test Current Asset",
                "code": "PEAT01",
                "account_type": "asset_current",
                "company_ids": [Command.set([self.env.company.id])],
            })
        return account

    def _create_pos_user_with_employee(self, login, hide_refund=False):
        user = self.env["res.users"].create({
            "name": login,
            "login": login,
            "group_ids": [Command.set([self.env.ref("point_of_sale.group_pos_user").id])],
        })
        self.env["hr.employee"].create({
            "name": login,
            "user_id": user.id,
            "sbl_hide_pos_action_refund": hide_refund,
        })
        return user

    def _get_or_create_pos_config(self):
        pos_config = self.env["pos.config"].search(
            [("company_id", "=", self.env.company.id)], limit=1
        )
        if pos_config:
            return pos_config
        journal = self.env["account.journal"].search(
            [("company_id", "=", self.env.company.id), ("type", "=", "bank")],
            limit=1,
        )
        if not journal:
            self.env["account.journal"].create(
                {
                    "name": "POS Employee Access Test Bank",
                    "code": "PEAB",
                    "type": "bank",
                    "company_id": self.env.company.id,
                }
            )
        return self.env["pos.config"].create(
            {"name": "POS Employee Access Test", "self_ordering_mode": "nothing"}
        )

    def test_closing_register_details_field_is_loaded_in_pos(self):
        pos_config = self._get_or_create_pos_config()

        loaded_fields = self.env["hr.employee"]._load_pos_data_fields(pos_config.id)
        self.assertIn("sbl_show_pos_closing_register_details", loaded_fields)

    def test_all_access_rights_are_stored_with_safe_defaults_and_loaded_in_pos(self):
        """Every Employee-form switch must reach the POS client and default to allowed."""
        pos_config = self._get_or_create_pos_config()
        employee_model = self.env["hr.employee"]
        employee = employee_model.create({"name": "POS Access Rights Cashier"})

        loaded_fields = employee_model._load_pos_data_fields(pos_config.id)
        expected_fields = set(self.ACCESS_BOOLEAN_FIELDS) | {
            "sbl_disabled_payment_method_ids",
        }
        self.assertTrue(expected_fields.issubset(employee_model._fields))
        self.assertTrue(expected_fields.issubset(loaded_fields))
        self.assertTrue(
            all(not employee[field_name] for field_name in self.ACCESS_BOOLEAN_FIELDS),
            "POS restrictions must be opt-in for new employees.",
        )

        employee.write({field_name: True for field_name in self.ACCESS_BOOLEAN_FIELDS})
        employee.invalidate_recordset(self.ACCESS_BOOLEAN_FIELDS)
        self.assertTrue(all(employee[field_name] for field_name in self.ACCESS_BOOLEAN_FIELDS))

    def test_disabled_payment_methods_are_stored_and_loaded_in_pos(self):
        pos_config = self._get_or_create_pos_config()
        payment_method = self.env["pos.payment.method"].search(
            [("company_id", "=", self.env.company.id)], limit=1
        )
        if not payment_method:
            self.skipTest("No POS payment method available.")

        employee = self.env["hr.employee"].create({
            "name": "Restricted Payment Method Cashier",
            "sbl_disabled_payment_method_ids": [Command.set(payment_method.ids)],
        })
        employee.invalidate_recordset(["sbl_disabled_payment_method_ids"])

        self.assertEqual(employee.sbl_disabled_payment_method_ids, payment_method)
        self.assertIn(
            "sbl_disabled_payment_method_ids",
            self.env["hr.employee"]._load_pos_data_fields(pos_config.id),
        )

    def test_pos_qweb_extensions_have_their_parent_templates(self):
        """Fail early when an Odoo POS upgrade removes a template we extend."""
        bundle = self.env["ir.qweb"]._get_asset_bundle(
            "point_of_sale._assets_pos", css=False, js=True
        )
        template_names = {
            element.get("t-name")
            for block in bundle.xml()
            if block["type"] == "templates"
            for element, _url, _inherit_from in block["templates"]
            if element.get("t-name")
        }

        module_assets = [
            asset for asset in bundle.javascripts + bundle.templates
            if asset.url.startswith("/dk_pos_employee_access_rights/")
        ]
        self.assertTrue(module_assets, "The module QWeb assets must be in the POS bundle.")
        asset_sources = "\n".join(asset.content for asset in module_assets)
        expected_access_fields = set(self.ACCESS_BOOLEAN_FIELDS) | {
            "sbl_disabled_payment_method_ids",
        }
        missing_field_bindings = {
            field_name for field_name in expected_access_fields
            if field_name not in asset_sources
        }
        self.assertFalse(
            missing_field_bindings,
            "These POS access fields are not used by the POS UI assets: %s"
            % ", ".join(sorted(missing_field_bindings)),
        )

        for asset in (asset for asset in module_assets if asset in bundle.templates):
            content = asset.content.strip()
            root = etree.fromstring(
                (content if content.startswith("<odoo>") else f"<templates>{content}</templates>").encode(),
            )
            for template in root:
                parent = template.get("t-inherit")
                if parent:
                    self.assertIn(
                        parent,
                        template_names,
                        f"{asset.url} extends the missing POS template {parent!r}.",
                    )

    def test_closing_register_details_permission_defaults_to_hidden(self):
        employee = self.env["hr.employee"].create({"name": "Closing Register Cashier"})
        self.assertFalse(employee.sbl_show_pos_closing_register_details)

        employee.sbl_show_pos_closing_register_details = True
        self.assertTrue(employee.sbl_show_pos_closing_register_details)

    def test_closing_move_ref_uses_pos_employee(self):
        employee = self.env["hr.employee"].create({"name": "Closing Move Cashier"})
        session = self.env["pos.session"].new({
            "name": "POS/TEST/00001",
            "employee_id": employee.id,
            "user_id": self.env.user.id,
        })

        self.assertEqual(
            session._sbl_get_closing_move_ref(),
            "POS/TEST/00001 - Employee: Closing Move Cashier",
        )

    def test_closing_move_ref_falls_back_to_session_user(self):
        session = self.env["pos.session"].new({
            "name": "POS/TEST/00002",
            "user_id": self.env.user.id,
        })

        self.assertEqual(
            session._sbl_get_closing_move_ref(),
            f"POS/TEST/00002 - Employee: {self.env.user.name}",
        )

    def test_closing_move_ref_is_applied_to_account_move(self):
        journal = self._get_accounting_journal()

        employee = self.env["hr.employee"].create({"name": "Closing Move Ref Cashier"})
        move = self.env["account.move"].create({
            "journal_id": journal.id,
            "date": fields.Date.context_today(self.env["account.move"]),
            "ref": "POS/TEST/00003",
        })
        session = self.env["pos.session"].new({
            "name": "POS/TEST/00003",
            "employee_id": employee.id,
            "user_id": self.env.user.id,
            "move_id": move.id,
        })

        session._sbl_apply_closing_move_employee_ref()

        self.assertEqual(move.ref, "POS/TEST/00003 - Employee: Closing Move Ref Cashier")

    def test_closing_move_partner_is_applied_to_empty_move_lines_only(self):
        journal = self._get_accounting_journal()
        account = self._get_move_line_account(journal)
        employee_partner = self.env["res.partner"].create({
            "name": "Closing Move Employee Partner",
        })
        existing_partner = self.env["res.partner"].create({
            "name": "Existing Move Line Partner",
        })
        employee = self.env["hr.employee"].create({
            "name": "Closing Move Partner Cashier",
            "work_contact_id": employee_partner.id,
        })
        move = self.env["account.move"].with_context(check_move_validity=False).create({
            "journal_id": journal.id,
            "date": fields.Date.context_today(self.env["account.move"]),
            "ref": "POS/TEST/00004",
            "line_ids": [
                Command.create({
                    "name": "Line without partner",
                    "account_id": account.id,
                    "debit": 10.0,
                }),
                Command.create({
                    "name": "Line with existing partner",
                    "account_id": account.id,
                    "credit": 10.0,
                    "partner_id": existing_partner.id,
                }),
            ],
        })
        session = self.env["pos.session"].new({
            "name": "POS/TEST/00004",
            "employee_id": employee.id,
            "user_id": self.env.user.id,
            "move_id": move.id,
        })

        session._sbl_apply_closing_move_employee_partner()

        empty_line = move.line_ids.filtered(lambda line: line.name == "Line without partner")
        existing_line = move.line_ids.filtered(lambda line: line.name == "Line with existing partner")
        self.assertEqual(empty_line.partner_id, employee_partner)
        self.assertEqual(existing_line.partner_id, existing_partner)

    def test_backend_refund_is_allowed_without_employee_restriction(self):
        user = self._create_pos_user_with_employee(
            "pos_refund_allowed@example.com",
            hide_refund=False,
        )
        order = self.env["pos.order"].with_user(user).new({})

        self.assertTrue(order.sbl_can_refund_pos_order)

    def test_backend_refund_is_blocked_for_restricted_employee(self):
        user = self._create_pos_user_with_employee(
            "pos_refund_blocked@example.com",
            hide_refund=True,
        )
        pos_order = self.env["pos.order"].with_user(user)

        self.assertFalse(pos_order.new({}).sbl_can_refund_pos_order)
        with self.assertRaises(UserError):
            pos_order._sbl_check_can_refund_pos_order()

    def test_refund_line_creation_is_blocked_for_restricted_employee(self):
        user = self._create_pos_user_with_employee(
            "pos_refund_line_blocked@example.com",
            hide_refund=True,
        )

        with self.assertRaises(UserError):
            self.env["pos.order.line"].with_user(user).create({
                "refunded_orderline_id": 1,
            })
