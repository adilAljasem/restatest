"""The Dash - analytics for the dashboard cards (Odoo 18 / 19).

Everything here is aggregated in SQL (one statement per card) except the
session closing figures: Odoo computes a session's expected cash and its
difference on the fly (they are not columns), so sessions are read through the
ORM - there are only a handful per branch and day.

Every optional source is probed first and skipped when missing:

* modifiers: ``pos_restaurant_combo_modifiers`` (``is_modifier_line``,
  ``modifier_parent_line_id``, ``modifier_option_name``) and
  ``product_modifiers`` (``modifier_parent_uuid`` / ``parent_line_id``);
* delivery apps: ``bs_pos_delivery_core`` (``bs.delivery.order`` linked to the
  POS order, and the per-app payment method mapping) and FeedUs
  (``pos.order.aggregator``);
* cashier: the POS employee (``pos_hr``) when installed, else the user;
* kitchen display: Odoo 19 (``pos.prep.order`` / ``pos.prep.state``) and
  Odoo 17-18 (``pos_preparation_display.order`` and its order stages).

Scope (``scope`` dict built by the controller):
    config_ids  points of sale
    utc_from / utc_to  naive UTC datetimes of the local day bounds
    d_from / d_to      local dates (for ``business_date``)
    team_ids    optional sales teams
"""

import statistics
from collections import defaultdict

from odoo import api, models

PAID_STATES = ('paid', 'done', 'invoiced')

KIND_LABELS = {
    'cash': 'نقدي',
    'bank': 'بنك / شبكة',
    'app': 'تطبيقات التوصيل',
    'credit': 'آجل / حساب عميل',
}

# Service time beyond this is a forgotten ticket, not a measurement.
KITCHEN_MAX_MINUTES = 240


class TheDashAnalytics(models.AbstractModel):
    _name = 'the.dash.analytics'
    _description = 'The Dash dashboard analytics'

    # ── probing ─────────────────────────────────────────────────────────

    @api.model
    def _field(self, model, fname):
        if model not in self.env:
            return None
        return self.env[model]._fields.get(fname)

    @api.model
    def _stored(self, model, fname):
        f = self._field(model, fname)
        return bool(f is not None and f.store and not f.type.endswith('2many'))

    @api.model
    def _table_of(self, model):
        return self.env[model]._table if model in self.env else None

    # ── common SQL pieces ───────────────────────────────────────────────

    @api.model
    def _order_where(self, scope, alias='o', paid=True):
        where = ['%s.config_id IN %%(config_ids)s' % alias]
        params = {'config_ids': tuple(scope['config_ids']) or (0,)}
        if paid:
            where.append('%s.state IN %%(paid_states)s' % alias)
            params['paid_states'] = PAID_STATES
        if self._stored('pos.order', 'business_date'):
            where.append('%s.business_date BETWEEN %%(d_from)s AND %%(d_to)s' % alias)
            params.update(d_from=scope['d_from'], d_to=scope['d_to'])
        else:
            where.append('%s.date_order BETWEEN %%(utc_from)s AND %%(utc_to)s' % alias)
            params.update(utc_from=scope['utc_from'], utc_to=scope['utc_to'])
        if scope.get('team_ids') and self._stored('pos.order', 'crm_team_id'):
            where.append('%s.crm_team_id IN %%(team_ids)s' % alias)
            params['team_ids'] = tuple(scope['team_ids'])
        return ' AND '.join(where), params

    @api.model
    def _cashier_sql(self, alias='o'):
        """-> (sql, model) of the cashier of an order."""
        if self._stored('pos.order', 'employee_id'):
            return ('COALESCE(%s.employee_id, 0)' % alias, 'hr.employee')
        return ('COALESCE(%s.user_id, 0)' % alias, 'res.users')

    @api.model
    def _modifier_sql(self, alias='l'):
        """SQL boolean: the line is a modifier of another line."""
        parts = []
        if self._stored('pos.order.line', 'is_modifier_line'):
            parts.append('COALESCE(%s.is_modifier_line, FALSE)' % alias)
        if self._stored('pos.order.line', 'modifier_parent_line_id'):
            parts.append('%s.modifier_parent_line_id IS NOT NULL' % alias)
        if self._stored('pos.order.line', 'modifier_parent_uuid'):
            parts.append("COALESCE(%s.modifier_parent_uuid, '') <> ''" % alias)
        f = self._field('pos.order.line', 'parent_line_id')
        if f is not None and f.store:
            if f.type == 'many2one' and f.comodel_name == 'pos.order.line':
                parts.append('%s.parent_line_id IS NOT NULL' % alias)
            elif f.type in ('char', 'text'):
                parts.append("COALESCE(%s.parent_line_id, '') <> ''" % alias)
            elif f.type == 'integer':
                parts.append('COALESCE(%s.parent_line_id, 0) <> 0' % alias)
        return '(%s)' % ' OR '.join(parts) if parts else 'FALSE'

    @api.model
    def _combo_child_sql(self, alias='l'):
        if self._stored('pos.order.line', 'combo_parent_id'):
            return '%s.combo_parent_id IS NOT NULL' % alias
        return 'FALSE'

    @api.model
    def _resolve(self, model, ids):
        ids = [i for i in set(ids) if i]
        if not ids or model not in self.env:
            return {}
        return {r.id: r.display_name for r in self.env[model].sudo().browse(ids).exists()}

    # ── items and modifiers ─────────────────────────────────────────────

    @api.model
    def items_and_modifiers(self, scope, limit=300):
        """Item sales (modifiers apart, combo items folded into their combo)
        and modifier sales, from one pass over the order lines."""
        where, params = self._order_where(scope)
        is_mod = self._modifier_sql('l')
        is_child = self._combo_child_sql('l')
        has_option = self._stored('pos.order.line', 'modifier_option_name')
        option_col = ("NULLIF(l.modifier_option_name, '')" if has_option else 'NULL')
        child_join = ''
        child_cte = ''
        if self._stored('pos.order.line', 'combo_parent_id'):
            child_cte = """,
children AS (
    SELECT combo_parent_id AS parent_id,
           SUM(price_subtotal) AS excl, SUM(price_subtotal_incl) AS incl
    FROM lines WHERE is_child AND NOT is_mod
    GROUP BY combo_parent_id
)"""
            child_join = 'LEFT JOIN children ch ON ch.parent_id = x.id'
        combo_col = 'l.combo_parent_id' if self._stored('pos.order.line', 'combo_parent_id') else 'NULL::int'
        query = """
WITH lines AS (
    SELECT l.id, l.qty, l.price_subtotal, l.price_subtotal_incl,
           pp.product_tmpl_id AS tmpl_id, {combo_col} AS combo_parent_id,
           {is_mod} AS is_mod, {is_child} AS is_child, {option_col} AS option_name
    FROM pos_order_line l
    JOIN pos_order o ON o.id = l.order_id
    LEFT JOIN product_product pp ON pp.id = l.product_id
    WHERE {where}
){child_cte}
SELECT 'item' AS kind, x.tmpl_id AS key_id, NULL AS key_name,
       SUM(x.qty) AS qty,
       SUM(x.price_subtotal{child_excl}) AS excl,
       SUM(x.price_subtotal_incl{child_incl}) AS incl,
       COUNT(*) AS line_count
FROM lines x {child_join}
WHERE NOT x.is_mod AND NOT x.is_child
GROUP BY x.tmpl_id
UNION ALL
SELECT 'modifier', CASE WHEN x.option_name IS NULL THEN x.tmpl_id END, x.option_name,
       SUM(x.qty), SUM(x.price_subtotal), SUM(x.price_subtotal_incl), COUNT(*)
FROM lines x
WHERE x.is_mod
GROUP BY 2, 3
""".format(
            combo_col=combo_col, is_mod=is_mod, is_child=is_child,
            option_col=option_col, where=where, child_cte=child_cte,
            child_join=child_join,
            child_excl=' + COALESCE(ch.excl, 0)' if child_join else '',
            child_incl=' + COALESCE(ch.incl, 0)' if child_join else '',
        )
        self.env.cr.execute(query, params)
        rows = self.env.cr.dictfetchall()
        names = self._resolve('product.template', [r['key_id'] for r in rows])
        categories = self._template_categories([r['key_id'] for r in rows if r['kind'] == 'item'])

        items, modifiers = [], []
        for r in rows:
            entry = {
                'name': r['key_name'] or names.get(r['key_id']) or 'غير محدد',
                'qty': round(float(r['qty'] or 0), 2),
                'net': round(float(r['excl'] or 0), 2),
                'revenue': round(float(r['incl'] or 0), 2),
            }
            if r['kind'] == 'item':
                entry['category'] = categories.get(r['key_id'], 'أخرى')
                items.append(entry)
            else:
                modifiers.append(entry)
        # A modifier option sold on several products is one row.
        merged = {}
        for m in modifiers:
            acc = merged.setdefault(m['name'], dict(m, qty=0.0, net=0.0, revenue=0.0))
            for k in ('qty', 'net', 'revenue'):
                acc[k] = round(acc[k] + m[k], 2)
        modifiers = list(merged.values())

        items.sort(key=lambda e: -e['revenue'])
        modifiers.sort(key=lambda e: -e['revenue'])
        item_total = sum(e['revenue'] for e in items)
        mod_total = sum(e['revenue'] for e in modifiers)
        grand = item_total + mod_total
        for e in items:
            e['pct'] = round(e['revenue'] / item_total * 100, 1) if item_total else 0.0
        for e in modifiers:
            e['pct'] = round(e['revenue'] / mod_total * 100, 1) if mod_total else 0.0
        return {
            'items': items[:limit],
            'items_count': len(items),
            'modifiers': modifiers[:limit],
            'modifiers_count': len(modifiers),
            'attributes': self._attribute_options(scope),
            'totals': {
                'items': round(item_total, 2),
                'modifiers': round(mod_total, 2),
                'all': round(grand, 2),
                'items_qty': round(sum(e['qty'] for e in items), 2),
                'modifiers_qty': round(sum(e['qty'] for e in modifiers), 2),
                'modifier_share': round(mod_total / grand * 100, 1) if grand else 0.0,
            },
            'modifier_source': self._modifier_source(),
        }

    @api.model
    def _modifier_source(self):
        if self._stored('pos.order.line', 'is_modifier_line'):
            return 'pos_restaurant_combo_modifiers'
        if self._stored('pos.order.line', 'modifier_parent_uuid') or \
                self._stored('pos.order.line', 'parent_line_id'):
            return 'product_modifiers'
        return None

    @api.model
    def _template_categories(self, tmpl_ids):
        tmpl_ids = [i for i in set(tmpl_ids) if i]
        if not tmpl_ids:
            return {}
        out = {}
        for t in self.env['product.template'].sudo().browse(tmpl_ids).exists():
            if 'pos_categ_ids' in t._fields and t.pos_categ_ids:
                out[t.id] = t.pos_categ_ids[0].name
            elif 'pos_categ_id' in t._fields and t.pos_categ_id:
                out[t.id] = t.pos_categ_id.name
            else:
                out[t.id] = 'أخرى'
        return out

    @api.model
    def _attribute_options(self, scope, limit=100):
        """Attribute values chosen on the lines, with their extra price."""
        f = self._field('pos.order.line', 'attribute_value_ids')
        if f is None or f.type != 'many2many':
            return []
        where, params = self._order_where(scope)
        query = """
SELECT r."{col_value}" AS value_id, SUM(l.qty) AS qty,
       SUM(l.qty * COALESCE(v.price_extra, 0)) AS extra
FROM pos_order_line l
JOIN pos_order o ON o.id = l.order_id
JOIN "{table}" r ON r."{col_line}" = l.id
LEFT JOIN product_template_attribute_value v ON v.id = r."{col_value}"
WHERE {where}
GROUP BY 1
ORDER BY 2 DESC
LIMIT {limit}
""".format(table=f.relation, col_line=f.column1, col_value=f.column2,
           where=where, limit=int(limit))
        self.env.cr.execute(query, params)
        rows = self.env.cr.dictfetchall()
        values = self.env[f.comodel_name].sudo().browse([r['value_id'] for r in rows]).exists()
        label = {}
        for v in values:
            attr = v.attribute_id.name if 'attribute_id' in v._fields and v.attribute_id else ''
            name = v.name if 'name' in v._fields else v.display_name
            label[v.id] = '%s: %s' % (attr, name) if attr else name
        return [{
            'name': label.get(r['value_id'], '#%s' % r['value_id']),
            'qty': round(float(r['qty'] or 0), 2),
            'extra': round(float(r['extra'] or 0), 2),
        } for r in rows]

    # ── collections: cash / bank / apps ─────────────────────────────────

    @api.model
    def _app_sources(self):
        """SQL pieces that name the delivery app of an order or a payment."""
        cols, joins = [], []
        Delivery = 'bs.delivery.order'
        if self._stored(Delivery, 'pos_order_id'):
            src = ("NULLIF(d.external_source, '')"
                   if self._stored(Delivery, 'external_source') else 'NULL')
            prov = ('d.connection_id' if self._stored(Delivery, 'connection_id') else 'NULL')
            joins.append(
                'LEFT JOIN LATERAL (SELECT {src} AS src, {prov} AS conn FROM {t} d '
                'WHERE d.pos_order_id = o.id ORDER BY d.id DESC LIMIT 1) dl ON TRUE'.format(
                    src=src, prov=prov, t=self._table_of(Delivery)))
            cols += ['dl.src AS app_src', 'dl.conn AS app_conn']
        else:
            cols += ['NULL AS app_src', 'NULL::int AS app_conn']
        agg = self._field('pos.order', 'aggregator')
        if agg is not None and agg.store and agg.type in ('char', 'selection', 'many2one'):
            cols.append('o.aggregator AS app_agg')
        else:
            cols.append('NULL AS app_agg')
        Mapping = 'bs.delivery.pos.payment.mapping'
        if self._stored(Mapping, 'payment_method_id') and self._stored(Mapping, 'aggregator_name'):
            joins.append(
                'LEFT JOIN LATERAL (SELECT mp.aggregator_name AS name FROM {t} mp '
                'WHERE mp.payment_method_id = p.payment_method_id '
                'AND mp.pos_config_id = o.config_id ORDER BY mp.id LIMIT 1) ml ON TRUE'.format(
                    t=self._table_of(Mapping)))
            cols.append('ml.name AS app_map')
        else:
            cols.append('NULL AS app_map')
        return cols, joins

    @api.model
    def _app_name(self, row, conn_names, agg_names):
        name = row.get('app_src') or (conn_names.get(row.get('app_conn')) if row.get('app_conn') else None)
        if not name and row.get('app_agg') not in (None, '', False):
            agg = row['app_agg']
            name = agg_names.get(agg, agg) if isinstance(agg, int) else str(agg)
        if not name and row.get('app_map'):
            name = row['app_map']
        return (str(name).strip() or None) if name else None

    @api.model
    def payment_rows(self, scope):
        """Payments grouped by POS, cashier, method and delivery app."""
        where, params = self._order_where(scope)
        cashier, _model = self._cashier_sql('o')
        cols, joins = self._app_sources()
        query = """
SELECT o.config_id, {cashier} AS cashier_id, p.payment_method_id AS method_id,
       {cols},
       SUM(p.amount) AS amount, COUNT(DISTINCT o.id) AS orders
FROM pos_payment p
JOIN pos_order o ON o.id = p.pos_order_id
{joins}
WHERE {where}
GROUP BY 1, 2, 3, {group_cols}
""".format(cashier=cashier, cols=', '.join(cols), joins='\n'.join(joins),
           where=where,
           group_cols=', '.join(str(i) for i in range(4, 4 + len(cols))))
        self.env.cr.execute(query, params)
        rows = self.env.cr.dictfetchall()

        methods = self.env['pos.payment.method'].sudo().browse(
            list({r['method_id'] for r in rows if r['method_id']})).exists()
        method_info = {}
        for m in methods:
            kind = 'cash' if (m.is_cash_count or m.type == 'cash') else (
                'credit' if m.type == 'pay_later' else 'bank')
            method_info[m.id] = (m.display_name, kind)
        conn_names = {}
        if 'bs.delivery.connection' in self.env:
            conns = self.env['bs.delivery.connection'].sudo().browse(
                list({r['app_conn'] for r in rows if r.get('app_conn')})).exists()
            conn_names = {c.id: (c.provider_id.display_name if 'provider_id' in c._fields
                                 and c.provider_id else c.display_name) for c in conns}
        agg_f = self._field('pos.order', 'aggregator')
        agg_names = {}
        if agg_f is not None and agg_f.type == 'many2one':
            agg_names = self._resolve(agg_f.comodel_name,
                                      [r['app_agg'] for r in rows if isinstance(r.get('app_agg'), int)])

        out = []
        for r in rows:
            mname, mkind = method_info.get(r['method_id'], ('#%s' % r['method_id'], 'bank'))
            app = self._app_name(r, conn_names, agg_names)
            out.append({
                'config_id': r['config_id'],
                'cashier_id': r['cashier_id'] or 0,
                'method_id': r['method_id'],
                'method': mname,
                'method_kind': mkind,
                'kind': 'app' if app else mkind,
                'app': app,
                'amount': float(r['amount'] or 0),
                'orders': int(r['orders'] or 0),
            })
        return out

    @api.model
    def collections(self, scope, rows=None):
        rows = self.payment_rows(scope) if rows is None else rows
        totals = defaultdict(float)
        apps, methods = {}, {}
        for r in rows:
            totals[r['kind']] += r['amount']
            if r['kind'] == 'app':
                a = apps.setdefault(r['app'], {'name': r['app'], 'amount': 0.0, 'orders': 0,
                                               'cash': 0.0, 'bank': 0.0})
                a['amount'] += r['amount']
                a['orders'] += r['orders']
                a['cash' if r['method_kind'] == 'cash' else 'bank'] += r['amount']
            m = methods.setdefault(r['method_id'], {'name': r['method'], 'kind': r['method_kind'],
                                                    'amount': 0.0, 'app_amount': 0.0})
            m['amount'] += r['amount']
            if r['kind'] == 'app':
                m['app_amount'] += r['amount']
        total = sum(totals.values())

        def pct(v):
            return round(v / total * 100, 1) if total else 0.0

        app_rows = sorted(apps.values(), key=lambda a: -a['amount'])
        for a in app_rows:
            a.update(amount=round(a['amount'], 2), cash=round(a['cash'], 2),
                     bank=round(a['bank'], 2), pct=pct(a['amount']))
        method_rows = sorted(methods.values(), key=lambda m: -m['amount'])
        for m in method_rows:
            m.update(amount=round(m['amount'], 2), app_amount=round(m['app_amount'], 2),
                     kind_label=KIND_LABELS.get(m['kind'], m['kind']), pct=pct(m['amount']))
        return {
            'total': round(total, 2),
            'kinds': [{'key': k, 'name': KIND_LABELS[k], 'amount': round(totals.get(k, 0.0), 2),
                       'pct': pct(totals.get(k, 0.0))}
                      for k in ('cash', 'bank', 'app', 'credit')],
            'apps': app_rows,
            'methods': method_rows,
        }

    # ── breakdown by POS / cost center / region / cashier ───────────────

    @api.model
    def _config_maps(self, config_ids):
        configs = self.env['pos.config'].sudo().browse(config_ids).exists()
        cc, region = {}, {}
        for c in configs:
            cc[c.id] = c.dash_cost_center_id if 'dash_cost_center_id' in c._fields else None
            region[c.id] = c.dash_region_id if 'dash_region_id' in c._fields else None
        return configs, cc, region

    @api.model
    def breakdowns(self, scope, payment_rows=None, closing=None):
        """Sales, collections and closing differences per POS, cost center,
        region and cashier - all four computed together, the page switches
        between them without asking the server again."""
        payment_rows = self.payment_rows(scope) if payment_rows is None else payment_rows
        closing = self.closing(scope) if closing is None else closing
        where, params = self._order_where(scope)
        cashier, cashier_model = self._cashier_sql('o')
        self.env.cr.execute("""
SELECT o.config_id, {cashier} AS cashier_id,
       SUM(o.amount_total) AS sales,
       COUNT(*) FILTER (WHERE o.amount_total >= 0) AS orders,
       SUM(-o.amount_total) FILTER (WHERE o.amount_total < 0) AS refunds
FROM pos_order o
WHERE {where}
GROUP BY 1, 2
""".format(cashier=cashier, where=where), params)
        order_rows = self.env.cr.dictfetchall()

        configs, cc_map, region_map = self._config_maps(scope['config_ids'])
        cfg_name = {c.id: c.display_name for c in configs}
        cashier_names = self._resolve(cashier_model, [r['cashier_id'] for r in order_rows]
                                      + [r['cashier_id'] for r in payment_rows])

        def key_of(dim, config_id, cashier_id):
            if dim == 'pos':
                return config_id, cfg_name.get(config_id, '#%s' % config_id)
            if dim == 'cost_center':
                rec = cc_map.get(config_id)
                return (rec.id, rec.display_name) if rec else (0, 'بدون مركز تكلفة')
            if dim == 'region':
                rec = region_map.get(config_id)
                return (rec.id, rec.display_name) if rec else (0, 'بدون منطقة')
            return cashier_id or 0, cashier_names.get(cashier_id) or 'غير محدد'

        result = {}
        for dim in ('pos', 'cost_center', 'region', 'cashier'):
            acc = {}

            def node(key, name):
                return acc.setdefault(key, {
                    'name': name, 'sales': 0.0, 'orders': 0, 'refunds': 0.0,
                    'cash': 0.0, 'bank': 0.0, 'app': 0.0, 'credit': 0.0,
                    'sessions': 0, 'expected_cash': 0.0, 'counted_cash': 0.0,
                    'shortage': 0.0, 'overage': 0.0, 'difference': 0.0,
                    'bank_difference': 0.0,
                })

            for r in order_rows:
                n = node(*key_of(dim, r['config_id'], r['cashier_id']))
                n['sales'] += float(r['sales'] or 0)
                n['orders'] += int(r['orders'] or 0)
                n['refunds'] += float(r['refunds'] or 0)
            for r in payment_rows:
                n = node(*key_of(dim, r['config_id'], r['cashier_id']))
                n[r['kind']] += r['amount']
            for s in closing['sessions']:
                # A session belongs to its POS and to the user who ran it.
                n = node(*key_of(dim, s['config_id'], s['cashier_key'] if dim == 'cashier' else 0))
                n['sessions'] += 1
                n['expected_cash'] += s['expected']
                n['counted_cash'] += s['counted']
                n['difference'] += s['difference']
                n['bank_difference'] += s['bank_difference']
                for d in (s['difference'], s['bank_difference']):
                    if d < 0:
                        n['shortage'] += -d
                    elif d > 0:
                        n['overage'] += d
            rows = sorted(acc.values(), key=lambda x: -x['sales'])
            for n in rows:
                for k, v in n.items():
                    if isinstance(v, float):
                        n[k] = round(v, 2)
            result[dim] = rows
        result['cashier_source'] = 'employee' if cashier_model == 'hr.employee' else 'user'
        return result

    # ── closing: shortages / overages ───────────────────────────────────

    @api.model
    def closing(self, scope, limit=500):
        """Cash counted against expected, per session, and the non-cash
        differences posted at closing.

        Sessions that ran during the period (opened before its end and not
        closed before its start). Odoo computes the expected cash and the
        difference on the fly, so they are read through the ORM.
        """
        Session = self.env['pos.session'].sudo()
        domain = [('config_id', 'in', list(scope['config_ids'])),
                  ('start_at', '<=', scope['utc_to']),
                  '|', ('stop_at', '=', False), ('stop_at', '>=', scope['utc_from'])]
        sessions = Session.search(domain, order='start_at desc', limit=limit)
        has_employee = self._stored('pos.order', 'employee_id')
        bank_diffs = self._bank_differences(sessions)
        out = []
        for s in sessions:
            cashier_key = s.user_id.id
            cashier_name = s.user_id.display_name
            if has_employee:
                # pos_hr: the employee with the most orders in the session.
                counts = self.env['pos.order'].sudo()._read_group(
                    [('session_id', '=', s.id), ('employee_id', '!=', False)],
                    ['employee_id'], ['__count'])
                if counts:
                    emp, _n = max(counts, key=lambda c: c[1])
                    cashier_key, cashier_name = emp.id, emp.display_name
            cash_control = bool(s.cash_control) if 'cash_control' in s._fields else True
            expected = s.cash_register_balance_end or 0.0
            counted = s.cash_register_balance_end_real or 0.0
            diff = (s.cash_register_difference or 0.0) if s.state in ('closing_control', 'closed') else 0.0
            out.append({
                'id': s.id,
                'name': s.name,
                'config_id': s.config_id.id,
                'pos': s.config_id.display_name,
                'cashier': cashier_name or '',
                'cashier_key': cashier_key or 0,
                'user_id': s.user_id.id,
                'state': s.state,
                'state_label': dict(s._fields['state']._description_selection(self.env)).get(s.state, s.state),
                'start_at': s.start_at,
                'stop_at': s.stop_at,
                'cash_control': cash_control,
                'opening': round(s.cash_register_balance_start or 0.0, 2),
                'expected': round(expected, 2) if s.state != 'opened' else 0.0,
                'counted': round(counted, 2) if s.state != 'opened' else 0.0,
                'difference': round(diff, 2),
                'bank_difference': round(bank_diffs.get(s.id, 0.0), 2),
            })
        shortages = sum(-x for s in out for x in (s['difference'], s['bank_difference']) if x < 0)
        overages = sum(x for s in out for x in (s['difference'], s['bank_difference']) if x > 0)
        return {
            'sessions': out,
            'totals': {
                'sessions': len(out),
                'closed': sum(1 for s in out if s['state'] == 'closed'),
                'open': sum(1 for s in out if s['state'] != 'closed'),
                'expected': round(sum(s['expected'] for s in out), 2),
                'counted': round(sum(s['counted'] for s in out), 2),
                'cash_difference': round(sum(s['difference'] for s in out), 2),
                'bank_difference': round(sum(s['bank_difference'] for s in out), 2),
                'shortage': round(shortages, 2),
                'overage': round(overages, 2),
                'net': round(overages - shortages, 2),
                'with_difference': sum(1 for s in out if s['difference'] or s['bank_difference']),
            },
            'truncated': len(sessions) >= limit,
        }

    @api.model
    def _bank_differences(self, sessions):
        """Non-cash closing differences, per session.

        Odoo posts them on the payment journal's gain / loss accounts: inside
        the session entry, in the account payments of the session, or in a
        separate entry referenced "... (<session name>)" for split methods.
        Gain = overage (+), loss = shortage (-). Cash methods are left out:
        their difference is the session's cash difference.
        """
        if not sessions or 'account.move.line' not in self.env:
            return {}
        Line = self.env['account.move.line'].sudo()
        Move = self.env['account.move'].sudo()
        out = {}
        for s in sessions.filtered(lambda x: x.state == 'closed'):
            methods = s.config_id.payment_method_ids.filtered(
                lambda m: not m.is_cash_count and m.journal_id)
            accounts = (methods.journal_id.mapped('profit_account_id')
                        | methods.journal_id.mapped('loss_account_id'))
            if not accounts:
                continue
            moves = s.move_id
            if 'bank_payment_ids' in s._fields:
                moves |= s.bank_payment_ids.mapped('move_id')
            if s.name:
                moves |= Move.search([('ref', 'like', '(%s)' % s.name)])
            if not moves:
                continue
            lines = Line.search([('move_id', 'in', moves.ids), ('account_id', 'in', accounts.ids),
                                 ('parent_state', '=', 'posted')])
            total = sum(lines.mapped('credit')) - sum(lines.mapped('debit'))
            if total:
                out[s.id] = total
        return out

    # ── kitchen service time ────────────────────────────────────────────

    @api.model
    def kitchen(self, scope, late_minutes=15):
        """Time from the order reaching the kitchen display to the last of
        its items being finished, per order and display."""
        rows = None
        source = None
        if 'resta.kds.ticket' in self.env:
            rows, source = self._kitchen_resta(scope), 'resta_kds (Resta kitchen screen)'
        elif 'pos.prep.order' in self.env and 'pos.prep.state' in self.env:
            rows, source = self._kitchen_19(scope), 'pos.prep (Odoo 19)'
        elif 'pos_preparation_display.order.stage' in self.env:
            rows, source = self._kitchen_18(scope), 'pos_preparation_display (Odoo 17-18)'
        if rows is None:
            return None

        measured = [r for r in rows if r['minutes'] is not None
                    and 0 < r['minutes'] <= KITCHEN_MAX_MINUTES]
        pending = sum(1 for r in rows if r['minutes'] is None)
        if not measured:
            return {'source': source, 'count': 0, 'pending': pending, 'avg': 0,
                    'median': 0, 'p90': 0, 'max': 0, 'late': 0, 'late_pct': 0,
                    'late_minutes': late_minutes, 'by_pos': [], 'by_display': [],
                    'by_hour': [], 'slowest': []}
        cfg_names = self._resolve('pos.config', [r['config_id'] for r in measured])

        def summary(values):
            values = sorted(values)
            n = len(values)
            p90 = values[min(n - 1, int(round(0.9 * (n - 1))))]
            return {
                'count': n,
                'avg': round(sum(values) / n, 1),
                'median': round(statistics.median(values), 1),
                'p90': round(p90, 1),
                'max': round(values[-1], 1),
                'late': sum(1 for v in values if v > late_minutes),
            }

        def group(key_fn, name_fn):
            buckets = defaultdict(list)
            for r in measured:
                buckets[key_fn(r)].append(r['minutes'])
            out = []
            for k, vals in buckets.items():
                s = summary(vals)
                s['name'] = name_fn(k)
                s['late_pct'] = round(s['late'] / s['count'] * 100, 1)
                out.append(s)
            return out

        total = summary([r['minutes'] for r in measured])
        by_pos = sorted(group(lambda r: r['config_id'],
                              lambda k: cfg_names.get(k, '#%s' % k)), key=lambda x: -x['avg'])
        by_display = sorted(group(lambda r: r['display'] or 'غير محدد', lambda k: k),
                            key=lambda x: -x['avg'])
        start = int(scope.get('start_hour') or 0)
        by_hour = group(lambda r: r['hour'], lambda k: '%02d:00' % k if k is not None else '—')
        # Business-day order: the start hour first, past midnight last.
        by_hour.sort(key=lambda x: (x['name'] == '—', (int(x['name'][:2]) - start) % 24
                                    if x['name'] != '—' else 0))
        slowest = sorted(measured, key=lambda r: -r['minutes'])[:15]
        total.update({
            'source': source,
            'pending': pending,
            'late_minutes': late_minutes,
            'late_pct': round(total['late'] / total['count'] * 100, 1),
            'by_pos': by_pos,
            'by_display': by_display,
            'by_hour': by_hour,
            'slowest': [{'order': r['order'], 'pos': cfg_names.get(r['config_id'], ''),
                         'display': r['display'] or '', 'minutes': round(r['minutes'], 1)}
                        for r in slowest],
        })
        return total

    @api.model
    def _kitchen_rows(self, query, params):
        self.env.cr.execute(query, params)
        rows = self.env.cr.dictfetchall()
        out = []
        for r in rows:
            minutes = None
            if r['done'] and r['end_at'] and r['start_at']:
                minutes = (r['end_at'] - r['start_at']).total_seconds() / 60.0
            out.append({
                'order': r['order_ref'] or '#%s' % r['pos_order_id'],
                'config_id': r['config_id'],
                'display': r['display'],
                'hour': int(r['local_hour']) if r['local_hour'] is not None else None,
                'minutes': minutes,
            })
        return out

    @api.model
    def _display_name_sql(self, alias, model):
        """Display name column (jsonb when translatable)."""
        return ("COALESCE({a}.name->>%(lang)s, {a}.name->>'en_US')".format(a=alias)
                if self._is_translated(model, 'name')
                else '{a}.name'.format(a=alias))

    @api.model
    def _is_translated(self, model, fname):
        f = self._field(model, fname)
        return bool(f is not None and getattr(f, 'translate', False))

    @api.model
    def _kitchen_resta(self, scope):
        """Resta kitchen screen: one ticket per send and screen; an order is done on a screen
        when all its tickets there are done, at the last done time."""
        where, params = self._order_where(scope, paid=False)
        params.update(lang=self.env.lang or 'en_US', tz=self._scope_tz(scope))
        display = self._display_name_sql('dsp', 'resta.kds.display')
        query = """
WITH per_display AS (
    SELECT t.pos_order_id, t.display_id, MIN(t.create_date) AS start_at,
           BOOL_AND(t.done) AS done, MAX(t.done_date) AS end_at
    FROM resta_kds_ticket t
    JOIN pos_order o ON o.id = t.pos_order_id
    WHERE {where} AND NOT COALESCE(t.cancelled, FALSE) AND NOT COALESCE(t.is_resend, FALSE)
    GROUP BY t.pos_order_id, t.display_id
)
SELECT pd.pos_order_id, o.pos_reference AS order_ref, o.config_id,
       {display} AS display, pd.done, pd.start_at, pd.end_at,
       EXTRACT(HOUR FROM (pd.start_at AT TIME ZONE 'UTC' AT TIME ZONE %(tz)s)) AS local_hour
FROM per_display pd
JOIN pos_order o ON o.id = pd.pos_order_id
LEFT JOIN resta_kds_display dsp ON dsp.id = pd.display_id
""".format(where=where, display=display)
        return self._kitchen_rows(query, params)

    @api.model
    def _kitchen_19(self, scope):
        """Odoo 19: one pos.prep.state per item and display. An order is done
        on a display when every item reached the display's last stage; its
        service time ends at the last of those moves."""
        where, params = self._order_where(scope, paid=False)
        params.update(lang=self.env.lang or 'en_US', tz=self._scope_tz(scope))
        state_end = ('s.last_stage_change' if self._stored('pos.prep.state', 'last_stage_change')
                     else 's.write_date')
        seq = 'sg.sequence' if self._stored('pos.prep.stage', 'sequence') else 'sg.id'
        display = self._display_name_sql('dsp', 'pos.prep.display')
        query = """
WITH last_stage AS (
    SELECT DISTINCT ON (sg.prep_display_id) sg.prep_display_id AS display_id, sg.id AS stage_id
    FROM pos_prep_stage sg
    ORDER BY sg.prep_display_id, {seq} DESC, sg.id DESC
),
per_display AS (
    SELECT po.id AS prep_id, po.pos_order_id, MIN(po.create_date) AS start_at,
           sg.prep_display_id AS display_id,
           BOOL_AND(s.stage_id = ls.stage_id) AS done,
           MAX({state_end}) AS end_at
    FROM pos_prep_order po
    JOIN pos_order o ON o.id = po.pos_order_id
    JOIN pos_prep_line pl ON pl.prep_order_id = po.id
    JOIN pos_prep_state s ON s.prep_line_id = pl.id
    JOIN pos_prep_stage sg ON sg.id = s.stage_id
    JOIN last_stage ls ON ls.display_id = sg.prep_display_id
    WHERE {where}
    GROUP BY po.id, po.pos_order_id, sg.prep_display_id
)
SELECT pd.pos_order_id, o.pos_reference AS order_ref, o.config_id,
       {display} AS display, pd.done, pd.start_at, pd.end_at,
       EXTRACT(HOUR FROM (pd.start_at AT TIME ZONE 'UTC' AT TIME ZONE %(tz)s)) AS local_hour
FROM per_display pd
JOIN pos_order o ON o.id = pd.pos_order_id
LEFT JOIN pos_prep_display dsp ON dsp.id = pd.display_id
""".format(seq=seq, state_end=state_end, where=where, display=display)
        return self._kitchen_rows(query, params)

    @api.model
    def _kitchen_18(self, scope):
        """Odoo 17-18: an order stage row is written each time the order moves
        on a display; it is done when it reaches the display's last stage."""
        where, params = self._order_where(scope, paid=False)
        params.update(lang=self.env.lang or 'en_US', tz=self._scope_tz(scope))
        Stage = 'pos_preparation_display.stage'
        seq = 'sg.sequence' if self._stored(Stage, 'sequence') else 'sg.id'
        done_col = 'os.done' if self._stored('pos_preparation_display.order.stage', 'done') else 'FALSE'
        display = self._display_name_sql('dsp', 'pos_preparation_display.display')
        query = """
WITH last_stage AS (
    SELECT DISTINCT ON (sg.preparation_display_id) sg.preparation_display_id AS display_id,
           sg.id AS stage_id
    FROM pos_preparation_display_stage sg
    ORDER BY sg.preparation_display_id, {seq} DESC, sg.id DESC
),
per_display AS (
    SELECT po.id AS prep_id, po.pos_order_id, MIN(po.create_date) AS start_at,
           os.preparation_display_id AS display_id,
           BOOL_OR(os.stage_id = ls.stage_id OR {done_col}) AS done,
           MIN(os.create_date) FILTER (WHERE os.stage_id = ls.stage_id OR {done_col}) AS end_at
    FROM pos_preparation_display_order po
    JOIN pos_order o ON o.id = po.pos_order_id
    JOIN pos_preparation_display_order_stage os ON os.order_id = po.id
    LEFT JOIN last_stage ls ON ls.display_id = os.preparation_display_id
    WHERE {where}
    GROUP BY po.id, po.pos_order_id, os.preparation_display_id
)
SELECT pd.pos_order_id, o.pos_reference AS order_ref, o.config_id,
       {display} AS display, pd.done, pd.start_at, pd.end_at,
       EXTRACT(HOUR FROM (pd.start_at AT TIME ZONE 'UTC' AT TIME ZONE %(tz)s)) AS local_hour
FROM per_display pd
JOIN pos_order o ON o.id = pd.pos_order_id
LEFT JOIN pos_preparation_display_display dsp ON dsp.id = pd.display_id
""".format(seq=seq, done_col=done_col, where=where, display=display)
        return self._kitchen_rows(query, params)

    # ── business day: clock, order types, hours ─────────────────────────
    #
    # Everything below follows pos_business_day: hours are read in the
    # business-day timezone, a day starts at the company's start hour, and
    # the 24 hours are returned in that order (06:00 ... 05:00), so the last
    # bar is the last hour of the shift, not 23:00 of the calendar day.

    ORDER_TYPES = [
        ('dine_in', 'محلي'),
        ('takeaway', 'سفري'),
        ('delivery', 'توصيل'),
        ('app', 'تطبيقات التوصيل'),
        ('other', 'غير محدد'),
    ]

    # Odoo 19 pos_restaurant master presets.
    PRESET_XMLIDS = {
        'pos_restaurant.pos_takein_preset': 'dine_in',
        'pos_restaurant.pos_takeout_preset': 'takeaway',
        'pos_restaurant.pos_delivery_preset': 'delivery',
    }

    # Any other preset is classified by its name (every language checked).
    # Order matters: "Take in" is dine-in before "take" makes it takeaway.
    TYPE_KEYWORDS = (
        ('delivery', ('deliver', 'توصيل', 'دليفري', 'ديليفري')),
        ('dine_in', ('dine', 'eat in', 'eat-in', 'take in', 'takein', 'table',
                     'محلي', 'صالة', 'داخل', 'طاولة', 'جلوس')),
        ('takeaway', ('take', 'away', 'pick', 'carry', 'to go', 'togo', 'out',
                      'car', 'drive', 'سفري', 'استلام', 'تيك', 'خارجي', 'سيار')),
    )

    @api.model
    def _name_type(self, text):
        """Order type from a free-text name ('سفري', 'Dine In'...), or None."""
        text = (text or '').strip().lower()
        if not text:
            return None
        for k, words in self.TYPE_KEYWORDS:
            if any(w in text for w in words):
                return k
        return None

    @api.model
    def _type_name_col(self, alias='o'):
        """The order type typed on the order by codusic_pos_order_types
        (pos.order.pos_order_type_name), when that module is installed."""
        f = self._field('pos.order', 'pos_order_type_name')
        if f is not None and f.store and f.type in ('char', 'text'):
            return "NULLIF(TRIM(%s.pos_order_type_name), '')" % alias
        return None

    AR_WEEKDAYS = {1: 'الاثنين', 2: 'الثلاثاء', 3: 'الأربعاء', 4: 'الخميس',
                   5: 'الجمعة', 6: 'السبت', 7: 'الأحد'}
    # Saudi week: Sunday first.
    WEEK_ORDER = (7, 1, 2, 3, 4, 5, 6)

    @api.model
    def _scope_tz(self, scope):
        return (scope or {}).get('tz') or self.env.user.tz or 'UTC'

    @api.model
    def _clock_params(self, scope, params):
        start = float(scope.get('start_hour') or 0.0)
        params.update(tz=self._scope_tz(scope),
                      shift='%d minutes' % int(round(start * 60)))
        return start

    @api.model
    def _local_ts(self, alias='o', col='date_order'):
        return "({a}.{c} AT TIME ZONE 'UTC' AT TIME ZONE %(tz)s)".format(a=alias, c=col)

    @api.model
    def _business_day_sql(self, alias='o'):
        """The business date of an order: the stored pos_business_day field
        when present, else the same rule computed in SQL."""
        if self._stored('pos.order', 'business_date'):
            return '%s.business_date' % alias
        return '(%s - %%(shift)s::interval)::date' % self._local_ts(alias)

    @api.model
    def _app_order_sql(self, alias='o'):
        """SQL boolean: the order came from a delivery app."""
        parts = []
        Delivery = 'bs.delivery.order'
        if self._stored(Delivery, 'pos_order_id'):
            parts.append('EXISTS (SELECT 1 FROM {t} d WHERE d.pos_order_id = {a}.id)'.format(
                t=self._table_of(Delivery), a=alias))
        agg = self._field('pos.order', 'aggregator')
        if agg is not None and agg.store:
            if agg.type == 'many2one':
                parts.append('%s.aggregator IS NOT NULL' % alias)
            elif agg.type in ('char', 'selection'):
                parts.append("COALESCE(%s.aggregator, '') <> ''" % alias)
        return '(%s)' % ' OR '.join(parts) if parts else 'FALSE'

    @api.model
    def _order_type_cols(self, alias='o'):
        """Columns that decide dine-in / takeaway / delivery / app."""
        preset = ('%s.preset_id' % alias) if self._stored('pos.order', 'preset_id') else 'NULL::int'
        table = ('(%s.table_id IS NOT NULL)' % alias) if self._stored('pos.order', 'table_id') \
            else 'NULL::boolean'
        take = 'FALSE'
        f = self._field('pos.order', 'takeaway')       # Odoo 17 pos_restaurant
        if f is not None and f.store and f.type == 'boolean':
            take = 'COALESCE(%s.takeaway, FALSE)' % alias
        return ['%s AS preset_id' % preset, '%s AS has_table' % table,
                '%s AS is_takeaway' % take, '%s AS is_app' % self._app_order_sql(alias),
                '%s AS type_name' % (self._type_name_col(alias) or 'NULL::text')]

    @api.model
    def _preset_types(self, preset_ids):
        """-> ({preset_id: type}, {preset_id: name})."""
        ids = [i for i in set(preset_ids) if i]
        if not ids or 'pos.preset' not in self.env:
            return {}, {}
        presets = self.env['pos.preset'].sudo().browse(ids).exists()
        xmlids = {}
        for d in self.env['ir.model.data'].sudo().search_read(
                [('model', '=', 'pos.preset'), ('res_id', 'in', presets.ids)],
                ['module', 'name', 'res_id']):
            xmlids[d['res_id']] = '%s.%s' % (d['module'], d['name'])
        # Presets the delivery hub was told to use for each fulfilment.
        hinted = {}
        Binding = 'bs.delivery.store.binding'
        if Binding in self.env:
            fmap = (('dine_in_preset_id', 'dine_in'), ('pickup_preset_id', 'takeaway'),
                    ('delivery_preset_id', 'delivery'))
            fnames = [f for f, _k in fmap if self._field(Binding, f) is not None]
            if fnames:
                for b in self.env[Binding].sudo().search_read([], fnames):
                    for f, kind in fmap:
                        if f in b and b[f]:
                            hinted.setdefault(b[f][0], kind)
        langs = [code for code, _n in self.env['res.lang'].get_installed()]
        kinds, names = {}, {}
        for p in presets:
            names[p.id] = p.display_name
            kind = self.PRESET_XMLIDS.get(xmlids.get(p.id))
            if not kind:
                # The name in every installed language (an uninstalled
                # language in the context raises).
                kind = self._name_type(' '.join({p.with_context(lang=code).name or ''
                                                 for code in langs} | {p.name or ''}))
            kinds[p.id] = kind or hinted.get(p.id) or 'other'
        return kinds, names

    @api.model
    def _row_type(self, r, kinds):
        if r.get('is_app'):
            return 'app'
        # The type chosen at the till (codusic_pos_order_types) comes first.
        named = self._name_type(r.get('type_name'))
        if named:
            return named
        kind = kinds.get(r.get('preset_id')) if r.get('preset_id') else None
        if kind and kind != 'other':
            return kind
        if r.get('has_table'):
            return 'dine_in'
        if r.get('is_takeaway'):
            return 'takeaway'
        if r.get('has_table') is False and not r.get('preset_id'):
            # Restaurant installed, no table, no preset: a counter order.
            return 'takeaway'
        return kind or 'other'

    @staticmethod
    def _hhmm(t, shift_minutes):
        """A business-shifted time of day back to the wall clock."""
        if t is None:
            return None
        m = (t.hour * 60 + t.minute + shift_minutes) % 1440
        return '%02d:%02d' % (m // 60, m % 60)

    @api.model
    def activity(self, scope):
        """Sales by business hour, business day and order type - one SQL pass.

        Returns the 24 hours in business-day order with a split by order
        type, the first and last order of the shift, the busiest hour, the
        day-by-day trend, a weekday x hour grid and the order-type totals.
        """
        where, params = self._order_where(scope)
        start = self._clock_params(scope, params)
        shift_min = int(round(start * 60))
        ts = self._local_ts('o')
        cols = self._order_type_cols('o')
        query = """
SELECT {day} AS day, EXTRACT(HOUR FROM {ts})::int AS hour,
       {cols},
       SUM(o.amount_total) AS sales,
       COUNT(*) FILTER (WHERE o.amount_total >= 0) AS orders,
       COALESCE(SUM(-o.amount_total) FILTER (WHERE o.amount_total < 0), 0) AS refunds,
       MIN(({ts} - %(shift)s::interval)::time) AS first_t,
       MAX(({ts} - %(shift)s::interval)::time) AS last_t
FROM pos_order o
WHERE {where}
GROUP BY {group}
""".format(day=self._business_day_sql('o'), ts=ts, cols=', '.join(cols), where=where,
           group=', '.join(str(i) for i in range(1, 3 + len(cols))))
        self.env.cr.execute(query, params)
        rows = self.env.cr.dictfetchall()

        kinds, preset_names = self._preset_types([r['preset_id'] for r in rows])
        by_name = bool(self._type_name_col('o'))
        type_keys = [k for k, _l in self.ORDER_TYPES]
        type_label = dict(self.ORDER_TYPES)
        start_h = int(start)

        def blank():
            return {'sales': 0.0, 'orders': 0, 'refunds': 0.0,
                    'by_type': {k: 0.0 for k in type_keys},
                    'orders_by_type': {k: 0 for k in type_keys}}

        def add(acc, r, kind):
            sales, orders = float(r['sales'] or 0), int(r['orders'] or 0)
            acc['sales'] += sales
            acc['orders'] += orders
            acc['refunds'] += float(r['refunds'] or 0)
            acc['by_type'][kind] += sales
            acc['orders_by_type'][kind] += orders

        hours = {h: blank() for h in range(24)}
        days, grid, types, presets = {}, {}, {}, {}
        first_t = last_t = None
        for r in rows:
            kind = self._row_type(r, kinds)
            h = r['hour'] if r['hour'] is not None else 0
            add(hours[h], r, kind)
            if r['day']:
                add(days.setdefault(r['day'], blank()), r, kind)
                wd = r['day'].isoweekday()
                cell = grid.setdefault((wd, h), {'sales': 0.0, 'orders': 0})
                cell['sales'] += float(r['sales'] or 0)
                cell['orders'] += int(r['orders'] or 0)
            t = types.setdefault(kind, {'key': kind, 'name': type_label[kind],
                                        'sales': 0.0, 'orders': 0, 'refunds': 0.0})
            t['sales'] += float(r['sales'] or 0)
            t['orders'] += int(r['orders'] or 0)
            t['refunds'] += float(r['refunds'] or 0)
            # One row per type name typed at the till, else per preset.
            if by_name:
                pkey = r['type_name'] or ''
                label = r['type_name'] or 'بدون نوع طلب'
            else:
                pkey = r['preset_id'] or 0
                label = preset_names.get(pkey) or ('بدون إعداد مسبق' if not pkey else '#%s' % pkey)
            p = presets.setdefault((pkey, kind), {
                'name': label,
                'type': kind, 'type_name': type_label[kind], 'sales': 0.0, 'orders': 0})
            p['sales'] += float(r['sales'] or 0)
            p['orders'] += int(r['orders'] or 0)
            if int(r['orders'] or 0) > 0:
                if r['first_t'] is not None and (first_t is None or r['first_t'] < first_t):
                    first_t = r['first_t']
                if r['last_t'] is not None and (last_t is None or r['last_t'] > last_t):
                    last_t = r['last_t']

        def finish(acc):
            acc['sales'] = round(acc['sales'], 2)
            acc['refunds'] = round(acc['refunds'], 2)
            acc['avg'] = round(acc['sales'] / acc['orders'], 2) if acc['orders'] else 0.0
            acc['by_type'] = {k: round(v, 2) for k, v in acc['by_type'].items()}
            return acc

        hour_list = []
        for i in range(24):
            h = (start_h + i) % 24
            e = finish(hours[h])
            e.update(hour='%02d:00' % h, h=h, idx=i)
            hour_list.append(e)

        active = [e for e in hour_list if e['orders'] or e['sales']]
        peak = max(active, key=lambda e: e['sales']) if active else None

        day_list = []
        for d in sorted(days):
            e = finish(days[d])
            e.update(day=str(d), weekday=self.AR_WEEKDAYS.get(d.isoweekday(), ''))
            day_list.append(e)

        # Weekday x hour: average per occurrence of that weekday in the period.
        wd_count = defaultdict(int)
        for d in days:
            wd_count[d.isoweekday()] += 1
        heat = []
        for wd in self.WEEK_ORDER:
            if not wd_count.get(wd):
                continue
            n = wd_count[wd]
            heat.append({
                'weekday': self.AR_WEEKDAYS[wd], 'days': n,
                'cells': [round(grid.get((wd, (start_h + i) % 24), {}).get('sales', 0.0) / n, 2)
                          for i in range(24)],
                'orders': [round(grid.get((wd, (start_h + i) % 24), {}).get('orders', 0) / n, 1)
                           for i in range(24)],
            })

        total_sales = sum(t['sales'] for t in types.values())
        type_list = []
        for k in type_keys:
            t = types.get(k)
            if not t:
                continue
            t['sales'] = round(t['sales'], 2)
            t['refunds'] = round(t['refunds'], 2)
            t['avg'] = round(t['sales'] / t['orders'], 2) if t['orders'] else 0.0
            t['pct'] = round(t['sales'] / total_sales * 100, 1) if total_sales else 0.0
            type_list.append(t)
        preset_list = sorted(presets.values(), key=lambda p: -p['sales'])
        for p in preset_list:
            p['sales'] = round(p['sales'], 2)
            p['pct'] = round(p['sales'] / total_sales * 100, 1) if total_sales else 0.0

        return {
            'start_hour': start,
            'hours': hour_list,
            'first_order': self._hhmm(first_t, shift_min),
            'last_order': self._hhmm(last_t, shift_min),
            'active_hours': len([e for e in hour_list if e['orders']]),
            'peak': {'hour': peak['hour'], 'sales': peak['sales'], 'orders': peak['orders']}
            if peak else None,
            'days': day_list,
            'heat': heat,
            'types': type_list,
            'type_labels': dict(self.ORDER_TYPES),
            'presets': preset_list,
            'type_source': 'order_type' if by_name else
                           'preset' if self._stored('pos.order', 'preset_id') else (
                'table' if self._stored('pos.order', 'table_id') else None),
        }

    @api.model
    def payments_by_hour(self, scope):
        """Collections (cash / bank / apps / credit) by business hour and day.

        Same classification as collections(): an app order counts for the
        app whatever method settled it.
        """
        where, params = self._order_where(scope)
        start = self._clock_params(scope, params)
        cols, joins = self._app_sources()
        query = """
SELECT EXTRACT(HOUR FROM {ts})::int AS hour, {day} AS day,
       p.payment_method_id AS method_id, {cols},
       SUM(p.amount) AS amount
FROM pos_payment p
JOIN pos_order o ON o.id = p.pos_order_id
{joins}
WHERE {where}
GROUP BY 1, 2, 3, {group_cols}
""".format(ts=self._local_ts('o'), day=self._business_day_sql('o'), cols=', '.join(cols),
           joins='\n'.join(joins), where=where,
           group_cols=', '.join(str(i) for i in range(4, 4 + len(cols))))
        self.env.cr.execute(query, params)
        rows = self.env.cr.dictfetchall()
        methods = self.env['pos.payment.method'].sudo().browse(
            list({r['method_id'] for r in rows if r['method_id']})).exists()
        method_kind = {m.id: ('cash' if (m.is_cash_count or m.type == 'cash') else
                              ('credit' if m.type == 'pay_later' else 'bank')) for m in methods}
        agg_f = self._field('pos.order', 'aggregator')
        agg_names = {}
        if agg_f is not None and agg_f.type == 'many2one':
            agg_names = self._resolve(agg_f.comodel_name,
                                      [r['app_agg'] for r in rows if isinstance(r.get('app_agg'), int)])
        kinds = ('cash', 'bank', 'app', 'credit')
        hours = {h: dict.fromkeys(kinds, 0.0) for h in range(24)}
        days = {}
        for r in rows:
            kind = 'app' if self._app_name(r, {}, agg_names) or r.get('app_conn') else \
                method_kind.get(r['method_id'], 'bank')
            amount = float(r['amount'] or 0)
            hours[r['hour'] if r['hour'] is not None else 0][kind] += amount
            if r['day']:
                days.setdefault(r['day'], dict.fromkeys(kinds, 0.0))[kind] += amount
        start_h = int(start)
        out_hours = []
        for i in range(24):
            h = (start_h + i) % 24
            e = {k: round(v, 2) for k, v in hours[h].items()}
            e.update(hour='%02d:00' % h, h=h)
            out_hours.append(e)
        out_days = []
        for d in sorted(days):
            e = {k: round(v, 2) for k, v in days[d].items()}
            e['day'] = str(d)
            out_days.append(e)
        return {'hours': out_hours, 'days': out_days,
                'labels': {k: KIND_LABELS[k] for k in kinds}}

    @api.model
    def shortage_charts(self, closing, scope):
        """Shortages and overages arranged for charts: by business day, by
        point of sale, by cashier, and the sessions with the biggest gaps."""
        import pytz
        from datetime import timedelta
        try:
            tz = pytz.timezone(self._scope_tz(scope))
        except pytz.UnknownTimeZoneError:
            tz = pytz.utc
        shift = timedelta(hours=float(scope.get('start_hour') or 0.0))

        def bday(dt):
            if not dt:
                return None
            local = pytz.utc.localize(dt).astimezone(tz).replace(tzinfo=None)
            return str((local - shift).date())

        def blank(name):
            return {'name': name, 'shortage': 0.0, 'overage': 0.0, 'cash': 0.0,
                    'bank': 0.0, 'sessions': 0}

        by_day, by_pos, by_cashier = {}, {}, {}
        for s in closing.get('sessions', []):
            if s['state'] != 'closed' and not (s['difference'] or s['bank_difference']):
                continue
            nodes = (by_day.setdefault(bday(s['start_at']) or '—', blank(bday(s['start_at']) or '—')),
                     by_pos.setdefault(s['config_id'], blank(s['pos'])),
                     by_cashier.setdefault(s['cashier_key'] or 0, blank(s['cashier'] or 'غير محدد')))
            for n in nodes:
                n['sessions'] += 1
                n['cash'] += s['difference']
                n['bank'] += s['bank_difference']
                for d in (s['difference'], s['bank_difference']):
                    if d < 0:
                        n['shortage'] += -d
                    elif d > 0:
                        n['overage'] += d

        def done(nodes, key=None):
            out = list(nodes.values())
            for n in out:
                for k in ('shortage', 'overage', 'cash', 'bank'):
                    n[k] = round(n[k], 2)
                n['net'] = round(n['overage'] - n['shortage'], 2)
            out.sort(key=key or (lambda n: -(n['shortage'] + n['overage'])))
            return out

        top = sorted(closing.get('sessions', []),
                     key=lambda s: -(abs(s['difference']) + abs(s['bank_difference'])))
        return {
            'by_day': done(by_day, key=lambda n: n['name']),
            'by_pos': done(by_pos),
            'by_cashier': done(by_cashier),
            'top_sessions': [{
                'name': s['name'], 'pos': s['pos'], 'cashier': s['cashier'],
                'cash': s['difference'], 'bank': s['bank_difference'],
                'net': round(s['difference'] + s['bank_difference'], 2),
            } for s in top if s['difference'] or s['bank_difference']][:15],
        }

    # ── delivery-app commissions (Connect: bs_pos_delivery_core) ────────
    #
    # Each Connect order carries the commission the platform charges
    # (commission_amount, from the platform or from the connection's rate
    # at intake). When an order has none, the connection's current
    # "Default Commission %" is applied to the order total and the figure is
    # marked as estimated.

    @api.model
    def _commission_join(self, alias='o'):
        """-> LATERAL join exposing cm.app_src, cm.app_conn, cm.commission,
        cm.estimated, cm.gmv for an order - or None without Connect."""
        D, C = 'bs.delivery.order', 'bs.delivery.connection'
        if not (self._stored(D, 'pos_order_id') and self._stored(D, 'commission_amount')):
            return None
        src = "NULLIF(d.external_source, '')" if self._stored(D, 'external_source') else 'NULL'
        gmv = ('COALESCE(NULLIF(d.amount_total, 0), {a}.amount_total)'.format(a=alias)
               if self._stored(D, 'amount_total') else '%s.amount_total' % alias)
        conn = 'd.connection_id' if self._stored(D, 'connection_id') else 'NULL::int'
        pct, cjoin = '0', ''
        if self._stored(D, 'connection_id') and self._stored(C, 'commission_pct'):
            pct = 'COALESCE(c.commission_pct, 0)'
            cjoin = 'LEFT JOIN %s c ON c.id = d.connection_id' % self._table_of(C)
        return """LEFT JOIN LATERAL (
    SELECT {src} AS app_src, {conn} AS app_conn, {gmv} AS gmv,
           CASE WHEN COALESCE(d.commission_amount, 0) <> 0 THEN d.commission_amount
                ELSE {gmv} * {pct} / 100.0 END AS commission,
           (COALESCE(d.commission_amount, 0) = 0 AND {pct} > 0) AS estimated
    FROM {t} d {cjoin}
    WHERE d.pos_order_id = {a}.id
    ORDER BY d.id DESC LIMIT 1
) cm ON TRUE""".format(src=src, conn=conn, gmv=gmv, pct=pct, cjoin=cjoin,
                       t=self._table_of(D), a=alias)

    @api.model
    def _connection_names(self, conn_ids):
        C = 'bs.delivery.connection'
        if C not in self.env:
            return {}
        conns = self.env[C].sudo().browse([i for i in set(conn_ids) if i]).exists()
        return {c.id: (c.provider_id.display_name if 'provider_id' in c._fields and c.provider_id
                       else c.display_name) for c in conns}

    @api.model
    def app_commissions(self, scope):
        """Commission per app and per business day, actual vs estimated,
        plus app orders that have no Connect record (no commission data)."""
        join = self._commission_join('o')
        if not join:
            return None
        where, params = self._order_where(scope)
        self._clock_params(scope, params)
        no_data = 'FALSE'
        agg = self._field('pos.order', 'aggregator')
        if agg is not None and agg.store:
            no_data = ('(cm.app_conn IS NULL AND cm.app_src IS NULL AND %s)' %
                       ('o.aggregator IS NOT NULL' if agg.type == 'many2one'
                        else "COALESCE(o.aggregator, '') <> ''"))
        self.env.cr.execute("""
SELECT {day} AS day, cm.app_src, cm.app_conn,
       (cm.app_conn IS NOT NULL OR cm.app_src IS NOT NULL) AS is_connect,
       {no_data} AS no_data,
       COUNT(*) AS orders, SUM(o.amount_total) AS sales,
       SUM(o.amount_total - o.amount_tax) AS net,
       COALESCE(SUM(cm.gmv), 0) AS gmv,
       COALESCE(SUM(cm.commission), 0) AS commission,
       COALESCE(SUM(cm.commission) FILTER (WHERE cm.estimated), 0) AS estimated,
       COUNT(*) FILTER (WHERE cm.estimated) AS estimated_orders
FROM pos_order o
{join}
WHERE {where}
GROUP BY 1, 2, 3, 4, 5
""".format(day=self._business_day_sql('o'), no_data=no_data, join=join, where=where), params)
        rows = self.env.cr.dictfetchall()
        names = self._connection_names([r['app_conn'] for r in rows])
        apps, days = {}, {}
        missing = {'orders': 0, 'sales': 0.0}
        for r in rows:
            if r['no_data']:
                missing['orders'] += int(r['orders'] or 0)
                missing['sales'] += float(r['sales'] or 0)
                continue
            if not r['is_connect']:
                continue
            name = r['app_src'] or names.get(r['app_conn']) or 'تطبيق'
            a = apps.setdefault(name, {'name': name, 'orders': 0, 'sales': 0.0, 'net': 0.0,
                                       'gmv': 0.0, 'commission': 0.0, 'estimated': 0.0,
                                       'estimated_orders': 0})
            for k in ('sales', 'net', 'gmv', 'commission', 'estimated'):
                a[k] += float(r[k] or 0)
            a['orders'] += int(r['orders'] or 0)
            a['estimated_orders'] += int(r['estimated_orders'] or 0)
            if r['day']:
                dd = days.setdefault(r['day'], defaultdict(float))
                dd[name] += float(r['commission'] or 0)
        app_list = sorted(apps.values(), key=lambda a: -a['commission'])
        for a in app_list:
            for k in ('sales', 'net', 'gmv', 'commission', 'estimated'):
                a[k] = round(a[k], 2)
            a['rate'] = round(a['commission'] / a['gmv'] * 100, 2) if a['gmv'] else 0.0
            a['per_order'] = round(a['commission'] / a['orders'], 2) if a['orders'] else 0.0
            a['after'] = round(a['net'] - a['commission'], 2)
        total = sum(a['commission'] for a in app_list)
        return {
            'apps': app_list,
            'days': [dict({'day': str(d)}, **{k: round(v, 2) for k, v in days[d].items()})
                     for d in sorted(days)],
            'app_names': [a['name'] for a in app_list],
            'totals': {
                'orders': sum(a['orders'] for a in app_list),
                'sales': round(sum(a['sales'] for a in app_list), 2),
                'gmv': round(sum(a['gmv'] for a in app_list), 2),
                'commission': round(total, 2),
                'estimated': round(sum(a['estimated'] for a in app_list), 2),
                'estimated_orders': sum(a['estimated_orders'] for a in app_list),
                'rate': round(total / sum(a['gmv'] for a in app_list) * 100, 2)
                if sum(a['gmv'] for a in app_list) else 0.0,
            },
            'no_data': {'orders': missing['orders'], 'sales': round(missing['sales'], 2)},
        }
