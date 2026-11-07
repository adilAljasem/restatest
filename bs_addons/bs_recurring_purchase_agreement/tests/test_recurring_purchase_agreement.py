from datetime import date

from freezegun import freeze_time
from lxml import etree

from odoo import Command
from odoo.exceptions import ValidationError
from odoo.addons.base.tests.common import BaseCommon

from ..models.res_config_settings import RECURRING_PURCHASE_ORDER_PARAMETER


class TestRecurringPurchaseAgreement(BaseCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.vendor = cls.env["res.partner"].create({"name": "Recurring Vendor"})
        cls.product = cls.env["product.product"].create(
            {
                "name": "Recurring Material",
                "type": "consu",
                "purchase_ok": True,
            }
        )

    def setUp(self):
        super().setUp()
        self.env["ir.config_parameter"].sudo().set_param(
            RECURRING_PURCHASE_ORDER_PARAMETER,
            False,
        )
        self.env["ir.config_parameter"].sudo().set_param(
            "stock_request.allow_warehouse_in_purchase_order_line",
            False,
        )
        self.env.ref(
            "bs_recurring_purchase_agreement.ir_cron_recurring_purchase_orders"
        ).sudo().active = False

    def _set_feature(self, enabled):
        settings = self.env["res.config.settings"].create(
            {"bs_recurring_purchase_order_enabled": enabled}
        )
        settings.set_values()

    def _create_agreement(self, plan, date_start, date_end):
        agreement = self.env["purchase.requisition"].create(
            {
                "vendor_id": self.vendor.id,
                "requisition_type": "blanket_order",
                "date_start": date_start,
                "date_end": date_end,
                "bs_recurring_purchase_order": True,
                "bs_recurring_plan": plan,
                "line_ids": [
                    Command.create(
                        {
                            "product_id": self.product.id,
                            "product_qty": 100.0,
                            "price_unit": 12.5,
                            "bs_recurring_qty": 10.0,
                        }
                    )
                ],
            }
        )
        agreement.action_confirm()
        return agreement

    def test_setting_controls_feature_and_cron(self):
        cron = self.env.ref(
            "bs_recurring_purchase_agreement.ir_cron_recurring_purchase_orders"
        ).sudo()
        self.assertFalse(cron.active)

        self._set_feature(True)
        self.assertEqual(
            self.env["ir.config_parameter"].sudo().get_param(
                RECURRING_PURCHASE_ORDER_PARAMETER
            ),
            "True",
        )
        self.assertTrue(cron.active)

        self._set_feature(False)
        self.assertFalse(cron.active)

    def test_recurring_fields_and_visibility(self):
        self._set_feature(True)
        agreement = self._create_agreement(
            "weekly",
            date(2026, 9, 1),
            date(2026, 9, 30),
        )
        self.assertTrue(agreement.bs_recurring_purchase_order_feature_enabled)
        self.assertEqual(agreement.bs_next_recurring_date, date(2026, 9, 1))
        self.assertEqual(agreement.line_ids.bs_recurring_qty, 10.0)

        arch = self.env["purchase.requisition"].get_view(
            self.env.ref(
                "purchase_requisition.view_purchase_requisition_form"
            ).id,
            "form",
        )["arch"]
        root = etree.fromstring(arch)
        recurring_field = root.xpath(
            "//field[@name='bs_recurring_purchase_order']"
        )[0]
        plan_field = root.xpath("//field[@name='bs_recurring_plan']")[0]
        recurring_qty = root.xpath(
            "//field[@name='line_ids']/list/field[@name='bs_recurring_qty']"
        )[0]
        self.assertIn(
            "requisition_type != 'blanket_order'",
            recurring_field.get("invisible"),
        )
        self.assertEqual(plan_field.get("required"), "bs_recurring_purchase_order")
        self.assertIn(
            "not parent.bs_recurring_purchase_order",
            recurring_qty.get("column_invisible"),
        )

    def test_recurring_plan_is_required(self):
        with self.assertRaisesRegex(ValidationError, "Recurring Plan"):
            self.env["purchase.requisition"].create(
                {
                    "vendor_id": self.vendor.id,
                    "requisition_type": "blanket_order",
                    "date_start": date(2026, 9, 1),
                    "date_end": date(2026, 9, 30),
                    "bs_recurring_purchase_order": True,
                }
            )

    @freeze_time("2026-09-03 10:00:00")
    def test_daily_orders_are_created_confirmed_and_not_duplicated(self):
        self._set_feature(True)
        agreement = self._create_agreement(
            "daily",
            date(2026, 9, 1),
            date(2026, 9, 3),
        )

        self.env["purchase.requisition"]._cron_bs_create_recurring_purchase_orders()
        orders = agreement.purchase_ids.sorted("bs_recurring_schedule_date")
        self.assertEqual(len(orders), 3)
        self.assertEqual(
            orders.mapped("bs_recurring_schedule_date"),
            [date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3)],
        )
        self.assertTrue(all(order.state == "purchase" for order in orders))
        self.assertTrue(all(order.partner_id == self.vendor for order in orders))
        self.assertTrue(
            all(order.date_order.date() == date(2026, 9, 3) for order in orders)
        )
        self.assertTrue(
            all(order.order_line.product_qty == 10.0 for order in orders)
        )
        self.assertTrue(all(order.order_line.price_unit == 12.5 for order in orders))
        self.assertEqual(agreement.bs_next_recurring_date, date(2026, 9, 4))

        self.env["purchase.requisition"]._cron_bs_create_recurring_purchase_orders()
        self.assertEqual(len(agreement.purchase_ids), 3)
        self.env["purchase.requisition"]._cron_bs_create_recurring_purchase_orders(
            today=date(2026, 9, 4)
        )
        self.assertEqual(len(agreement.purchase_ids), 3)

    @freeze_time("2026-09-01 10:00:00")
    def test_recurring_order_without_warehouse_uses_deliver_to(self):
        self._set_feature(True)
        self.env["ir.config_parameter"].sudo().set_param(
            "stock_request.allow_warehouse_in_purchase_order_line",
            True,
        )
        agreement = self._create_agreement(
            "daily",
            date(2026, 9, 1),
            date(2026, 9, 1),
        )

        self.env["purchase.requisition"]._cron_bs_create_recurring_purchase_orders()

        order = agreement.purchase_ids
        self.assertEqual(order.state, "purchase")
        self.assertFalse(order.order_line.warehouse_id)
        self.assertEqual(len(order.picking_ids), 1)
        self.assertEqual(order.picking_ids.picking_type_id, order.picking_type_id)
        self.assertEqual(order.order_line.move_ids.picking_id, order.picking_ids)

    def test_weekly_and_monthly_schedule_dates(self):
        self._set_feature(True)
        weekly = self._create_agreement(
            "weekly",
            date(2026, 9, 1),
            date(2026, 9, 30),
        )
        monthly = self._create_agreement(
            "monthly",
            date(2026, 7, 15),
            date(2026, 10, 15),
        )

        self.env["purchase.requisition"]._cron_bs_create_recurring_purchase_orders(
            today=date(2026, 9, 20)
        )
        self.assertEqual(
            weekly.purchase_ids.sorted("bs_recurring_schedule_date").mapped(
                "bs_recurring_schedule_date"
            ),
            [date(2026, 9, 1), date(2026, 9, 8), date(2026, 9, 15)],
        )
        self.assertEqual(
            monthly.purchase_ids.sorted("bs_recurring_schedule_date").mapped(
                "bs_recurring_schedule_date"
            ),
            [date(2026, 7, 15), date(2026, 8, 15), date(2026, 9, 15)],
        )

    def test_disabled_setting_pauses_generation(self):
        self._set_feature(True)
        agreement = self._create_agreement(
            "daily",
            date(2026, 9, 1),
            date(2026, 9, 2),
        )
        self._set_feature(False)

        self.env["purchase.requisition"]._cron_bs_create_recurring_purchase_orders(
            today=date(2026, 9, 2)
        )
        self.assertFalse(agreement.purchase_ids)
