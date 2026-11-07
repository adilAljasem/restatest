from odoo.addons.pos_enterprise.tests.test_frontend import TestPreparationDisplayHttpCommon
from odoo.addons.pos_restaurant.tests.test_frontend import TestFrontendCommon
from odoo.tests import tagged


@tagged("post_install", "-at_install")
class TestKitchenFlowPriority(TestFrontendCommon, TestPreparationDisplayHttpCommon):
    def _run_priority_tour(self, priority):
        self.pdis.write({"pos_config_ids": [(4, self.pos_config.id)]})
        self.pos_config.write({"kitchen_flow_priority": priority})
        self.pos_config.printer_ids.unlink()
        self.pos_config.with_user(self.pos_user).open_ui()

        self.start_pos_tour("PreparationDisplayTourResto")

        logs = self.env["bs.pos.kitchen.flow.log"].search(
            [("config_id", "=", self.pos_config.id)]
        )
        self.assertTrue(logs, "The kitchen delivery tour must create diagnostic traces")
        self.assertEqual(set(logs.mapped("priority")), {priority})
        expected_step = (
            "display_sync_first" if priority == "display_first" else "display_process_parallel"
        )
        self.assertTrue(
            any(expected_step in log.step_ids.mapped("code") for log in logs),
            "The trace must prove that the selected preparation-display flow ran",
        )
        self.assertFalse(
            logs.filtered(lambda log: log.status == "failed"),
            "No kitchen delivery trace may fail",
        )

        first_order = self.env["pos.order"].search(
            [("pos_reference", "ilike", "%-000001")],
            limit=1,
        )
        prep_order = self.env["pos.prep.order"].search(
            [("pos_order_id", "=", first_order.id)],
            limit=1,
        )
        self.assertEqual(
            len(prep_order.prep_line_ids),
            2,
            "One click must send both order lines to the preparation display",
        )

    def test_display_first_flow(self):
        self._run_priority_tour("display_first")

    def test_parallel_flow(self):
        self._run_priority_tour("parallel")
