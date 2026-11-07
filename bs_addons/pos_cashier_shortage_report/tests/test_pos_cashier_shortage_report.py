from odoo import Command, fields
from odoo.addons.point_of_sale.tests.common import CommonPosTest
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged


@tagged("post_install", "-at_install")
class TestPosCashierShortageReport(CommonPosTest):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.group_ids += cls.env.ref("point_of_sale.group_pos_manager")
        cls.env.user.group_ids += cls.env.ref("hr.group_hr_user")
        cls.second_cash_journal = cls.company_data["default_journal_cash"].copy(
            {
                "name": "Delivery Cash",
                "code": "DCSH",
            }
        )
        cls.second_cash_method = cls.env["pos.payment.method"].create(
            {
                "name": "Delivery Cash",
                "journal_id": cls.second_cash_journal.id,
                "receivable_account_id": cls.company_data[
                    "default_account_receivable"
                ].id,
                "company_id": cls.company.id,
            }
        )
        cls.pos_config_usd.payment_method_ids = [
            Command.link(cls.second_cash_method.id)
        ]
        cls.employee = cls.env["hr.employee"].create(
            {
                "name": "Closing Cashier",
                "company_id": cls.company.id,
            }
        )

    def test_cash_methods_are_snapshotted_separately_and_bank_is_excluded(self):
        self.pos_config_usd.open_ui()
        session = self.pos_config_usd.current_session_id
        session.employee_id = self.employee
        session.set_opening_control(100.0, "Opening float")
        session.store_cashier_cash_counts(
            [
                [self.cash_payment_method.id, 80.0],
                [self.second_cash_method.id, 20.0],
            ]
        )
        session.post_closing_cash_details(80.0)
        session.stop_at = fields.Datetime.now()

        snapshot_vals = session._prepare_cashier_shortage_snapshot_vals()
        self.assertEqual(len(snapshot_vals), 2)
        values_by_method = {vals["payment_method_id"]: vals for vals in snapshot_vals}
        self.assertNotIn(self.bank_payment_method.id, values_by_method)

        default_cash = values_by_method[self.cash_payment_method.id]
        self.assertEqual(default_cash["closing_expected"], 100.0)
        self.assertEqual(default_cash["closing_counted"], 80.0)
        self.assertEqual(default_cash["closing_difference"], -20.0)

        delivery_cash = values_by_method[self.second_cash_method.id]
        self.assertEqual(delivery_cash["closing_expected"], 0.0)
        self.assertEqual(delivery_cash["closing_counted"], 20.0)
        self.assertEqual(delivery_cash["closing_difference"], 20.0)

        session._save_cashier_shortage_snapshot(snapshot_vals)
        report_lines = self.env["pos.cashier.shortage.report"].search(
            [("session_id", "=", session.id)]
        )
        self.assertEqual(len(report_lines), 2)
        self.assertEqual(report_lines.mapped("employee_id"), self.employee)

    def test_report_history_cannot_be_created_manually(self):
        with self.assertRaises(AccessError):
            self.env["pos.cashier.shortage.report"].create({})

    def test_cash_count_submission_rejects_invalid_values_and_non_cash_methods(self):
        self.pos_config_usd.open_ui()
        session = self.pos_config_usd.current_session_id

        with self.assertRaises(UserError):
            session.store_cashier_cash_counts([[self.bank_payment_method.id, 10.0]])
        with self.assertRaises(UserError):
            session.store_cashier_cash_counts(
                [[self.cash_payment_method.id, float("nan")]]
            )

    def test_closing_session_creates_snapshot_and_clears_pending_counts(self):
        self.pos_config_usd.open_ui()
        session = self.pos_config_usd.current_session_id
        session.employee_id = self.employee
        session.set_opening_control(100.0, "Opening float")
        session.store_cashier_cash_counts(
            [
                [self.cash_payment_method.id, 80.0],
                [self.second_cash_method.id, 20.0],
            ]
        )
        session.post_closing_cash_details(80.0)

        result = session.close_session_from_ui()

        self.assertTrue(result["successful"])
        self.assertEqual(session.state, "closed")
        self.assertFalse(session.cashier_cash_count_data)
        report_lines = self.env["pos.cashier.shortage.report"].search(
            [("session_id", "=", session.id)]
        )
        self.assertEqual(len(report_lines), 2)
        self.assertEqual(
            set(report_lines.mapped("payment_method_id").ids),
            {self.cash_payment_method.id, self.second_cash_method.id},
        )
