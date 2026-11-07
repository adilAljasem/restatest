"""Status contract regressions; these tests do not require a database."""

from types import SimpleNamespace
from unittest.mock import Mock

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged

from odoo.addons.bs_pos_blend_intgeration.models.pos_order import BLEND_STATUSES, PosOrder
from odoo.addons.bs_pos_blend_intgeration.services.exceptions import BlendAPIError
from odoo.addons.bs_pos_blend_intgeration.services.pos_api import BlendPOSAPIService


@tagged("post_install", "-at_install")
class TestBlendInboundStatus(TransactionCase):
    def setUp(self):
        super().setUp()
        # Status validation uses no database records.
        self.service = object.__new__(BlendPOSAPIService)

    def test_assigned_payload_keeps_its_remote_status(self):
        payload = {"status": "ASSIGNED"}
        self.assertEqual(self.service._validate_status(payload["status"]), ("ASSIGNED", "assigned"))
        self.assertEqual(payload, {"status": "ASSIGNED"})
        self.assertIn("assigned", dict(BLEND_STATUSES))
        self.assertNotIn("ASSIGNED", self.service.TERMINAL_ORDER_STATUSES)

    def test_existing_statuses_keep_their_mapping(self):
        for status, internal in (
            ("PENDING", "pending"), ("ACCEPTED", "accepted"), ("PREPARING", "preparing"),
            ("READY_TO_PICK_UP", "ready_to_pick_up"), ("COMPLETED", "completed"),
            ("AT_PICK_UP", "at_pick_up"), ("ON_THE_WAY", "on_the_way"),
            ("CANCELED", "cancelled"), ("REJECTED", "rejected"), ("TIME_OUT", "time_out"),
        ):
            with self.subTest(status=status):
                try:
                    result = self.service._validate_status(status)
                except BlendAPIError as error:
                    self.fail("%s was rejected: %s" % (status, error.message))
                self.assertEqual(result, (status, internal))

    def test_scheduled_status_is_supported_but_not_terminal(self):
        self.assertEqual(self.service._validate_status("SCHEDULED"), ("SCHEDULED", "scheduled"))
        self.assertIn("scheduled", dict(BLEND_STATUSES))
        self.assertNotIn("SCHEDULED", self.service.TERMINAL_ORDER_STATUSES)

    def test_unknown_statuses_still_fail(self):
        for status in ("UNKNOWN", "DELIVERED", "ASSIGN", "", None):
            with self.subTest(status=status), self.assertRaises(BlendAPIError) as error:
                self.service._validate_status(status)
            self.assertEqual(error.exception.status_code, 400)
            self.assertIn("ASSIGNED", error.exception.message)

    def test_assigned_without_financial_finalization_does_not_auto_accept(self):
        # Only guard inputs are provided; any workflow side effect fails.
        order = SimpleNamespace(
            config_id=SimpleNamespace(blend_enabled=True, blend_auto_accept=True),
            blend_partner_reference="ninja: 201688861", state="draft",
            blend_status="assigned", blend_financial_required=False,
        )
        self.assertTrue(PosOrder._blend_auto_accept([order]))
        self.assertEqual(order.blend_status, "assigned")
        self.assertTrue(PosOrder._blend_acceptance_obsolete(
            SimpleNamespace(ensure_one=lambda: None, state="draft", blend_status="assigned")
        ))

    def test_outgoing_status_contracts_are_unchanged(self):
        order = SimpleNamespace(
            ensure_one=Mock(), uuid="order-test", pos_reference=False, id=1,
            config_id=SimpleNamespace(
                blend_outbound_webhook_url=False, blend_schema="pos",
                blend_brand_id="brand", blend_branch_id="branch",
            ),
        )
        for status, remote in (
            ("pending", "PENDING"), ("accepted", "ACCEPTED"), ("preparing", "PREPARING"),
            ("cancelled", "CANCELED"), ("rejected", "REJECTED"), ("time_out", "TIME_OUT"),
        ):
            with self.subTest(status=status):
                self.assertEqual(PosOrder._blend_status_payload(order, status)["status"], remote)
        with self.assertRaises(ValidationError):
            PosOrder._blend_status_payload(order, "assigned")
        order.config_id.blend_schema = "primary"
        self.assertEqual(PosOrder._blend_status_payload(order, "assigned")["payload"]["status"], "assigned")
