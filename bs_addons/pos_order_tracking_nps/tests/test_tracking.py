from datetime import timedelta
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import ValidationError
from odoo.addons.point_of_sale.tests.common import TestPoSCommon
from odoo.tests import tagged


@tagged('post_install', '-at_install')
class TestPosOrderTracking(TestPoSCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.config = cls.basic_config
        cls.config.write({'tracking_enabled': True, 'tracking_nps_delay': 30})
        cls.display = cls.env['pos.prep.display'].create({
            'name': 'Kitchen',
            'pos_config_ids': [Command.link(cls.config.id)],
        })

    def _new_order(self):
        self.open_new_session()
        order = next(iter(self._create_orders([{'pos_order_lines_ui_args': [(self.product, 2)]}]).values()))
        self.env['pos.prep.order'].process_order(order.id)
        return order

    def _states(self, order):
        return self.env['pos.prep.state'].search([('prep_line_id.prep_order_id.pos_order_id', '=', order.id)])

    def _move(self, states, stage, display=None):
        states.change_state_stage({str(s.id): stage.id for s in states}, (display or self.display).id)

    def test_status_follows_preparation_display(self):
        order = self._new_order()
        self.assertTrue(order.tracking_token)
        self.assertEqual(order.tracking_state, 'preparing')

        self.env['pos.tracking.subscription'].sudo().create({
            'order_id': order.id,
            'endpoint': 'https://push.example.com/abc',
            'keys': '{"p256dh": "x", "auth": "y"}',
        })
        to_cook, ready, done = self.display.stage_ids
        self._move(self._states(order), ready)
        self.assertEqual(order.tracking_state, 'ready')
        self.assertTrue(order.tracking_ready_date)
        pushes = self.env['pos.tracking.push'].search([('subscription_id.order_id', '=', order.id)])
        self.assertEqual(len(pushes), 1, "Exactly one 'ready' push must be queued")

        self._move(self._states(order), done)
        self.assertEqual(order.tracking_state, 'completed')
        self.assertTrue(order.tracking_completed_date)
        self.assertEqual(
            self.env['pos.tracking.push'].search_count([('subscription_id.order_id', '=', order.id)]), 1,
            "Moving from ready to completed must not notify again")

        # Recall back to the first stage (kitchen undo) => preparing again
        self._move(self._states(order), to_cook)
        self.assertEqual(order.tracking_state, 'preparing')
        self.assertFalse(order.tracking_ready_date)

    def test_two_stage_screen_starts_preparing(self):
        """Regression: a 2-stage screen showed new orders as ready right away."""
        self.display.stage_ids[2:].unlink()
        order = self._new_order()
        self.assertEqual(order.tracking_state, 'preparing')
        self._move(self._states(order), self.display.stage_ids[1])
        self.assertEqual(order.tracking_state, 'ready')

    def test_single_stage_screen_never_jumps_to_picked_up(self):
        """Regression: a 1-stage screen showed new orders as picked up right away."""
        self.display.stage_ids[1:].unlink()
        order = self._new_order()
        self.assertEqual(order.tracking_state, 'preparing')
        order.action_tracking_mark_ready()
        self.assertEqual(order.tracking_state, 'ready')

    def test_configured_ready_stage(self):
        to_cook, ready, done = self.display.stage_ids
        self.config.write({'tracking_prep_display_id': self.display.id, 'tracking_ready_stage_id': done.id})
        order = self._new_order()
        self.assertEqual(order.tracking_state, 'preparing')
        self._move(self._states(order), ready)
        self.assertEqual(order.tracking_state, 'preparing', "Before the configured stage: still preparing")
        self._move(self._states(order), done)
        self.assertEqual(order.tracking_state, 'ready', "Last stage is the ready stage: not picked up")

    def test_ready_stage_cannot_be_the_first_stage(self):
        self.config.tracking_prep_display_id = self.display
        with self.assertRaises(ValidationError):
            self.config.tracking_ready_stage_id = self.display.stage_ids[0]

    def test_specific_screen_only(self):
        bar = self.env['pos.prep.display'].create({
            'name': 'Bar',
            'pos_config_ids': [Command.link(self.config.id)],
        })
        self.config.tracking_prep_display_id = self.display
        order = self._new_order()
        bar_states = self._states(order).filtered(lambda s: s.stage_id in bar.stage_ids)
        kitchen_states = self._states(order).filtered(lambda s: s.stage_id in self.display.stage_ids)
        self.assertTrue(bar_states and kitchen_states)
        self._move(bar_states, bar.stage_ids[1], bar)
        self.assertEqual(order.tracking_state, 'preparing', "The other screen must not drive the status")
        self._move(kitchen_states, self.display.stage_ids[1])
        self.assertEqual(order.tracking_state, 'ready')

    def test_all_screens_when_none_selected(self):
        bar = self.env['pos.prep.display'].create({
            'name': 'Bar',
            'pos_config_ids': [Command.link(self.config.id)],
        })
        order = self._new_order()
        kitchen_states = self._states(order).filtered(lambda s: s.stage_id in self.display.stage_ids)
        bar_states = self._states(order).filtered(lambda s: s.stage_id in bar.stage_ids)
        self._move(kitchen_states, self.display.stage_ids[1])
        self.assertEqual(order.tracking_state, 'preparing')
        self._move(bar_states, bar.stage_ids[1], bar)
        self.assertEqual(order.tracking_state, 'ready')

    def test_frontend_sync_cannot_overwrite_status(self):
        order = self._new_order()
        self._move(self._states(order), self.display.stage_ids[1])
        token = order.tracking_token
        vals = {'tracking_state': 'received', 'tracking_ready_date': False, 'nps_score': 10,
                'tracking_token': 'forged-token-forged-token', 'uuid': order.uuid,
                'session_id': order.session_id.id, 'state': 'paid'}
        target = 'odoo.addons.point_of_sale.models.pos_order.PosOrder._process_order'
        with patch(target, autospec=True, return_value=order.id) as base_mock:
            self.env['pos.order']._process_order(vals, order)
        received = base_mock.call_args.args[1]
        for key in ('tracking_state', 'tracking_ready_date', 'nps_score', 'tracking_token'):
            self.assertNotIn(key, received)
        self.assertEqual(order.tracking_state, 'ready')
        self.assertEqual(order.tracking_token, token)
        self.assertEqual(order.nps_score, -1)

    def test_nps_submission(self):
        order = self._new_order()
        self.assertFalse(order._tracking_submit_nps(10), "No survey before the order is ready")
        self._move(self._states(order), self.display.stage_ids[1])
        self.assertFalse(order._tracking_submit_nps(10), "No survey before the delay")
        self.assertFalse(order._tracking_public_data()['nps']['available'])

        order.tracking_ready_date = fields.Datetime.now() - timedelta(minutes=31)
        self.assertTrue(order._tracking_public_data()['nps']['available'])
        self.assertTrue(order._tracking_submit_nps(9, "  Great burger "))
        nps = self.env['pos.order.nps'].search([('order_id', '=', order.id)])
        self.assertEqual(nps.category, 'promoter')
        self.assertEqual(nps.nps_value, 100)
        self.assertEqual(nps.comment, "Great burger")
        self.assertEqual(order.nps_score, 9)
        self.assertFalse(order._tracking_submit_nps(0), "Only one answer per order")
        self.assertTrue(order._tracking_public_data()['nps']['done'])

    def test_nps_reminder_and_push_queue(self):
        order = self._new_order()
        sub = self.env['pos.tracking.subscription'].sudo().create({
            'order_id': order.id,
            'endpoint': 'https://push.example.com/def',
            'keys': '{"p256dh": "x", "auth": "y"}',
        })
        self._move(self._states(order), self.display.stage_ids[1])
        order.tracking_ready_date = fields.Datetime.now() - timedelta(minutes=31)
        self.env['pos.order']._cron_tracking_nps_reminder()
        self.assertTrue(order.tracking_nps_reminder_sent)
        self.assertEqual(self.env['pos.tracking.push'].search_count([('subscription_id', '=', sub.id)]), 2)

        self.env['ir.config_parameter'].sudo().set_param('mail.web_push_vapid_private_key', 'priv')
        self.env['ir.config_parameter'].sudo().set_param('mail.web_push_vapid_public_key', 'pub')
        target = 'odoo.addons.pos_order_tracking_nps.models.pos_tracking_push.send_web_push'
        with patch(target) as push_mock:
            self.env['pos.tracking.push']._process_queue()
        self.assertEqual(push_mock.call_count, 2)
        self.assertFalse(self.env['pos.tracking.push'].search_count([('subscription_id', '=', sub.id)]))

    def test_filter_by_order_type(self):
        dine_in, takeaway = self.env['pos.preset'].create([{'name': 'Dine In'}, {'name': 'Takeaway'}])
        self.config.write({
            'use_presets': True,
            'available_preset_ids': [Command.set([dine_in.id, takeaway.id])],
            'default_preset_id': dine_in.id,
            'tracking_preset_ids': [Command.set([dine_in.id])],
        })
        order = self._new_order()
        order.preset_id = takeaway
        self.assertFalse(order._tracking_is_eligible())
        self._move(self._states(order), self.display.stage_ids[1])
        self.assertEqual(order.tracking_state, 'preparing', "Excluded order types are not updated")

        order.preset_id = dine_in
        self.assertTrue(order._tracking_is_eligible())
        order._tracking_sync()
        self.assertEqual(order.tracking_state, 'ready')

        # Presets turned off on the POS: the order-type filter no longer applies
        self.config.use_presets = False
        order.preset_id = takeaway
        self.assertTrue(order._tracking_is_eligible())

    def test_filter_by_cashier(self):
        other_user = self.env['res.users'].sudo().create({'name': 'Other cashier', 'login': 'other_cashier_trk'})
        order = self._new_order()
        self.config.tracking_user_ids = [Command.set([other_user.id])]
        self.assertFalse(order._tracking_is_eligible())
        self.config.tracking_user_ids = [Command.set([order.user_id.id])]
        self.assertTrue(order._tracking_is_eligible())
        self.config.tracking_user_ids = [Command.clear()]
        self.assertTrue(order._tracking_is_eligible())

    def test_rating_only_without_pager(self):
        self.config.tracking_pager_enabled = False
        order = self._new_order()
        self.env['pos.tracking.subscription'].sudo().create({
            'order_id': order.id, 'endpoint': 'https://push.example.com/x', 'keys': '{"p256dh": "x", "auth": "y"}',
        })
        self._move(self._states(order), self.display.stage_ids[1])
        self.assertEqual(order.tracking_state, 'received', "No live status when the pager is off")
        self.assertFalse(self.env['pos.tracking.push'].search_count([('subscription_id.order_id', '=', order.id)]))
        data = order._tracking_public_data()
        self.assertFalse(data['features']['pager'])
        self.assertFalse(data['nps']['available'])
        # Rating delay is counted from the order date when there is no pager
        order.date_order = fields.Datetime.now() - timedelta(minutes=31)
        self.assertTrue(order._tracking_public_data()['nps']['available'])
        self.assertTrue(order._tracking_submit_nps(6))
        self.assertEqual(self.env['pos.order.nps'].search([('order_id', '=', order.id)]).category, 'detractor')

    def test_pager_only_without_rating(self):
        self.config.tracking_nps_enabled = False
        order = self._new_order()
        self._move(self._states(order), self.display.stage_ids[1])
        self.assertEqual(order.tracking_state, 'ready')
        order.tracking_ready_date = fields.Datetime.now() - timedelta(hours=2)
        data = order._tracking_public_data()
        self.assertFalse(data['features']['nps'])
        self.assertFalse(data['nps']['available'])
        self.assertFalse(order._tracking_submit_nps(10))

    def test_needs_at_least_one_feature(self):
        with self.assertRaises(ValidationError):
            self.config.write({'tracking_pager_enabled': False, 'tracking_nps_enabled': False})

    def test_ready_alert_repeats_until_acknowledged(self):
        self.config.tracking_alert_repeat = 3
        order = self._new_order()
        sub = self.env['pos.tracking.subscription'].sudo().create({
            'order_id': order.id, 'endpoint': 'https://push.example.com/r', 'keys': '{"p256dh": "x", "auth": "y"}',
        })
        Push = self.env['pos.tracking.push'].sudo()
        self._move(self._states(order), self.display.stage_ids[1])
        first = Push.search([('subscription_id', '=', sub.id)])
        self.assertEqual(len(first), 1)
        self.assertEqual((first.urgency, first.ttl), ('high', 900), "Ready alerts must wake a locked phone")
        self.assertEqual(order.tracking_ready_push_count, 1)

        # Too early: nothing new
        self.env['pos.order']._cron_tracking_ready_repeat()
        self.assertEqual(Push.search_count([('subscription_id', '=', sub.id)]), 1)

        # One minute later, not acknowledged: ring again, up to the configured number of times
        for expected in (2, 3, 3):
            order.sudo().tracking_ready_last_push = fields.Datetime.now() - timedelta(seconds=70)
            self.env['pos.order']._cron_tracking_ready_repeat()
            self.assertEqual(order.tracking_ready_push_count, expected)
        self.assertEqual(Push.search_count([('subscription_id', '=', sub.id)]), 3)

    def test_ready_alert_stops_after_ack(self):
        order = self._new_order()
        self.env['pos.tracking.subscription'].sudo().create({
            'order_id': order.id, 'endpoint': 'https://push.example.com/s', 'keys': '{"p256dh": "x", "auth": "y"}',
        })
        self._move(self._states(order), self.display.stage_ids[1])
        order._tracking_ack_ready()
        self.assertTrue(order.tracking_ready_ack)
        order.sudo().tracking_ready_last_push = fields.Datetime.now() - timedelta(seconds=70)
        self.env['pos.order']._cron_tracking_ready_repeat()
        self.assertEqual(order.tracking_ready_push_count, 1)
        # Back to preparing (new items) then ready again: alerts start over
        self._move(self._states(order), self.display.stage_ids[0])
        self.assertFalse(order.tracking_ready_ack)
