"""Cashier synchronization must not write server-managed Blend accounting."""
import copy
import uuid
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged
from odoo.tests.common import new_test_user

from .test_order_accounting import BlendAccountingCommon


@tagged("post_install", "-at_install", "blend_accounting")
class TestBlendFinancialSync(BlendAccountingCommon):
    def setUp(self):
        super().setUp()
        self.currency = self.pos_session.currency_id
        self.pricelist = self.config.pricelist_id
        self.operator = new_test_user(
            self.env, login="blend-sync-" + uuid.uuid4().hex,
            groups="base.group_user,point_of_sale.group_pos_user",
        )

    def _ui(self, order=None, paid=False):
        data = {
            "uuid": order.uuid if order else str(uuid.uuid4()),
            "state": "paid" if paid else "draft", "session_id": self.pos_session.id,
            "user_id": self.operator.id, "name": "Cashier sync regression",
            "date_order": fields.Datetime.to_string(fields.Datetime.now()),
            "amount_total": 50, "amount_tax": 0, "amount_paid": 50 if paid else 0,
            "amount_return": 0, "last_order_preparation_change": "{}",
            "lines": [Command.create({"product_id": self.item.product_variant_id.id,
                                      "qty": 1, "price_unit": 50, "discount": 0,
                                      "price_subtotal": 50, "price_subtotal_incl": 50,
                                      "tax_ids": [Command.clear()]})],
            "payment_ids": [Command.create({"amount": 50,
                                           "payment_method_id": self.bank_payment_method.id})] if paid else [],
        }
        if order:
            data.update(id=order.id, access_token=order.access_token)
        return data

    def _snapshot(self, order):
        return (
            order.read(["state", "amount_total", "amount_paid", "blend_financial_state", "write_date"]),
            order.lines.read(["product_id", "qty", "price_unit", "write_date"]),
            order.payment_ids.ids, order.account_move.ids,
        )

    def test_mixed_sync_pays_cashier_and_refreshes_failed_blend(self):
        incoming = self._create()
        incoming.sudo().write({"blend_financial_state": "failed", "blend_financial_error": "Review accounting"})
        before = self._snapshot(incoming)
        stale = self._ui(incoming)
        # Old cached clients resend server-only fields too. Trust the DB identity.
        stale.update(blend_financial_required=False, blend_financial_error=False)
        model = self.env["pos.order"].with_user(self.operator)
        created = model.sync_from_ui([self._ui()])
        ordinary = self.env["pos.order"].browse(created["pos.order"][0]["id"])
        cashier = self._ui(ordinary, paid=True)
        cashier["lines"] = [Command.update(ordinary.lines.id, {"qty": 1, "price_unit": 50})]
        data = model.sync_from_ui([stale, cashier])
        self.assertEqual(ordinary.state, "paid")
        self.assertEqual(ordinary.amount_total, 50)
        self.assertEqual(len(ordinary.payment_ids), 1)
        self.assertFalse(ordinary.blend_financial_required)
        self.assertFalse(ordinary.blend_partner_reference)
        self.assertEqual(self._snapshot(incoming), before)
        self.assertEqual({row["id"] for row in data["pos.order"]}, {incoming.id, ordinary.id})
        self.assertEqual(next(row for row in data["pos.order"] if row["id"] == incoming.id)["amount_total"], incoming.amount_total)

    def test_financial_snapshot_does_not_apply_preparation_context(self):
        incoming = self._create()
        before = self._snapshot(incoming)
        stale = self._ui(incoming)
        stale["lines"] = [Command.clear()]
        original_payload = copy.deepcopy(stale)
        with patch.object(type(self.env["pos.prep.order"]), "process_order") as process:
            data = self.env["pos.order"].with_user(self.operator).with_context(
                preparation={"process_order_options": {"cancelled": True}},
            ).sync_from_ui([stale])
            process.assert_not_called()
        self.assertEqual(stale, original_payload)
        self.assertEqual(self._snapshot(incoming), before)
        self.assertEqual(data["pos.order"][0]["id"], incoming.id)

    def test_mixed_preparation_context_applies_only_to_cashier(self):
        incoming = self._create()
        cashier = self._ui()
        options = {"internal_note": "Bar"}
        with patch.object(type(self.env["pos.prep.order"]), "process_order") as process:
            data = self.env["pos.order"].with_user(self.operator).with_context(
                preparation={"process_order_options": options},
            ).sync_from_ui([self._ui(incoming), cashier])
            ordinary_id = next(row["id"] for row in data["pos.order"] if row["uuid"] == cashier["uuid"])
            process.assert_called_once_with(ordinary_id, options)

    def test_completed_financial_order_refresh_does_not_duplicate_money_or_prep(self):
        incoming = self._create()
        incoming.action_blend_accept_order()
        before = self._snapshot(incoming)
        with patch.object(type(self.env["pos.prep.order"]), "process_order") as process:
            data = self.env["pos.order"].with_user(self.operator).with_context(
                preparation={"process_order_options": {}},
            ).sync_from_ui([self._ui(incoming)])
            process.assert_not_called()
        self.assertEqual(self._snapshot(incoming), before)
        self.assertEqual(data["pos.order"][0]["state"], "done")

    def test_direct_financial_payment_is_rejected_with_actionable_message(self):
        incoming = self._create()
        before = self._snapshot(incoming)
        with self.assertRaisesRegex(UserError, "acceptance action"):
            self.env["pos.order"].with_user(self.operator).sync_from_ui([self._ui(incoming, paid=True)])
        self.assertEqual(self._snapshot(incoming), before)

    def test_forged_enrollment_and_direct_writes_remain_forbidden(self):
        incoming = self._create()
        data = self._ui()
        data["blend_financial_required"] = True
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            self.env["pos.order"].with_user(self.operator).sync_from_ui([data])
        with self.assertRaises(AccessError):
            incoming.with_user(self.operator).write({"amount_total": 1})
        with self.assertRaises(AccessError):
            incoming.lines.with_user(self.operator).write({"qty": 10})

    def test_foreign_company_financial_order_is_not_returned(self):
        incoming = self._create()
        other = self.env["res.company"].create({"name": "Blend sync other company"})
        other_operator = new_test_user(
            self.env, login="blend-sync-other-" + uuid.uuid4().hex,
            groups="base.group_user,point_of_sale.group_pos_user",
            company_id=other.id, company_ids=[Command.set(other.ids)],
        )
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            self.env["pos.order"].with_user(other_operator).with_context(
                allowed_company_ids=other.ids,
            ).sync_from_ui([self._ui(incoming)])

    def test_refund_and_ordinary_update_still_use_native_sync(self):
        incoming = self._create()
        incoming.action_blend_accept_order()
        refund = incoming.with_user(self.operator)._refund()
        self.assertFalse(refund.blend_financial_required)
        cashier = self._ui()
        model = self.env["pos.order"].with_user(self.operator)
        data = model.sync_from_ui([cashier])
        ordinary = self.env["pos.order"].browse(data["pos.order"][0]["id"])
        update = self._ui(ordinary)
        update["lines"] = [Command.update(ordinary.lines.id, {"qty": 1, "price_unit": 50})]
        update["general_customer_note"] = "Cashier note still syncs"
        model.sync_from_ui([update])
        self.assertEqual(ordinary.general_customer_note, "Cashier note still syncs")
