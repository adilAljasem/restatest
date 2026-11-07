from types import SimpleNamespace
from unittest import TestCase

from odoo.exceptions import UserError

from ..models.stock_request import StockRequest


class Records(list):
    def filtered(self, predicate):
        return Records(item for item in self if predicate(item))

    def mapped(self, name):
        return Records(getattr(item, name) for item in self)

    def lock_for_update(self, **kwargs):
        pass

    def invalidate_recordset(self, fields):
        pass


class TestInTransitEditGuards(TestCase):
    def line(self, moves=(), received=0, closed=0):
        return SimpleNamespace(
            order_id=SimpleNamespace(transfer_flow="two_step_no_transit", state="in_transit"),
            operation_move_ids=Records(SimpleNamespace(state=state) for state in moves),
            received_qty=received, closed_receipt_qty=closed, cancelled_receipt_qty=closed,
            product_id=SimpleNamespace(id=1), product_uom_id=SimpleNamespace(id=1, rounding=0.01),
        )

    def check(self, line, vals=None, deleting=False):
        StockRequest._check_in_transit_item_change(Records([line]), vals, deleting)

    def test_unreceived_items_can_change_or_be_deleted(self):
        self.check(self.line(), {"product_id": 2, "product_uom_qty": 8})
        self.check(self.line(), deleting=True)

    def test_recorded_receipt_preserves_identity_and_minimum_quantity(self):
        line = self.line(["done"], received=4)
        for vals in ({"product_id": 2}, {"product_uom_id": 2}, {"product_uom_qty": 3}):
            with self.assertRaises(UserError):
                self.check(line, vals)
        with self.assertRaises(UserError):
            self.check(line, deleting=True)
        self.check(line, {"product_uom_qty": 4})
        self.check(line, {"product_uom_qty": 9})

    def test_pending_receipt_blocks_changes(self):
        with self.assertRaises(UserError):
            self.check(self.line(["assigned"]), {"product_uom_qty": 8})
        with self.assertRaises(UserError):
            self.check(self.line(["draft"]), deleting=True)

    def test_closed_receipt_is_preserved(self):
        with self.assertRaises(UserError):
            self.check(self.line(closed=3), deleting=True)
        with self.assertRaises(UserError):
            self.check(self.line(received=2, closed=3), {"product_uom_qty": 4})
        self.check(self.line(received=2, closed=3), {"product_uom_qty": 5})

    def test_other_flows_are_not_changed(self):
        line = self.line(["assigned"])
        line.order_id.transfer_flow = "two_step"
        self.check(line, deleting=True)

