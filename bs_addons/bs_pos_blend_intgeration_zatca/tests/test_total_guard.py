import uuid
from unittest.mock import patch

from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged
from odoo.tests.common import new_test_user

from odoo.addons.account_edi.models.account_edi_document import AccountEdiDocument
from odoo.addons.bs_pos_blend_intgeration.tests.test_order_accounting import (
    BlendAccountingCommon,
)
from odoo.addons.l10n_sa_edi.models.account_edi_format import AccountEdiFormat


@tagged("post_install", "-at_install", "blend_total_match")
class TestBlendZatcaTotalGuard(BlendAccountingCommon):
    def setUp(self):
        super().setUp()
        self.order = self._create()
        self.invoice = self.env["account.move"].create({
            "move_type": "out_invoice", "partner_id": self.partner_mobt.id,
            "journal_id": self.company_data["default_journal_sale"].id,
            "invoice_line_ids": [Command.create({
                "product_id": self.item.product_variant_id.id,
                "quantity": 1, "price_unit": 62, "tax_ids": [Command.clear()],
            })],
        })
        self.order.account_move = self.invoice
        self.edi_format = self.env.ref("l10n_sa_edi.edi_sa_zatca")

    def test_mismatch_blocks_before_native_chain_or_network_then_recovers(self):
        self.order.amount_total = 63
        with patch.object(AccountEdiFormat, "_l10n_sa_post_zatca_edi", return_value={}) as native:
            result = self.edi_format._l10n_sa_post_zatca_edi(self.invoice)
            native.assert_not_called()
            self.assertTrue(result[self.invoice]["error"])
            self.assertEqual(result[self.invoice]["blocking_level"], "warning")
            self.assertFalse(self.invoice.l10n_sa_chain_index)
            self.order.amount_total = 62
            self.assertEqual(self.edi_format._l10n_sa_post_zatca_edi(self.invoice), {})
            native.assert_called_once_with(self.invoice)

    def test_missing_payload_blocks(self):
        self.order.blend_payload = {}
        with patch.object(AccountEdiFormat, "_l10n_sa_post_zatca_edi") as native:
            self.assertTrue(self.edi_format._l10n_sa_post_zatca_edi(self.invoice)[self.invoice]["error"])
            native.assert_not_called()

    def test_non_blend_invoice_keeps_native_submission(self):
        self.order.write({"blend_partner_reference": False, "amount_total": 63})
        with patch.object(AccountEdiFormat, "_l10n_sa_post_zatca_edi", return_value={}) as native:
            self.assertEqual(self.edi_format._l10n_sa_post_zatca_edi(self.invoice), {})
            native.assert_called_once_with(self.invoice)

    def test_edi_retry_rechecks_without_sending_mismatched_order(self):
        document = self.env["account.edi.document"].create({
            "move_id": self.invoice.id, "edi_format_id": self.edi_format.id, "state": "to_send",
        })
        job = {"documents": document, "method_to_call": self.edi_format._l10n_sa_post_zatca_edi}
        self.order.amount_total = 63
        with patch.object(AccountEdiFormat, "_l10n_sa_post_zatca_edi",
                          return_value={self.invoice: {"success": True}}) as native:
            document._process_job(job)
            native.assert_not_called()
            self.assertEqual(document.state, "to_send")
            self.assertEqual(document.blocking_level, "warning")
            self.order.amount_total = 62
            document._process_job(job)
            native.assert_called_once()
            self.assertEqual(document.state, "sent")
            self.assertFalse(document.error)

    def test_admin_can_authorize_one_mismatch_and_changed_totals_block_again(self):
        self.invoice.action_post()
        document = self.invoice.edi_document_ids.filtered(lambda doc: doc.edi_format_id == self.edi_format)
        if not document:
            document = self.env["account.edi.document"].create({
                "move_id": self.invoice.id, "edi_format_id": self.edi_format.id, "state": "to_send",
            })
        self.order.amount_total = 63
        with patch.object(AccountEdiDocument, "_process_documents_web_services", return_value=0) as process:
            self.order.action_blend_send_zatca_with_total_override()
            process.assert_called_once_with(with_commit=False)
        self.assertEqual(self.order.blend_zatca_override_by, self.env.user)
        self.assertTrue(self.order.blend_zatca_override_at)
        self.assertEqual(self.order.blend_zatca_override_odoo_total, 63)
        self.assertEqual(self.order.blend_zatca_override_blend_total, 62)
        self.assertEqual(self.order.blend_zatca_override_invoice_id, self.invoice)
        self.assertFalse(self.order.blend_total_matches)
        with patch.object(AccountEdiFormat, "_l10n_sa_post_zatca_edi", return_value={}) as native:
            self.assertEqual(self.edi_format._l10n_sa_post_zatca_edi(self.invoice), {})
            native.assert_called_once_with(self.invoice)
            self.order.amount_total = 64
            self.assertTrue(self.edi_format._l10n_sa_post_zatca_edi(self.invoice)[self.invoice]["error"])
            native.assert_called_once_with(self.invoice)

    def test_non_admin_cannot_authorize_zatca_mismatch_by_rpc(self):
        user = new_test_user(
            self.env, login="blend-zatca-override-" + uuid.uuid4().hex,
            groups="point_of_sale.group_pos_user",
        )
        with self.assertRaises(AccessError):
            self.order.with_user(user).action_blend_send_zatca_with_total_override()

    def test_override_audit_fields_cannot_be_written_directly(self):
        user = new_test_user(
            self.env, login="blend-zatca-direct-" + uuid.uuid4().hex,
            groups="base.group_system,point_of_sale.group_pos_user",
        )
        with self.assertRaises(AccessError):
            self.order.with_user(user).write({"blend_zatca_override_by": user.id})

    def test_admin_override_requires_valid_blend_total_and_posted_invoice(self):
        self.order.amount_total = 63
        with self.assertRaises(UserError):
            self.order.action_blend_send_zatca_with_total_override()
        self.invoice.action_post()
        self.order.blend_payload = {}
        with self.assertRaises(UserError):
            self.order.action_blend_send_zatca_with_total_override()
