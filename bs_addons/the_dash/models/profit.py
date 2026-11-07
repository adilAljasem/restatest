"""The Dash - item profitability from recipe cost, and break-even.

Recipe cost
-----------
A "recipe" is a bill of materials (``mrp.bom``, normal or kit). The unit cost
of a product is its recipe exploded down to purchased ingredients, each at its
product cost, converted between units of measure, and divided by the quantity
the recipe makes. Sub-recipes (a sauce, a dough) are exploded too.

* A product with no recipe falls back to its own product cost and is flagged
  "no recipe" - with a note saying where its cost came from, or that it has
  none at all.
* A combo has no recipe of its own: its cost is the cost of the items chosen
  in it, and it is flagged when one of those items has no recipe.
* Ingredients with no cost are named in the note - a recipe that costs zero
  is usually a missing purchase price, not a free burger.

Manufacturing (``mrp``) is optional. Without it every product is "no recipe"
and the product cost is used.

Break-even
----------
Kept on its own route and button: it reads the accounting and re-explodes the
recipes, so it never slows the dashboard refresh.

    contribution per order = (net sales - recipe cost) / orders
    break-even orders      = operating expenses / contribution per order

Operating expenses are the posted lines on expense, depreciation and other
expense accounts. Cost of revenue accounts are left out: the recipe cost is
already the variable cost, counting both would charge food twice.
"""

import calendar
from collections import defaultdict
from datetime import date, timedelta

from odoo import api, models

PAID_STATES = ('paid', 'done', 'invoiced')
EXPENSE_TYPES = ('expense', 'expense_depreciation', 'expense_other')
MAX_DEPTH = 6


class TheDashProfit(models.AbstractModel):
    _name = 'the.dash.profit'
    _description = 'The Dash profitability and break-even'

    # ── recipe cost ─────────────────────────────────────────────────────

    @api.model
    def _has_mrp(self):
        return 'mrp.bom' in self.env

    @api.model
    def recipe_costs(self, products):
        """-> {product_id: {cost, source, bom, missing}} for every product.

        source: 'recipe'  cost from the recipe
                'product' no recipe, cost from the product
                'none'    no recipe and no cost
                'combo'   a combo: cost comes from its items
        missing: ingredients (or sub-ingredients) that have no cost.
        """
        products = products.exists()
        cache = {}
        boms = {}
        if self._has_mrp() and products:
            found = self.env['mrp.bom'].sudo()._bom_find(
                products.filtered(lambda p: p.type != 'combo'),
                company_id=self.env.company.id)
            boms = {p.id: b for p, b in found.items() if b}
        for p in products:
            self._unit_cost(p, cache, boms, ())
        return cache

    @api.model
    def _bom_of(self, product, boms):
        if product.id in boms:
            return boms[product.id]
        bom = self.env['mrp.bom']
        if self._has_mrp() and product.type not in ('combo', 'service'):
            bom = self.env['mrp.bom'].sudo()._bom_find(
                product, company_id=self.env.company.id)[product]
        boms[product.id] = bom
        return bom

    @api.model
    def _unit_cost(self, product, cache, boms, trail):
        if product.id in cache:
            return cache[product.id]
        res = {'cost': 0.0, 'source': 'none', 'bom': None, 'missing': []}
        if product.type == 'combo':
            res['source'] = 'combo'
            cache[product.id] = res
            return res
        bom = self._bom_of(product, boms) if len(trail) < MAX_DEPTH else None
        if bom and product.id not in trail:
            total = 0.0
            missing = []
            for line in bom.bom_line_ids:
                if line._skip_bom_line(product):
                    continue
                comp = line.product_id
                sub = self._unit_cost(comp, cache, boms, trail + (product.id,))
                qty = line.product_uom_id._compute_quantity(
                    line.product_qty, comp.uom_id, round=False, raise_if_failure=False)
                total += qty * sub['cost']
                if not sub['cost']:
                    missing.append(comp.display_name)
                missing += sub['missing']
            made = bom.product_uom_id._compute_quantity(
                bom.product_qty or 1.0, product.uom_id, round=False,
                raise_if_failure=False) or 1.0
            res.update(cost=total / made, source='recipe', bom=bom.display_name,
                       missing=sorted(set(missing)))
        else:
            cost = product.sudo().standard_price or 0.0
            res.update(cost=cost, source='product' if cost else 'none')
        cache[product.id] = res
        return res

    # ── item profitability ──────────────────────────────────────────────

    @api.model
    def _note(self, info):
        src = info['source']
        if src == 'none':
            return 'بدون رسبي ولا تكلفة — الربح غير صحيح'
        if src == 'product':
            return 'بدون رسبي — التكلفة من تكلفة المنتج'
        if src == 'recipe' and info['missing']:
            return 'مكونات بدون تكلفة: ' + '، '.join(info['missing'][:5]) + \
                ('…' if len(info['missing']) > 5 else '')
        return ''

    @api.model
    def profitability(self, scope, limit=500):
        """Net sales, recipe cost and profit per item (combos at full price
        with their items' cost) and per modifier, plus every product sold
        without a recipe."""
        A = self.env['the.dash.analytics']
        where, params = A._order_where(scope)
        is_mod = A._modifier_sql('l')
        is_child = A._combo_child_sql('l')
        combo_col = 'l.combo_parent_id' if A._stored('pos.order.line', 'combo_parent_id') \
            else 'NULL::int'
        # App commission of the order, shared over its lines by their net.
        cjoin = A._commission_join('o')
        ccols = ('cm.commission AS order_commission, cm.app_src, cm.app_conn'
                 if cjoin else 'NULL::numeric AS order_commission, NULL AS app_src, NULL::int AS app_conn')
        self.env.cr.execute("""
WITH lines AS (
    SELECT l.id, l.qty, l.price_subtotal, l.product_id,
           pp.product_tmpl_id AS tmpl_id, {combo_col} AS combo_parent_id,
           {is_mod} AS is_mod, {is_child} AS is_child, {ccols},
           SUM(l.price_subtotal) OVER (PARTITION BY l.order_id) AS order_net
    FROM pos_order_line l
    JOIN pos_order o ON o.id = l.order_id
    LEFT JOIN product_product pp ON pp.id = l.product_id
    {cjoin}
    WHERE {where}
)
SELECT x.is_mod AS is_mod,
       CASE WHEN x.is_child AND NOT x.is_mod THEN COALESCE(p.tmpl_id, x.tmpl_id)
            ELSE x.tmpl_id END AS item_tmpl,
       x.product_id,
       (x.is_child AND NOT x.is_mod) AS in_combo,
       x.app_src, x.app_conn,
       SUM(x.qty) AS qty, SUM(x.price_subtotal) AS net,
       COALESCE(SUM(x.order_commission * x.price_subtotal / NULLIF(x.order_net, 0)), 0) AS commission
FROM lines x
LEFT JOIN lines p ON p.id = x.combo_parent_id
GROUP BY 1, 2, 3, 4, 5, 6
""".format(combo_col=combo_col, is_mod=is_mod, is_child=is_child, where=where,
           ccols=ccols, cjoin=cjoin or ''), params)
        rows = self.env.cr.dictfetchall()
        conn_names = A._connection_names([r['app_conn'] for r in rows])
        channels = {}

        Product = self.env['product.product'].sudo()
        products = Product.browse({r['product_id'] for r in rows if r['product_id']})
        costs = self.recipe_costs(products)
        names = {p.id: p.display_name for p in products.exists()}
        tmpl_names = A._resolve('product.template', [r['item_tmpl'] for r in rows])
        categories = A._template_categories([r['item_tmpl'] for r in rows if not r['is_mod']])

        def blank(tmpl, kind):
            return {'name': tmpl_names.get(tmpl) or 'غير محدد', 'kind': kind,
                    'category': categories.get(tmpl, '') if kind == 'item' else '',
                    'qty': 0.0, 'net': 0.0, 'cost': 0.0, 'commission': 0.0, 'sources': set(),
                    'notes': [], 'no_recipe': False, 'combo': False}

        groups = {}
        no_recipe = {}
        for r in rows:
            kind = 'modifier' if r['is_mod'] else 'item'
            g = groups.setdefault((kind, r['item_tmpl']), blank(r['item_tmpl'], kind))
            info = costs.get(r['product_id'], {'cost': 0.0, 'source': 'none', 'missing': []})
            qty = float(r['qty'] or 0)
            net = float(r['net'] or 0)
            commission = float(r['commission'] or 0)
            g['net'] += net
            g['cost'] += qty * info['cost']
            g['commission'] += commission
            is_app = bool(r['app_src'] or r['app_conn'])
            ch_name = (r['app_src'] or conn_names.get(r['app_conn']) or 'تطبيق') if is_app \
                else 'مباشر (بدون تطبيق)'
            ch = channels.setdefault(ch_name, {'name': ch_name, 'app': is_app, 'net': 0.0,
                                               'cost': 0.0, 'commission': 0.0})
            ch['net'] += net
            ch['cost'] += qty * info['cost']
            ch['commission'] += commission
            if not r['in_combo']:
                g['qty'] += qty
            if info['source'] == 'combo':
                g['combo'] = True
                continue
            g['sources'].add(info['source'])
            note = self._note(info)
            pname = names.get(r['product_id'], '')
            if info['source'] in ('product', 'none'):
                g['no_recipe'] = True
                g['notes'].append(('عنصر الكومبو «%s»: ' % pname if r['in_combo'] else '') + note)
            elif note:
                g['notes'].append(note)
            if info['source'] in ('product', 'none') or (info['source'] == 'recipe' and info['missing']):
                n = no_recipe.setdefault(r['product_id'], {
                    'name': pname, 'roles': [],
                    'qty': 0.0, 'net': 0.0, 'unit_cost': round(info['cost'], 4),
                    'source': info['source'], 'note': note})
                role = 'مودفاير' if r['is_mod'] else ('عنصر كومبو' if r['in_combo'] else 'صنف')
                if role not in n['roles']:
                    n['roles'].append(role)
                n['role'] = '، '.join(n['roles'])
                n['qty'] += qty
                n['net'] += net

        items, modifiers = [], []
        for (kind, _t), g in groups.items():
            notes = list(dict.fromkeys(n for n in g['notes'] if n))
            if g['combo'] and not notes:
                notes = ['كومبو: التكلفة من رسبي عناصره']
            elif g['combo']:
                notes.insert(0, 'كومبو')
            profit = g['net'] - g['cost']
            after = profit - g['commission']
            entry = {
                'commission': round(g['commission'], 2),
                'profit_after': round(after, 2),
                'margin_after': round(after / g['net'] * 100, 1) if g['net'] else 0.0,
                'name': g['name'], 'kind': kind, 'category': g['category'],
                'qty': round(g['qty'], 2), 'net': round(g['net'], 2),
                'cost': round(g['cost'], 2),
                'unit_cost': round(g['cost'] / g['qty'], 2) if g['qty'] else 0.0,
                'profit': round(profit, 2),
                'margin': round(profit / g['net'] * 100, 1) if g['net'] else 0.0,
                'no_recipe': g['no_recipe'],
                'recipe': 'recipe' in g['sources'] or (g['combo'] and not g['no_recipe']),
                'combo': g['combo'],
                'note': ' · '.join(notes),
            }
            (items if kind == 'item' else modifiers).append(entry)
        items.sort(key=lambda e: -e['profit'])
        modifiers.sort(key=lambda e: -e['profit'])

        missing = sorted(no_recipe.values(), key=lambda n: -abs(n['net']))
        for n in missing:
            n['qty'] = round(n['qty'], 2)
            n['net'] = round(n['net'], 2)

        def total(rows_):
            net = sum(e['net'] for e in rows_)
            cost = sum(e['cost'] for e in rows_)
            return net, cost

        commission = sum(c['commission'] for c in channels.values())
        channel_list = sorted(channels.values(), key=lambda c: (c['app'], -c['net']))
        for c in channel_list:
            c['profit'] = round(c['net'] - c['cost'], 2)
            c['profit_after'] = round(c['net'] - c['cost'] - c['commission'], 2)
            c['margin'] = round(c['profit'] / c['net'] * 100, 1) if c['net'] else 0.0
            c['margin_after'] = round(c['profit_after'] / c['net'] * 100, 1) if c['net'] else 0.0
            for k in ('net', 'cost', 'commission'):
                c[k] = round(c[k], 2)

        i_net, i_cost = total(items)
        m_net, m_cost = total(modifiers)
        net, cost = i_net + m_net, i_cost + m_cost
        nr_net = sum(e['net'] for e in items + modifiers if e['no_recipe'])
        return {
            'items': items[:limit],
            'items_count': len(items),
            'modifiers': modifiers[:limit],
            'missing': missing[:limit],
            'missing_count': len(missing),
            'mrp': self._has_mrp(),
            'connect': bool(cjoin),
            'channels': channel_list,
            'totals': {
                'net': round(net, 2), 'cost': round(cost, 2),
                'profit': round(net - cost, 2),
                'margin': round((net - cost) / net * 100, 1) if net else 0.0,
                'commission': round(commission, 2),
                'profit_after': round(net - cost - commission, 2),
                'margin_after': round((net - cost - commission) / net * 100, 1) if net else 0.0,
                'items_net': round(i_net, 2), 'items_cost': round(i_cost, 2),
                'modifiers_net': round(m_net, 2), 'modifiers_cost': round(m_cost, 2),
                'no_recipe_items': sum(1 for e in items + modifiers if e['no_recipe']),
                'no_recipe_net': round(nr_net, 2),
                'no_recipe_pct': round(nr_net / net * 100, 1) if net else 0.0,
            },
        }

    # ── break-even ──────────────────────────────────────────────────────

    @api.model
    def _expense_rows(self, d_from, d_to, analytic_ids=None):
        if 'account.move.line' not in self.env:
            return []
        params = {'df': d_from, 'dt': d_to, 'types': EXPENSE_TYPES,
                  'companies': tuple(self.env.companies.ids) or (0,)}
        analytic = ''
        if analytic_ids:
            # analytic_distribution keys are "id" or "id1,id2" strings.
            analytic = """AND aml.analytic_distribution IS NOT NULL AND EXISTS (
                SELECT 1 FROM jsonb_object_keys(aml.analytic_distribution) k
                WHERE string_to_array(k, ',') && %(analytic)s)"""
            params['analytic'] = [str(i) for i in analytic_ids]
        self.env.cr.execute("""
SELECT aml.account_id, SUM(aml.balance) AS amount
FROM account_move_line aml
JOIN account_account a ON a.id = aml.account_id
WHERE aml.parent_state = 'posted'
  AND aml.company_id IN %(companies)s
  AND a.account_type IN %(types)s
  AND aml.date BETWEEN %(df)s AND %(dt)s
  {analytic}
GROUP BY 1
""".format(analytic=analytic), params)
        return self.env.cr.dictfetchall()

    @staticmethod
    def _months_before(d, n):
        """First and last day of the n full months before d's month."""
        first_this = d.replace(day=1)
        last = first_this - timedelta(days=1)
        y, m = first_this.year, first_this.month - n
        while m <= 0:
            m += 12
            y -= 1
        return date(y, m, 1), last

    @api.model
    def breakeven(self, scope, basis='avg', months=3, analytic_ids=None):
        """Orders needed to cover operating expenses, against orders sold."""
        A = self.env['the.dash.analytics']
        d_from, d_to = scope['d_from'], scope['d_to']
        days = (d_to - d_from).days + 1

        # Expenses: actual posted in the period, or the monthly average of
        # the last full months spread per day (rent and salaries are posted
        # once a month, so "today" alone would show none).
        if basis == 'period':
            e_from, e_to = d_from, d_to
            rows = self._expense_rows(e_from, e_to, analytic_ids)
            factor = 1.0
        else:
            e_from, e_to = self._months_before(d_from, months)
            rows = self._expense_rows(e_from, e_to, analytic_ids)
            span = (e_to - e_from).days + 1
            factor = days / span if span else 0.0
        names = A._resolve('account.account', [r['account_id'] for r in rows])
        by_account = sorted(({
            'name': names.get(r['account_id'], '#%s' % r['account_id']),
            'amount': round(float(r['amount'] or 0) * factor, 2),
        } for r in rows if r['amount']), key=lambda x: -x['amount'])
        expenses = sum(a['amount'] for a in by_account)

        prof = self.profitability(scope, limit=0)
        net, cost = prof['totals']['net'], prof['totals']['cost']
        commission = prof['totals']['commission']

        where, params = A._order_where(scope)
        A._clock_params(scope, params)
        self.env.cr.execute("""
SELECT {day} AS day,
       COUNT(*) FILTER (WHERE o.amount_total >= 0) AS orders,
       SUM(o.amount_total - o.amount_tax) AS net
FROM pos_order o
WHERE {where}
GROUP BY 1 ORDER BY 1
""".format(day=A._business_day_sql('o'), where=where), params)
        day_rows = self.env.cr.dictfetchall()
        orders = sum(int(r['orders'] or 0) for r in day_rows)

        avg_net = net / orders if orders else 0.0
        avg_cost = cost / orders if orders else 0.0
        avg_commission = commission / orders if orders else 0.0
        cpo = avg_net - avg_cost - avg_commission
        be_orders = expenses / cpo if cpo > 0 else None
        cost_ratio = (cost + commission) / net if net else 0.0

        per_day = expenses / days if days else 0.0
        daily, cum_c, cum_e = [], 0.0, 0.0
        sales_by_day = {r['day']: r for r in day_rows}
        for i in range(days):
            d = d_from + timedelta(days=i)
            r = sales_by_day.get(d) or {'orders': 0, 'net': 0}
            o_n = int(r['orders'] or 0)
            d_net = float(r['net'] or 0)
            contrib = d_net * (1 - cost_ratio)
            cum_c += contrib
            cum_e += per_day
            daily.append({
                'day': str(d), 'weekday': A.AR_WEEKDAYS.get(d.isoweekday(), ''),
                'orders': o_n, 'net': round(d_net, 2),
                'contribution': round(contrib, 2),
                'expenses': round(per_day, 2),
                'be_orders': round(per_day / cpo, 1) if cpo > 0 else None,
                'cum_contribution': round(cum_c, 2), 'cum_expenses': round(cum_e, 2),
            })

        return {
            'basis': basis,
            'months': months,
            'expense_from': str(e_from), 'expense_to': str(e_to),
            'days': days,
            'expenses': round(expenses, 2),
            'expenses_per_day': round(per_day, 2),
            'by_account': by_account[:15],
            'accounts_count': len(by_account),
            'orders': orders,
            'net': round(net, 2), 'cost': round(cost, 2),
            'avg_net': round(avg_net, 2), 'avg_cost': round(avg_cost, 2),
            'commission': round(commission, 2),
            'avg_commission': round(avg_commission, 2),
            'contribution_per_order': round(cpo, 2),
            'contribution': round(net - cost - commission, 2),
            'be_orders': round(be_orders, 1) if be_orders is not None else None,
            'be_sales': round(be_orders * avg_net, 2) if be_orders is not None else None,
            'be_orders_per_day': round(be_orders / days, 1) if be_orders is not None and days else None,
            'safety_pct': round((orders - be_orders) / orders * 100, 1)
            if be_orders is not None and orders else None,
            'profit': round(net - cost - commission - expenses, 2),
            'daily': daily,
            'no_recipe_pct': prof['totals']['no_recipe_pct'],
            'mrp': prof['mrp'],
            'analytic': bool(analytic_ids),
        }

    # ── menu engineering ────────────────────────────────────────────────
    #
    # Kasavana & Smith: popularity against contribution margin per unit.
    #   popular        qty >= 70% of an equal share (qty / number of items)
    #   profitable     contribution per unit >= the sales-weighted average
    # Contribution = net sales - recipe cost - app commission.

    MENU_CLASSES = {
        'star': ('نجم', 'شائع ومربح: حافظ عليه وأبرزه'),
        'plowhorse': ('حصان عمل', 'شائع وربحه ضعيف: ارفع السعر قليلاً أو خفّض تكلفة الرسبي'),
        'puzzle': ('لغز', 'مربح وقليل الطلب: سوّقه، غيّر مكانه في المنيو أو اسمه'),
        'dog': ('ضعيف', 'قليل الطلب والربح: أعد تصميمه أو احذفه'),
    }

    @api.model
    def _classify(self, rows):
        rows = [r for r in rows if r['qty'] > 0]
        if not rows:
            return {}
        total_qty = sum(r['qty'] for r in rows)
        pop_t = 0.7 * total_qty / len(rows)
        cm_t = sum(r['contribution'] for r in rows) / total_qty if total_qty else 0.0
        out = {}
        for r in rows:
            popular = r['qty'] >= pop_t
            profitable = r['cm_unit'] >= cm_t
            out[r['key']] = ('star' if popular and profitable else
                             'plowhorse' if popular else
                             'puzzle' if profitable else 'dog')
        return {'classes': out, 'pop_threshold': round(pop_t, 2), 'cm_threshold': round(cm_t, 2)}

    @api.model
    def menu_engineering(self, items):
        rows = []
        for i, e in enumerate(items):
            if e['qty'] <= 0:
                continue
            contribution = e['net'] - e['cost'] - e.get('commission', 0.0)
            rows.append({'key': i, 'name': e['name'], 'category': e.get('category') or 'أخرى',
                         'qty': e['qty'], 'net': e['net'], 'contribution': contribution,
                         'cm_unit': contribution / e['qty'], 'price': e['net'] / e['qty'],
                         'no_recipe': e['no_recipe']})
        overall = self._classify(rows)
        by_cat = defaultdict(list)
        for r in rows:
            by_cat[r['category']].append(r)
        cat_class, cat_thresholds = {}, {}
        for cat, rs in by_cat.items():
            c = self._classify(rs)
            cat_class.update(c.get('classes', {}))
            cat_thresholds[cat] = {'pop': c.get('pop_threshold'), 'cm': c.get('cm_threshold')}
        out = []
        for r in rows:
            out.append({
                'name': r['name'], 'category': r['category'], 'qty': round(r['qty'], 2),
                'net': round(r['net'], 2), 'price': round(r['price'], 2),
                'cm_unit': round(r['cm_unit'], 2), 'contribution': round(r['contribution'], 2),
                'class': overall['classes'][r['key']], 'class_cat': cat_class[r['key']],
                'no_recipe': r['no_recipe'],
            })
        counts = defaultdict(lambda: {'count': 0, 'contribution': 0.0, 'qty': 0.0})
        for r in out:
            c = counts[r['class']]
            c['count'] += 1
            c['contribution'] += r['contribution']
            c['qty'] += r['qty']
        return {
            'items': out,
            'pop_threshold': overall.get('pop_threshold', 0),
            'cm_threshold': overall.get('cm_threshold', 0),
            'cat_thresholds': cat_thresholds,
            'labels': {k: v[0] for k, v in self.MENU_CLASSES.items()},
            'advice': {k: v[1] for k, v in self.MENU_CLASSES.items()},
            'summary': {k: {'count': counts[k]['count'],
                            'contribution': round(counts[k]['contribution'], 2),
                            'qty': round(counts[k]['qty'], 2)} for k in self.MENU_CLASSES},
        }

    # ── actual vs recipe (theoretical) ingredient usage ─────────────────
    #
    # Theoretical: every item sold exploded through its recipes down to the
    #   stocked ingredients (kits all the way; a stocked intermediate with a
    #   manufacturing recipe - a sauce made in-house - stops there), plus the
    #   ingredients that the intermediates produced in the period should have
    #   used.
    # Actual: what left the branch's stock in the period - to customers (POS
    #   deliveries, net of returns), into production, to scrap (waste) and to
    #   inventory loss on counts (net of count surpluses). Transfers to
    #   another branch are not consumption and are left out.

    @api.model
    def _explode_qty(self, product, qty, out, untracked, boms, trail=()):
        if not qty or product.type == 'combo':
            return
        bom = self._bom_of(product, boms) if len(trail) < MAX_DEPTH else None
        stocked = bool(getattr(product, 'is_storable', False))
        if bom and product.id not in trail and (bom.type == 'phantom' or not stocked):
            made = bom.product_uom_id._compute_quantity(
                bom.product_qty or 1.0, product.uom_id, round=False, raise_if_failure=False) or 1.0
            for line in bom.bom_line_ids:
                if line._skip_bom_line(product):
                    continue
                comp = line.product_id
                cq = line.product_uom_id._compute_quantity(
                    line.product_qty, comp.uom_id, round=False, raise_if_failure=False)
                self._explode_qty(comp, cq * qty / made, out, untracked, boms,
                                  trail + (product.id,))
        elif stocked:
            out[product.id] += qty
        else:
            untracked[product.id] += qty

    @api.model
    def _branch_location_paths(self, config_ids):
        configs = self.env['pos.config'].sudo().browse(config_ids).exists()
        whs = self.env['stock.warehouse']
        for c in configs:
            wh = (c.picking_type_id.warehouse_id if 'picking_type_id' in c._fields else whs) \
                or (c.warehouse_id if 'warehouse_id' in c._fields else whs)
            whs |= wh
        return [w.view_location_id.parent_path + '%' for w in whs if w.view_location_id], whs

    @api.model
    def variance(self, scope):
        if 'stock.move' not in self.env:
            return {'error': 'no_stock'}
        A = self.env['the.dash.analytics']
        Product = self.env['product.product'].sudo()

        # 1. theoretical from sales
        where, params = A._order_where(scope)
        self.env.cr.execute("""
SELECT l.product_id, SUM(l.qty) AS qty, SUM(l.price_subtotal) AS net
FROM pos_order_line l JOIN pos_order o ON o.id = l.order_id
WHERE {where} GROUP BY 1
""".format(where=where), params)
        sold_rows = self.env.cr.dictfetchall()
        net_sales = sum(float(r['net'] or 0) for r in sold_rows)
        theo = defaultdict(float)
        untracked = defaultdict(float)
        boms = {}
        for r in sold_rows:
            if r['product_id']:
                self._explode_qty(Product.browse(r['product_id']), float(r['qty'] or 0),
                                  theo, untracked, boms)

        # 2. actual stock usage in the branches' warehouses
        paths, warehouses = self._branch_location_paths(scope['config_ids'])
        if not paths:
            return {'error': 'no_warehouse'}
        self.env.cr.execute("""
WITH m AS (
    SELECT sm.product_id, sm.product_uom, sm.quantity,
           sm.scrap_id IS NOT NULL AS is_scrap, COALESCE(sm.is_inventory, FALSE) AS is_inv,
           src.usage AS su, dst.usage AS du,
           (src.usage = 'internal' AND src.parent_path LIKE ANY(%(paths)s)) AS from_branch,
           (dst.usage = 'internal' AND dst.parent_path LIKE ANY(%(paths)s)) AS to_branch
    FROM stock_move sm
    JOIN stock_location src ON src.id = sm.location_id
    JOIN stock_location dst ON dst.id = sm.location_dest_id
    WHERE sm.state = 'done' AND sm.date BETWEEN %(utc_from)s AND %(utc_to)s
)
SELECT product_id, product_uom,
       SUM(CASE WHEN from_branch AND du = 'customer' THEN quantity
                WHEN to_branch AND su = 'customer' THEN -quantity ELSE 0 END) AS sold,
       SUM(CASE WHEN from_branch AND du = 'production' THEN quantity ELSE 0 END) AS production,
       SUM(CASE WHEN to_branch AND su = 'production' THEN quantity ELSE 0 END) AS produced,
       SUM(CASE WHEN from_branch AND du = 'inventory' AND is_scrap THEN quantity ELSE 0 END) AS waste,
       SUM(CASE WHEN from_branch AND du = 'inventory' AND NOT is_scrap THEN quantity
                WHEN to_branch AND su = 'inventory' THEN -quantity ELSE 0 END) AS counts
FROM m
WHERE from_branch OR to_branch
GROUP BY 1, 2
""", {'paths': paths, 'utc_from': scope['utc_from'], 'utc_to': scope['utc_to']})
        move_rows = self.env.cr.dictfetchall()
        Uom = self.env['uom.uom'].sudo()
        actual = defaultdict(lambda: defaultdict(float))
        produced = defaultdict(float)
        for r in move_rows:
            p = Product.browse(r['product_id'])
            uom = Uom.browse(r['product_uom'])

            def conv(q):
                q = float(q or 0)
                return uom._compute_quantity(q, p.uom_id, round=False, raise_if_failure=False) \
                    if q and uom and uom != p.uom_id else q
            for k in ('sold', 'production', 'waste', 'counts'):
                actual[p.id][k] += conv(r[k])
            produced[p.id] += conv(r['produced'])

        # Intermediates made in the period should have used their recipe.
        for pid, qty in produced.items():
            if not qty:
                continue
            p = Product.browse(pid)
            bom = self._bom_of(p, boms)
            if not bom:
                continue
            made = bom.product_uom_id._compute_quantity(
                bom.product_qty or 1.0, p.uom_id, round=False, raise_if_failure=False) or 1.0
            for line in bom.bom_line_ids:
                if line._skip_bom_line(p):
                    continue
                comp = line.product_id
                cq = line.product_uom_id._compute_quantity(
                    line.product_qty, comp.uom_id, round=False, raise_if_failure=False)
                self._explode_qty(comp, cq * qty / made, theo, untracked, boms, (p.id,))

        ids = set(theo) | {pid for pid, a in actual.items() if any(a.values())}
        products = Product.browse(ids).exists()
        rows = []
        for p in products:
            a = actual.get(p.id, {})
            sold, prod, waste, counts = (a.get('sold', 0.0), a.get('production', 0.0),
                                         a.get('waste', 0.0), a.get('counts', 0.0))
            act = sold + prod + waste + counts
            th = theo.get(p.id, 0.0)
            var = act - th
            cost = p.standard_price or 0.0
            rows.append({
                'name': p.display_name,
                'category': p.categ_id.display_name if p.categ_id else '',
                'uom': p.uom_id.name,
                'theoretical': round(th, 3), 'sold': round(sold, 3), 'production': round(prod, 3),
                'waste': round(waste, 3), 'counts': round(counts, 3), 'actual': round(act, 3),
                'variance': round(var, 3),
                'variance_pct': round(var / th * 100, 1) if th else None,
                'unit_cost': round(cost, 4),
                'theoretical_value': round(th * cost, 2), 'actual_value': round(act * cost, 2),
                'variance_value': round(var * cost, 2), 'waste_value': round(waste * cost, 2),
                'counts_value': round(counts * cost, 2),
                'no_cost': not cost,
                'no_moves': not any(a.values()) if a else True,
            })
        rows.sort(key=lambda r: -abs(r['variance_value']))
        tv = sum(r['theoretical_value'] for r in rows)
        av = sum(r['actual_value'] for r in rows)
        unt = Product.browse([pid for pid, q in untracked.items() if q]).exists()
        return {
            'rows': rows,
            'warehouses': warehouses.mapped('display_name'),
            'totals': {
                'net_sales': round(net_sales, 2),
                'theoretical_value': round(tv, 2), 'actual_value': round(av, 2),
                'variance_value': round(av - tv, 2),
                'variance_pct': round((av - tv) / tv * 100, 1) if tv else 0.0,
                'food_cost_theoretical': round(tv / net_sales * 100, 1) if net_sales else 0.0,
                'food_cost_actual': round(av / net_sales * 100, 1) if net_sales else 0.0,
                'waste_value': round(sum(r['waste_value'] for r in rows), 2),
                'counts_value': round(sum(r['counts_value'] for r in rows), 2),
                'over_5': sum(1 for r in rows if r['variance_pct'] is not None
                              and abs(r['variance_pct']) > 5),
                'no_moves': sum(1 for r in rows if r['no_moves'] and r['theoretical']),
            },
            'untracked': [{'name': p.display_name, 'qty': round(untracked[p.id], 2)} for p in unt][:50],
        }
