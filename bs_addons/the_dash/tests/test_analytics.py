"""The Dash figures reconcile with Odoo's own totals."""
import datetime as dt

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestTheDashAnalytics(TransactionCase):

    def _scope(self):
        configs = self.env['pos.config'].search([])
        return {
            'config_ids': configs.ids,
            'utc_from': dt.datetime(2000, 1, 1),
            'utc_to': dt.datetime(2100, 1, 1),
            'd_from': dt.date(2000, 1, 1),
            'd_to': dt.date(2100, 1, 1),
            'team_ids': [],
        }

    def _paid_orders(self, scope):
        return self.env['pos.order'].search([
            ('config_id', 'in', scope['config_ids']),
            ('state', 'in', ('paid', 'done', 'invoiced')),
        ])

    def test_items_plus_modifiers_equal_order_lines(self):
        A = self.env['the.dash.analytics']
        scope = self._scope()
        sales = A.items_and_modifiers(scope, limit=100000)
        expected = round(sum(self._paid_orders(scope).mapped('lines.price_subtotal_incl')), 2)
        self.assertAlmostEqual(sales['totals']['all'], expected, places=2)
        self.assertAlmostEqual(sales['totals']['items'] + sales['totals']['modifiers'],
                               sales['totals']['all'], places=2)

    def test_collections_equal_payments(self):
        A = self.env['the.dash.analytics']
        scope = self._scope()
        col = A.collections(scope)
        expected = round(sum(self._paid_orders(scope).mapped('payment_ids.amount')), 2)
        self.assertAlmostEqual(col['total'], expected, places=2)
        self.assertAlmostEqual(sum(k['amount'] for k in col['kinds']), col['total'], places=2)

    def test_breakdowns_add_up(self):
        A = self.env['the.dash.analytics']
        scope = self._scope()
        b = A.breakdowns(scope)
        expected = round(sum(self._paid_orders(scope).mapped('amount_total')), 2)
        for dim in ('pos', 'cost_center', 'region', 'cashier'):
            self.assertAlmostEqual(sum(r['sales'] for r in b[dim]), expected, places=2, msg=dim)

    def test_closing_signs(self):
        closing = self.env['the.dash.analytics'].closing(self._scope())
        t = closing['totals']
        self.assertGreaterEqual(t['shortage'], 0)
        self.assertGreaterEqual(t['overage'], 0)
        self.assertAlmostEqual(t['net'], t['overage'] - t['shortage'], places=2)

    def test_report_dimensions_run(self):
        Report = self.env['the.dash.report']
        scope = self._scope()
        for key in Report._dimensions():
            Report.build(scope['config_ids'], scope['utc_from'], scope['utc_to'], key, 'none',
                         local_from=scope['d_from'], local_to=scope['d_to'])

    def test_activity_follows_business_day(self):
        """Hours start at the business-day start hour and add up to the orders."""
        A = self.env['the.dash.analytics']
        scope = dict(self._scope(), tz='Asia/Riyadh', start_hour=6.0)
        act = A.activity(scope)
        self.assertEqual(len(act['hours']), 24)
        self.assertEqual(act['hours'][0]['h'], 6)
        self.assertEqual(act['hours'][-1]['h'], 5)
        expected = round(sum(self._paid_orders(scope).mapped('amount_total')), 2)
        self.assertAlmostEqual(sum(h['sales'] for h in act['hours']), expected, places=2)
        self.assertAlmostEqual(sum(t['sales'] for t in act['types']), expected, places=2)
        pay = A.payments_by_hour(scope)
        paid = round(sum(self._paid_orders(scope).mapped('payment_ids.amount')), 2)
        self.assertAlmostEqual(sum(h['cash'] + h['bank'] + h['app'] + h['credit']
                                   for h in pay['hours']), paid, places=2)

    def test_order_type_from_preset_name(self):
        A = self.env['the.dash.analytics']
        if 'pos.preset' not in self.env:
            self.skipTest('no presets')
        p = self.env['pos.preset'].create({'name': 'سفري'})
        q = self.env['pos.preset'].create({'name': 'صالة'})
        kinds, _names = A._preset_types([p.id, q.id])
        self.assertEqual(kinds[p.id], 'takeaway')
        self.assertEqual(kinds[q.id], 'dine_in')
        self.assertEqual(A._row_type({'is_app': True, 'preset_id': q.id}, kinds), 'app')

    def test_order_type_from_till_name(self):
        """codusic_pos_order_types: the name typed at the till wins."""
        A = self.env['the.dash.analytics']
        self.assertEqual(A._name_type('سفري'), 'takeaway')
        self.assertEqual(A._name_type('محلي'), 'dine_in')
        self.assertEqual(A._name_type('Delivery'), 'delivery')
        self.assertEqual(A._name_type('استلام بالسيارة'), 'takeaway')
        self.assertEqual(A._row_type({'type_name': 'سفري', 'has_table': True}, {}), 'takeaway')
        self.assertEqual(A._row_type({'type_name': 'سفري', 'is_app': True}, {}), 'app')

    def test_recipe_cost(self):
        """Recipe cost: sub-recipe, units of measure, and the no-recipe notes."""
        if 'mrp.bom' not in self.env:
            self.skipTest('mrp not installed')
        kg = self.env.ref('uom.product_uom_kgm')
        gram = self.env.ref('uom.product_uom_gram')
        unit = self.env.ref('uom.product_uom_unit')
        P = self.env['product.product']
        bun = P.create({'name': 'bun', 'type': 'consu', 'standard_price': 1.0})
        mayo = P.create({'name': 'mayo', 'type': 'consu', 'standard_price': 10.0, 'uom_id': kg.id})
        garlic = P.create({'name': 'garlic', 'type': 'consu', 'standard_price': 0.0, 'uom_id': kg.id})
        sauce = P.create({'name': 'sauce', 'type': 'consu', 'uom_id': kg.id})
        burger = P.create({'name': 'burger', 'type': 'consu', 'list_price': 20})
        cola = P.create({'name': 'cola', 'type': 'consu', 'standard_price': 1.5})
        water = P.create({'name': 'water', 'type': 'consu'})
        Bom = self.env['mrp.bom']
        Bom.create({'product_tmpl_id': sauce.product_tmpl_id.id, 'product_qty': 2,
                    'product_uom_id': kg.id, 'bom_line_ids': [
                        (0, 0, {'product_id': mayo.id, 'product_qty': 1.8, 'product_uom_id': kg.id}),
                        (0, 0, {'product_id': garlic.id, 'product_qty': 0.2, 'product_uom_id': kg.id})]})
        Bom.create({'product_tmpl_id': burger.product_tmpl_id.id, 'product_qty': 1,
                    'product_uom_id': unit.id, 'bom_line_ids': [
                        (0, 0, {'product_id': bun.id, 'product_qty': 1, 'product_uom_id': unit.id}),
                        (0, 0, {'product_id': sauce.id, 'product_qty': 50, 'product_uom_id': gram.id})]})
        c = self.env['the.dash.profit'].recipe_costs(burger | cola | water)
        # sauce = 1.8 kg x 10 / 2 kg = 9 per kg; burger = 1 + 0.05 x 9 = 1.45
        self.assertAlmostEqual(c[burger.id]['cost'], 1.45, places=4)
        self.assertEqual(c[burger.id]['source'], 'recipe')
        self.assertIn('garlic', c[burger.id]['missing'])
        self.assertEqual(c[cola.id]['source'], 'product')
        self.assertEqual(c[water.id]['source'], 'none')

    def test_breakeven_runs(self):
        scope = dict(self._scope(), tz='Asia/Riyadh', start_hour=6.0,
                     d_from=dt.date(2026, 1, 1), d_to=dt.date(2026, 1, 7))
        b = self.env['the.dash.profit'].breakeven(scope)
        self.assertEqual(len(b['daily']), 7)
        self.assertGreaterEqual(b['expenses'], 0)

    def test_commissions_reconcile(self):
        """App commission: the per-app list, the items and the channels agree."""
        A = self.env['the.dash.analytics']
        scope = dict(self._scope(), tz='Asia/Riyadh', start_hour=6.0)
        c = A.app_commissions(scope)
        if c is None:
            self.skipTest('Connect not installed')
        p = self.env['the.dash.profit'].profitability(scope, limit=100000)
        self.assertAlmostEqual(p['totals']['commission'], c['totals']['commission'], places=1)
        self.assertAlmostEqual(sum(e['commission'] for e in p['items'] + p['modifiers']),
                               c['totals']['commission'], places=1)
        self.assertAlmostEqual(sum(x['commission'] for x in p['channels']),
                               c['totals']['commission'], places=1)

    def test_menu_engineering(self):
        P = self.env['the.dash.profit']
        items = [
            {'name': 'A', 'qty': 100, 'net': 2000, 'cost': 500, 'commission': 0, 'no_recipe': False, 'category': 'x'},
            {'name': 'B', 'qty': 100, 'net': 1000, 'cost': 700, 'commission': 0, 'no_recipe': False, 'category': 'x'},
            {'name': 'C', 'qty': 10, 'net': 300, 'cost': 50, 'commission': 0, 'no_recipe': False, 'category': 'x'},
            {'name': 'D', 'qty': 10, 'net': 100, 'cost': 90, 'commission': 0, 'no_recipe': False, 'category': 'x'},
        ]
        m = P.menu_engineering(items)
        classes = {i['name']: i['class'] for i in m['items']}
        self.assertEqual(classes, {'A': 'star', 'B': 'plowhorse', 'C': 'puzzle', 'D': 'dog'})

    def test_variance_runs(self):
        if 'stock.move' not in self.env:
            self.skipTest('no stock')
        scope = dict(self._scope(), tz='Asia/Riyadh', start_hour=6.0)
        v = self.env['the.dash.profit'].variance(scope)
        if v.get('error'):
            return
        t = v['totals']
        self.assertAlmostEqual(t['variance_value'], t['actual_value'] - t['theoretical_value'], places=1)
