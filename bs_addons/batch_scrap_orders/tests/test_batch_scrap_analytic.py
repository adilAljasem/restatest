from odoo import Command
from odoo.exceptions import UserError

from odoo.addons.stock_account.tests.common import TestStockValuationCommon


class TestBatchScrapAnalytic(TestStockValuationCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.loss_account = cls.account_expense
        cls.inventory_location.valuation_account_id = cls.loss_account
        cls.plan = cls.env["account.analytic.plan"].create(
            {"name": "Batch Scrap Plan", "default_applicability": "optional"},
        )
        cls.analytic_account = cls.env["account.analytic.account"].create(
            {
                "name": "Batch Scrap Branch",
                "plan_id": cls.plan.id,
                "company_id": cls.company.id,
            },
        )
        cls.env["stock.quant"]._update_available_quantity(
            cls.product_standard_auto, cls.stock_location, 10,
        )

    def _create_batch(self, **values):
        return self.env["batch.scrap.order"].create(
            {
                "company_id": self.company.id,
                "location_id": self.stock_location.id,
                "scrap_location_id": self.inventory_location.id,
                "analytic_account_id": self.analytic_account.id,
                "line_ids": [
                    Command.create(
                        {"product_id": self.product_standard_auto.id, "quantity": 2},
                    ),
                    Command.create(
                        {"product_id": self.product_standard_auto.id, "quantity": 3},
                    ),
                ],
                **values,
            },
        )

    def _create_default_distribution(self):
        default_account = self.analytic_account.copy({"name": "Default Branch"})
        self.env["account.analytic.distribution.model"].create(
            {
                "company_id": self.company.id,
                "account_prefix": self.loss_account.code,
                "analytic_distribution": {str(default_account.id): 100.0},
            },
        )
        return default_account

    def test_loss_line_only_and_no_duplicate_analytic_items(self):
        self._create_default_distribution()
        batch = self._create_batch()
        batch.action_validate()

        moves = batch.scrap_ids.move_ids
        entries = moves.account_move_id
        self.assertEqual(len(entries), 2)
        self.assertEqual(set(entries.mapped("state")), {"posted"})
        loss_lines = entries.line_ids.filtered(
            lambda line: line.account_id == self.loss_account,
        )
        stock_lines = entries.line_ids - loss_lines
        self.assertEqual(len(loss_lines), 2)
        self.assertEqual(len(stock_lines), 2)
        self.assertEqual(sum(loss_lines.mapped("debit")), 50.0)
        self.assertEqual(sum(stock_lines.mapped("credit")), 50.0)
        for line in loss_lines:
            self.assertEqual(
                line.analytic_distribution, {str(self.analytic_account.id): 100.0},
            )
        self.assertFalse(any(stock_lines.mapped("analytic_distribution")))
        self.assertFalse(moves.analytic_account_line_ids)
        self.assertEqual(len(loss_lines.analytic_line_ids), 2)
        self.assertEqual(sum(loss_lines.analytic_line_ids.mapped("amount")), -50.0)

    def test_empty_account_keeps_default_distribution(self):
        default_account = self._create_default_distribution()
        batch = self._create_batch(analytic_account_id=False)
        batch.action_validate()

        loss_lines = batch.scrap_ids.move_ids.account_move_id.line_ids.filtered(
            lambda line: line.account_id == self.loss_account,
        )
        self.assertEqual(len(loss_lines), 2)
        for line in loss_lines:
            self.assertEqual(
                line.analytic_distribution, {str(default_account.id): 100.0},
            )

    def test_no_journal_entry_does_not_create_analytic_items(self):
        self.inventory_location.valuation_account_id = False
        batch = self._create_batch()
        batch.action_validate()

        self.assertEqual(batch.state, "done")
        self.assertFalse(batch.scrap_ids.move_ids.account_move_id)
        self.assertFalse(batch.scrap_ids.move_ids.analytic_account_line_ids)

    def test_standalone_scrap_keeps_default_distribution(self):
        default_account = self._create_default_distribution()
        scrap = self.env["stock.scrap"].create(
            {
                "product_id": self.product_standard_auto.id,
                "scrap_qty": 1,
                "location_id": self.stock_location.id,
                "scrap_location_id": self.inventory_location.id,
            },
        )
        scrap.action_validate()
        loss_line = scrap.move_ids.account_move_id.line_ids.filtered(
            lambda line: line.account_id == self.loss_account,
        )
        self.assertEqual(
            loss_line.analytic_distribution, {str(default_account.id): 100.0},
        )

    def test_analytic_account_locked_for_validated_recordset(self):
        draft = self._create_batch()
        done = self._create_batch()
        done.action_validate()

        with self.assertRaises(UserError):
            (draft | done).write({"analytic_account_id": False})
        self.assertEqual(draft.analytic_account_id, self.analytic_account)
        self.assertEqual(done.analytic_account_id, self.analytic_account)
        draft.analytic_account_id = False
        self.assertFalse(draft.analytic_account_id)

    def test_other_company_analytic_account_rejected(self):
        other_account = self.analytic_account.copy(
            {"name": "Other Company Branch", "company_id": self.other_company.id},
        )
        batch = self._create_batch()
        with self.assertRaises(UserError), self.cr.savepoint():
            batch.analytic_account_id = other_account

    def test_shared_analytic_account_allowed(self):
        self.analytic_account.company_id = False
        batch = self._create_batch()
        batch.action_validate()
        self.assertEqual(batch.state, "done")

    def test_stock_user_can_select_account_and_validate(self):
        batch = self._create_batch(analytic_account_id=False).with_user(
            self.inventory_user,
        )
        batch.analytic_account_id = self.analytic_account
        batch.action_validate()
        self.assertEqual(batch.state, "done")
        self.assertTrue(batch.sudo().scrap_ids.move_ids.account_move_id)

    def test_failure_rolls_back_journal_and_analytic_entries(self):
        batch = self._create_batch()
        batch.line_ids[1].quantity = 1000
        entries_before = self.env["account.move"].search([])
        analytic_before = self.env["account.analytic.line"].search([])

        with self.assertRaises(UserError):
            batch.action_validate()

        self.assertEqual(batch.state, "draft")
        self.assertFalse(batch.scrap_ids)
        self.assertEqual(self.env["account.move"].search([]), entries_before)
        self.assertEqual(self.env["account.analytic.line"].search([]), analytic_before)
