from odoo import Command
from odoo.exceptions import AccessError
from unittest.mock import patch
from odoo.tests import tagged
from odoo.tests.common import new_test_user
from odoo.addons.web.tests.test_js import HOOTCommon, unit_test_error_checker

from .test_order_accounting import BlendAccountingCommon


@tagged('post_install', '-at_install')
class TestBlendKitchenPrint(BlendAccountingCommon):
    def _legacy_receipt(self, receipt):
        keys = ('quantity', 'basic_name', 'attribute_value_names', 'note', 'customer_note')
        return {
            key: value for key, value in receipt.items()
            if key not in ('blend_kitchen', 'tracking_number')
        } | {'changes': {'title': 'Blend', 'data': [
            {key: line[key] for key in keys} for line in receipt['changes']['data']
        ]}}

    def test_receipt_keeps_product_identity_and_kitchen_metadata(self):
        order = self._create()
        receipt = order.blend_kitchen_print_ids.receipt
        self.assertEqual(receipt['tracking_number'], order.tracking_number)
        self.assertEqual(receipt['blend_kitchen']['version'], 2)
        self.assertEqual(receipt['blend_kitchen']['reference'], order.blend_partner_reference)
        for line in receipt['changes']['data']:
            source = order.lines.filtered(lambda item: item.uuid == line['uuid'])
            self.assertEqual(line['product_id'], source.product_id.id)
            self.assertEqual(line['quantity'], source.qty)
            self.assertTrue(line['product_name_ar'])

    def test_legacy_waiting_receipt_is_enriched_at_claim_without_extra_job(self):
        order = self._create()
        job = order.blend_kitchen_print_ids
        legacy = self._legacy_receipt(job.receipt)
        job.sudo().receipt = legacy
        claim = self.env['blend.kitchen.print'].claim_next(self.config.id, self.pos_session.id, self.printer.ids)
        self.assertEqual(claim['id'], job.id)
        self.assertEqual(claim['receipt']['blend_kitchen']['version'], 2)
        self.assertEqual(self._legacy_receipt(claim['receipt']), legacy)
        self.assertEqual(len(order.blend_kitchen_print_ids), 1)

    def test_changed_legacy_snapshot_is_not_silently_replaced_or_claimed(self):
        order = self._create()
        job = order.blend_kitchen_print_ids
        legacy = self._legacy_receipt(job.receipt)
        legacy['changes']['data'][0]['quantity'] += 1
        job.sudo().receipt = legacy
        self.assertFalse(self.env['blend.kitchen.print'].claim_next(self.config.id, self.pos_session.id, self.printer.ids))
        self.assertEqual(job.state, 'failed')
        self.assertTrue(job.error)
        self.assertEqual(job.receipt, legacy)

    def test_modifier_identity_is_retained_for_shared_kitchen_style(self):
        self.modifier.pos_categ_ids = [Command.clear()]
        order = self._create()
        receipt = order.blend_kitchen_print_ids.receipt
        child = next(line for line in receipt['changes']['data'] if line['isModifierChild'])
        parent = next(line for line in receipt['changes']['data'] if line['uuid'] == child['modifier_parent_uuid'])
        self.assertEqual(child['product_id'], self.modifier.product_variant_id.id)
        self.assertEqual(parent['product_id'], self.item.product_variant_id.id)

    def setUp(self):
        super().setUp()
        self.config.blend_auto_accept = True
        self.printer = self.env['pos.printer'].create({
            'name': 'Blend kitchen test', 'company_id': self.config.company_id.id,
            'product_categories_ids': [Command.set(self.item.pos_categ_ids.ids)],
        })
        self.config.printer_ids = [Command.set(self.printer.ids)]

    def test_acceptance_queues_once_and_claim_is_exclusive(self):
        order = self._create()
        jobs = order.blend_kitchen_print_ids
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs.state, 'pending')
        order._blend_send_to_preparation()
        self.assertEqual(order.blend_kitchen_print_ids, jobs)
        claimed = self.env['blend.kitchen.print'].claim_next(self.config.id, self.pos_session.id, self.printer.ids)
        self.assertEqual(claimed['id'], jobs.id)
        self.assertFalse(self.env['blend.kitchen.print'].claim_next(self.config.id, self.pos_session.id, self.printer.ids))
        jobs.finish(claimed['token'], True, '')
        jobs.finish(claimed['token'], True, '')
        self.assertEqual(jobs.state, 'done')
        self.assertTrue(claimed['receipt']['changes']['data'])

    def test_failure_needs_explicit_retry_and_old_token_cannot_ack(self):
        order = self._create()
        jobs = order.blend_kitchen_print_ids
        claim = self.env['blend.kitchen.print'].claim_next(self.config.id, self.pos_session.id, self.printer.ids)
        jobs.finish(claim['token'], False, 'Printer offline')
        self.assertEqual(jobs.state, 'failed')
        self.assertFalse(self.env['blend.kitchen.print'].claim_next(self.config.id, self.pos_session.id, self.printer.ids))
        jobs.action_retry()
        with self.assertRaises(AccessError):
            jobs.finish(claim['token'], True, '')

    def test_failed_accounting_and_cashier_do_not_queue(self):
        self.mapping.active = False
        order = self._create()
        self.assertFalse(order.blend_kitchen_print_ids)
        order.sudo().write({'blend_partner_reference': False})
        order._blend_send_to_preparation()
        self.assertFalse(order.blend_kitchen_print_ids)

    def test_pos_user_can_claim_but_cannot_forge_job(self):
        order = self._create()
        user = new_test_user(self.env, login='blend-print-operator', groups='base.group_user,point_of_sale.group_pos_user')
        model = self.env['blend.kitchen.print'].with_user(user)
        with self.assertRaises(AccessError):
            model.create({'order_id': order.id, 'printer_id': self.printer.id})
        claim = model.claim_next(self.config.id, self.pos_session.id, self.printer.ids)
        self.assertTrue(claim)
        model.browse(claim['id']).finish(claim['token'], True, '')
        self.assertEqual(order.blend_kitchen_print_ids.state, 'done')

    def test_routes_food_and_bar_separately_including_child_categories(self):
        food = self.env['pos.category'].create({'name': 'Food'})
        child = self.env['pos.category'].create({'name': 'Pizza', 'parent_id': food.id})
        bar = self.env['pos.category'].create({'name': 'Bar'})
        self.item.pos_categ_ids = child
        self.modifier.pos_categ_ids = bar
        self.config.iface_available_categ_ids = food | child | bar
        self.printer.product_categories_ids = food
        bar_printer = self.printer.copy({'name': 'Bar', 'product_categories_ids': [Command.set(bar.ids)]})
        self.config.printer_ids |= bar_printer
        order = self._create()
        self.assertEqual(len(order.blend_kitchen_print_ids), 2)
        kitchen_job = order.blend_kitchen_print_ids.filtered(lambda j: j.printer_id == self.printer)
        bar_job = order.blend_kitchen_print_ids - kitchen_job
        self.assertEqual([x['basic_name'] for x in kitchen_job.receipt['changes']['data']], [self.item.name])
        self.assertEqual([x['basic_name'] for x in bar_job.receipt['changes']['data']], [self.modifier.name])
        self.assertNotIn('blend_kitchen_print_ids', order._load_pos_data_fields(self.config))

    def test_wrong_session_unconfigured_printer_and_portal_cannot_claim(self):
        order = self._create()
        model = self.env['blend.kitchen.print']
        self.assertFalse(model.claim_next(self.config.id, self.pos_session.id, []))
        with self.assertRaises(AccessError):
            model.claim_next(self.config.id, 0, self.printer.ids)
        portal = new_test_user(self.env, login='blend-print-portal', groups='base.group_portal')
        with self.assertRaises(AccessError):
            model.with_user(portal).claim_next(self.config.id, self.pos_session.id, self.printer.ids)
        self.assertEqual(order.blend_kitchen_print_ids.state, 'pending')

    def test_other_company_cannot_claim_or_acknowledge(self):
        order = self._create()
        other = self.env['res.company'].create({'name': 'Unrelated print company'})
        user = new_test_user(self.env, login='blend-print-other', groups='base.group_user,point_of_sale.group_pos_user',
                             company_id=other.id, company_ids=[Command.set(other.ids)])
        scoped = self.env['blend.kitchen.print'].with_user(user).with_context(allowed_company_ids=other.ids)
        with self.assertRaises(AccessError):
            scoped.claim_next(self.config.id, self.pos_session.id, self.printer.ids)
        with self.assertRaises(AccessError):
            scoped.browse(order.blend_kitchen_print_ids.id).finish('forged-token', True)

    def test_queue_failure_rolls_back_payment_and_invoice(self):
        with patch.object(type(self.env['blend.kitchen.print']), 'create', side_effect=RuntimeError('Queue unavailable')):
            order = self._create()
        self.assertEqual(order.blend_financial_state, 'failed')
        self.assertFalse(order.payment_ids)
        self.assertFalse(order.account_move)
        self.assertFalse(order.blend_kitchen_print_ids)

    def test_unmatched_printer_categories_are_reported_without_printing_elsewhere(self):
        self.printer.product_categories_ids = False
        order = self._create()
        self.assertEqual(order.blend_financial_state, 'done')
        self.assertFalse(order.blend_kitchen_print_ids)
        self.assertIn(self.item.name, order.blend_kitchen_print_warning)

    def test_cancelled_and_closed_session_jobs_are_not_printed(self):
        order = self._create()
        order.sudo().with_context(blend_skip_status_sync=True).write({'blend_refund_required': True})
        model = self.env['blend.kitchen.print']
        self.assertFalse(model.claim_next(self.config.id, self.pos_session.id, self.printer.ids))
        order.sudo().blend_refund_required = False
        self.pos_session.state = 'closing_control'
        self.assertFalse(model.claim_next(self.config.id, self.pos_session.id, self.printer.ids))


@tagged('post_install', '-at_install')
class TestBlendKitchenPrintBrowser(HOOTCommon):
    def test_print_worker(self):
        suite = self._generate_hash('@bs_pos_blend_intgeration')
        self.browser_js(
            '/web/tests?headless&loglevel=2&preset=desktop&timeout=15000&id=' + suite,
            '', '', login='admin', timeout=180,
            success_signal='[HOOT] Test suite succeeded', error_checker=unit_test_error_checker,
        )
