"""Multi-layer POS report engine.

Why raw SQL instead of ``_read_group``:

  * ``_read_group`` cannot group by a related field (``order_id.config_id``),
    and half of the requested dimensions live on ``pos.order`` while the
    measures live on ``pos.order.line``.
  * It cannot express ``COUNT(DISTINCT order_id)``, which is what "number of
    orders" means once you group by a line-level dimension such as product.
  * It cannot express ``FILTER (WHERE ...)``, needed to split sales from
    cancellations and refunds inside a single pass.

So the report is one hand-written statement. Every fragment that reaches the
SQL string comes from the whitelists below - ``DIMENSIONS``, ``JOINS`` and
``MEASURES``. No caller-supplied text is ever interpolated; dates and ids are
bound as query parameters.
"""

from odoo import api, models


# Arabic labels for the derived time dimensions
AR_WEEKDAYS = {
    '1': 'الاثنين', '2': 'الثلاثاء', '3': 'الأربعاء', '4': 'الخميس',
    '5': 'الجمعة', '6': 'السبت', '7': 'الأحد',
}
AR_MONTHS = {
    '01': 'يناير', '02': 'فبراير', '03': 'مارس', '04': 'أبريل',
    '05': 'مايو', '06': 'يونيو', '07': 'يوليو', '08': 'أغسطس',
    '09': 'سبتمبر', '10': 'أكتوبر', '11': 'نوفمبر', '12': 'ديسمبر',
}


class PosDashboardReport(models.AbstractModel):
    _name = 'the.dash.report'
    _description = 'The Dash multi-layer report engine'

    # ── capability probing ───────────────────────────────────────────────
    # Several dimensions come from optional apps (restaurant, loyalty,
    # combos) or from customisations. Anything missing is simply dropped
    # from the dimension list the UI receives.

    @api.model
    def _stored(self, model, fname):
        if model not in self.env:
            return False
        f = self.env[model]._fields.get(fname)
        return bool(f is not None and f.store)

    @api.model
    def _aggregator_field(self):
        Order = self.env['pos.order']
        f = Order._fields.get('aggregator')
        if f is not None and f.store and f.type in ('many2one', 'char', 'selection'):
            return 'aggregator'
        for name, fld in Order._fields.items():
            if 'aggregator' in name and fld.store and \
                    fld.type in ('many2one', 'char', 'selection'):
                return name
        return None

    @api.model
    def _order_type_field(self):
        Order = self.env['pos.order']
        for name in ('pos_order_type_name', 'delivery_type'):
            if self._stored('pos.order', name):
                return name
        for name, fld in Order._fields.items():
            if 'order_type' in name and fld.store and \
                    fld.type in ('many2one', 'char', 'selection'):
                return name
        return None

    @api.model
    def _m2m_rel(self, model, fname):
        """-> (relation table, column pointing at `model`, other column)."""
        f = self.env[model]._fields.get(fname)
        if f is None or f.type != 'many2many':
            return None
        return f.relation, f.column1, f.column2

    # ── dimension registry ───────────────────────────────────────────────

    @api.model
    def _dimensions(self):
        """Every dimension the engine can group by, in menu order.

        ``sql``    - expression yielding the group key
        ``joins``  - join fragments required, applied in JOIN_ORDER
        ``kind``   - how to turn the key into a label
        ``layer``  - 1 = offered as first level, 2 = as second level, 3 = both
        ``fans``   - True when the join multiplies rows (many-to-many), so the
                     UI can warn that values are counted once per value
        """
        has_bdate = self._stored('pos.order', 'business_date')
        dfield = 'o.business_date' if has_bdate else None
        agg = self._aggregator_field()
        otype = self._order_type_field()
        order_team = self._stored('pos.order', 'crm_team_id')
        cfg_team = self._stored('pos.config', 'crm_team_id')

        dims = {}

        def add(key, label, sql, kind, layer, joins=(), model=None, fans=False):
            dims[key] = {
                'key': key, 'label': label, 'sql': sql, 'kind': kind,
                'layer': layer, 'joins': tuple(joins), 'model': model,
                'fans': fans,
            }

        # ---- order level ------------------------------------------------
        if order_team and cfg_team:
            branch_sql = 'COALESCE(o.crm_team_id, c.crm_team_id)'
            branch_joins = ('config',)
        elif order_team:
            branch_sql, branch_joins = 'o.crm_team_id', ()
        elif cfg_team:
            branch_sql, branch_joins = 'c.crm_team_id', ('config',)
        else:
            branch_sql, branch_joins = None, ()
        if branch_sql:
            add('branch', 'الفرع (فريق المبيعات)', branch_sql, 'm2o', 1,
                branch_joins, 'crm.team')

        add('pos', 'نقطة البيع', 'o.config_id', 'm2o', 3, (), 'pos.config')
        add('cost_center', 'مركز التكلفة', 'c.dash_cost_center_id', 'm2o', 3,
            ('config',), 'account.analytic.account')
        if self._stored('pos.config', 'dash_region_id'):
            add('region', 'المنطقة', 'c.dash_region_id', 'm2o', 3,
                ('config',), 'the.dash.region')
        # The POS employee (pos_hr) is the cashier when installed; the user
        # is the same for every employee logged on one till.
        if self._stored('pos.order', 'employee_id'):
            add('cashier', 'الكاشير', 'o.employee_id', 'm2o', 3, (), 'hr.employee')
        else:
            add('cashier', 'الكاشير', 'o.user_id', 'm2o', 3, (), 'res.users')
        add('customer', 'العميل', 'o.partner_id', 'm2o', 3, (), 'res.partner')

        if agg:
            fld = self.env['pos.order']._fields[agg]
            add('source', 'مصدر الطلب', 'o.%s' % agg,
                'm2o' if fld.type == 'many2one' else 'raw', 3, (),
                fld.comodel_name if fld.type == 'many2one' else None)
        if otype:
            fld = self.env['pos.order']._fields[otype]
            add('order_type', 'نوع الطلب', 'o.%s' % otype,
                'm2o' if fld.type == 'many2one' else 'sel', 3, (),
                fld.comodel_name if fld.type == 'many2one' else None)

        # ---- line level -------------------------------------------------
        if self._stored('product.template', 'pos_categ_id'):
            add('category', 'الفئة', 'pt.pos_categ_id', 'm2o', 3,
                ('prod',), 'pos.category')
        elif self._m2m_rel('product.template', 'pos_categ_ids'):
            add('category', 'الفئة', 'pc.categ_id', 'm2o', 3,
                ('prod', 'categ'), 'pos.category')

        # The line stores a variant; the template is what people mean by
        # "product", so that is the headline dimension. The variant stays
        # available for anyone who prices or stocks per variant.
        add('product', 'المنتج', 'pp.product_tmpl_id', 'm2o', 3, ('prod',),
            'product.template')
        add('variant', 'متغير المنتج', 'l.product_id', 'm2o', 3, (),
            'product.product')

        if self._stored('pos.order.line', 'combo_parent_id'):
            add('combo', 'الكومبو', 'cpp.product_tmpl_id', 'm2o', 1,
                ('combo_parent',), 'product.template')
        if self._stored('pos.order.line', 'combo_item_id'):
            add('combo_item', 'الإضافة', 'l.combo_item_id', 'm2o', 3, (),
                self.env['pos.order.line']._fields['combo_item_id'].comodel_name)
        if self._m2m_rel('pos.order.line', 'attribute_value_ids'):
            add('modifier', 'الخيارات (الأتربيوت)', 'avr.attr_id', 'm2o', 3, ('modifier',),
                self.env['pos.order.line']._fields['attribute_value_ids'].comodel_name,
                fans=True)

        # Item, modifier (both modifier modules) or combo item.
        analytics = self.env['the.dash.analytics']
        is_mod = analytics._modifier_sql('l')
        is_child = analytics._combo_child_sql('l')
        add('line_kind', 'نوع السطر',
            "CASE WHEN %s THEN 'modifier' WHEN %s THEN 'combo_item' ELSE 'item' END"
            % (is_mod, is_child), 'line_kind', 3)
        if analytics._modifier_source():
            option = ("NULLIF(l.modifier_option_name, '')"
                      if self._stored('pos.order.line', 'modifier_option_name') else 'NULL')
            # Modifier lines only: the option name, else the modifier product.
            add('modifier_line', 'المودفاير (أسطر الإضافات)',
                "CASE WHEN %s THEN COALESCE(%s, 'p:' || pp.product_tmpl_id::text) END"
                % (is_mod, option), 'modifier_line', 3, ('prod',))

        add('discount', 'الخصم', "to_char(COALESCE(l.discount, 0), 'FM990D00')",
            'discount', 1)

        if self._stored('pos.order.line', 'reward_id'):
            add('promo', 'عرض ترويجي', 'l.reward_id', 'm2o', 1, (),
                self.env['pos.order.line']._fields['reward_id'].comodel_name)
        if self._stored('pos.order.line', 'coupon_id'):
            add('coupon', 'كوبون', 'l.coupon_id', 'm2o', 3, (),
                self.env['pos.order.line']._fields['coupon_id'].comodel_name)

        # ---- time -------------------------------------------------------
        tz = "(o.date_order AT TIME ZONE 'UTC' AT TIME ZONE %(tz)s)"
        # Day-level buckets follow the operational day when the DB has one.
        day = dfield or tz

        add('day', 'يوم', "to_char(%s, 'YYYY-MM-DD')" % day, 'plain', 3)
        add('week', 'أسبوع', "to_char(%s, 'IYYY-\"W\"IW')" % day, 'plain', 3)
        add('month', 'شهر', "to_char(%s, 'YYYY-MM')" % day, 'plain', 3)
        add('quarter', 'ربع', "to_char(%s, 'YYYY') || '-Q' || to_char(%s, 'Q')"
            % (day, day), 'plain', 3)
        add('year', 'عام', "to_char(%s, 'YYYY')" % day, 'plain', 3)
        add('hour', 'ساعات اليوم', "to_char(%s, 'HH24')" % tz, 'hour', 3)
        add('weekday', 'يوم من الأسبوع', "to_char(%s, 'ID')" % day, 'weekday', 3)
        add('monthday', 'يوم من الشهر', "to_char(%s, 'DD')" % day, 'int', 3)
        add('yearday', 'يوم من السنة', "to_char(%s, 'DDD')" % day, 'int', 3)
        add('weekofyear', 'أسبوع من السنة', "to_char(%s, 'IW')" % day, 'week_no', 3)
        add('monthofyear', 'شهر من السنة', "to_char(%s, 'MM')" % day, 'month_no', 3)
        add('quarterofyear', 'ربع من السنة', "'Q' || to_char(%s, 'Q')" % day, 'plain', 3)

        return dims

    # ── joins ────────────────────────────────────────────────────────────

    JOIN_ORDER = ('config', 'prod', 'categ', 'combo_parent', 'modifier')

    @api.model
    def _join_sql(self, name):
        if name == 'config':
            return 'LEFT JOIN pos_config c ON c.id = o.config_id'
        if name == 'prod':
            return ('LEFT JOIN product_product pp ON pp.id = l.product_id '
                    'LEFT JOIN product_template pt ON pt.id = pp.product_tmpl_id')
        if name == 'categ':
            rel = self._m2m_rel('product.template', 'pos_categ_ids')
            if not rel:
                return ''
            table, col_tmpl, col_categ = rel
            # LATERAL keeps one category per product, so the many-to-many
            # cannot duplicate the line and inflate the measures.
            return ('LEFT JOIN LATERAL (SELECT r."%s" AS categ_id FROM "%s" r '
                    'WHERE r."%s" = pt.id ORDER BY r."%s" LIMIT 1) pc ON TRUE'
                    % (col_categ, table, col_tmpl, col_categ))
        if name == 'combo_parent':
            # Two hops: the parent line, then its variant, so the combo can be
            # reported at template level like every other product dimension.
            return ('LEFT JOIN pos_order_line cpl ON cpl.id = l.combo_parent_id '
                    'LEFT JOIN product_product cpp ON cpp.id = cpl.product_id')
        if name == 'modifier':
            rel = self._m2m_rel('pos.order.line', 'attribute_value_ids')
            if not rel:
                return ''
            table, col_line, col_attr = rel
            # This one legitimately fans out: a line with three modifiers is
            # counted under each of them. Flagged via dims[...]['fans'].
            return ('LEFT JOIN (SELECT "%s" AS line_id, "%s" AS attr_id FROM "%s") avr '
                    'ON avr.line_id = l.id' % (col_line, col_attr, table))
        return ''

    # ── measures ─────────────────────────────────────────────────────────

    PAID_STATES = ('paid', 'done', 'invoiced')

    @api.model
    def _measure_sql(self):
        """SQL for every summable measure, evaluated over the `base` CTE.

        The identities that hold by construction:
            gross - discount = net
            net   + tax      = net_incl
            net   - cost     = profit
        """
        cost = ('COALESCE(b.total_cost, 0)'
                if self._stored('pos.order.line', 'total_cost') else '0')
        sale = "b.state <> 'cancel'"
        canc = "b.state = 'cancel'"
        disc = 'COALESCE(b.discount, 0)'
        return [
            ('gross', 'SUM(b.qty * b.price_unit) FILTER (WHERE %s)' % sale),
            ('discount', 'SUM(b.qty * b.price_unit * %s / 100.0) FILTER (WHERE %s)'
             % (disc, sale)),
            ('net', 'SUM(b.price_subtotal) FILTER (WHERE %s)' % sale),
            ('tax', 'SUM(b.price_subtotal_incl - b.price_subtotal) FILTER (WHERE %s)'
             % sale),
            ('net_incl', 'SUM(b.price_subtotal_incl) FILTER (WHERE %s)' % sale),
            ('cost', 'SUM(%s) FILTER (WHERE %s)' % (cost, sale)),
            ('net_qty', 'SUM(b.qty) FILTER (WHERE %s)' % sale),
            ('refund_qty', 'SUM(-b.qty) FILTER (WHERE %s AND b.qty < 0)' % sale),
            ('refund_amount',
             'SUM(-b.price_subtotal_incl) FILTER (WHERE %s AND b.qty < 0)' % sale),
            ('cancel_amount', 'SUM(b.price_subtotal_incl) FILTER (WHERE %s)' % canc),
            ('cancel_qty', 'SUM(b.qty) FILTER (WHERE %s)' % canc),
        ]

    @api.model
    def _order_measure_sql(self):
        """Measures that belong to the order, not the line.

        Computed over the DISTINCT orders touching each group, so a two-line
        order contributes its tip and its guest count exactly once per group.
        """
        visitors = ('SUM(x.visitors)'
                    if self._stored('pos.order', 'customer_count') else 'NULL')
        tips = ('SUM(x.tips)'
                if self._stored('pos.order', 'tip_amount') else 'NULL')
        return [
            ('orders', "COUNT(*) FILTER (WHERE x.total >= 0 AND x.state <> 'cancel')"),
            ('customers', 'COUNT(DISTINCT x.partner_id)'),
            ('visitors', visitors),
            ('tips', tips),
        ]

    # ── query builder ────────────────────────────────────────────────────

    @api.model
    def prepare(self, config_ids, date_from, date_to, group1='pos', group2='none',
                team_ids=None, limit=2000, local_from=None, local_to=None):
        """Build the statement without running it.

        Kept separate from :meth:`build` so the controller can show the exact
        SQL when the database rejects it. (It cannot be stashed on ``self``:
        Odoo models define ``__slots__``, so a recordset takes no ad-hoc
        attributes.)

        ``date_from``/``date_to`` are naive datetimes in the same convention
        the rest of the module uses (what the ORM stores, i.e. UTC).
        """
        dims = self._dimensions()
        if group1 not in dims:
            group1 = 'pos' if 'pos' in dims else sorted(dims)[0]
        if group2 == group1 or group2 not in dims:
            group2 = None

        d1 = dims[group1]
        d2 = dims[group2] if group2 else None

        joins = []
        for name in self.JOIN_ORDER:
            if name in d1['joins'] or (d2 and name in d2['joins']):
                sql = self._join_sql(name)
                if sql:
                    joins.append(sql)

        params = {
            'config_ids': tuple(config_ids) or (0,),
            'dt_from': date_from,
            'dt_to': date_to,
            'tz': self.env.context.get('dash_tz') or self.env.user.tz or 'UTC',
            'states': tuple(self.PAID_STATES) + ('cancel',),
            'limit': limit,
        }

        where = ['o.config_id IN %(config_ids)s', 'o.state IN %(states)s']
        if self._stored('pos.order', 'business_date'):
            where.append('o.business_date BETWEEN %(d_from)s AND %(d_to)s')
            # Local (user timezone) days, not the UTC bounds.
            params['d_from'] = local_from or date_from.date()
            params['d_to'] = local_to or date_to.date()
        else:
            where.append('o.date_order BETWEEN %(dt_from)s AND %(dt_to)s')
        if team_ids and self._stored('pos.order', 'crm_team_id'):
            where.append('o.crm_team_id IN %(team_ids)s')
            params['team_ids'] = tuple(team_ids)

        k2_sql = d2['sql'] if d2 else 'NULL'
        measures = self._measure_sql()
        order_measures = self._order_measure_sql()

        query = """
WITH base AS (
    SELECT {k1} AS k1,
           {k2} AS k2,
           o.id  AS order_id,
           o.state AS state,
           o.amount_total AS total,
           o.partner_id AS partner_id,
           {visitors_col} AS visitors,
           {tips_col} AS tips,
           l.qty, l.price_unit, l.discount,
           l.price_subtotal, l.price_subtotal_incl{cost_col}
    FROM pos_order_line l
    JOIN pos_order o ON o.id = l.order_id
    {joins}
    WHERE {where}
),
line_agg AS (
    SELECT k1, k2, {line_measures}
    FROM base b
    GROUP BY k1, k2
),
order_rows AS (
    SELECT k1, k2, order_id,
           MAX(state) AS state, MAX(total) AS total, MAX(partner_id) AS partner_id,
           MAX(visitors) AS visitors, MAX(tips) AS tips
    FROM base
    GROUP BY k1, k2, order_id
),
order_agg AS (
    SELECT k1, k2, {order_measures}
    FROM order_rows x
    GROUP BY k1, k2
)
SELECT la.k1, la.k2, {measure_cols}, {order_cols}
FROM line_agg la
LEFT JOIN order_agg oa
       ON la.k1 IS NOT DISTINCT FROM oa.k1
      AND la.k2 IS NOT DISTINCT FROM oa.k2
ORDER BY la.k1, COALESCE(la.net, 0) DESC NULLS LAST
LIMIT %(limit)s
""".format(
            k1=d1['sql'],
            k2=k2_sql,
            joins='\n    '.join(joins),
            where='\n      AND '.join(where),
            visitors_col=('o.customer_count'
                          if self._stored('pos.order', 'customer_count') else 'NULL'),
            tips_col=('o.tip_amount'
                      if self._stored('pos.order', 'tip_amount') else 'NULL'),
            cost_col=(', l.total_cost AS total_cost'
                      if self._stored('pos.order.line', 'total_cost') else ''),
            line_measures=', '.join('%s AS %s' % (sql, name) for name, sql in measures),
            order_measures=', '.join('%s AS %s' % (sql, name)
                                     for name, sql in order_measures),
            measure_cols=', '.join('COALESCE(la.%s, 0) AS %s' % (n, n)
                                   for n, _ in measures),
            order_cols=', '.join('oa.%s AS %s' % (n, n) for n, _ in order_measures),
        )
        return {
            'query': query,
            'params': params,
            'd1': d1,
            'd2': d2,
            'measures': measures,
            'order_measures': order_measures,
            'limit': limit,
        }

    @api.model
    def build(self, config_ids, date_from, date_to, group1='pos', group2='none',
              team_ids=None, limit=2000, local_from=None, local_to=None):
        """Run the two-layer report and shape it for the UI."""
        plan = self.prepare(config_ids, date_from, date_to, group1, group2,
                            team_ids, limit, local_from, local_to)
        self.env.cr.execute(plan['query'], plan['params'])
        rows = self.env.cr.dictfetchall()
        result = self._shape(rows, plan['d1'], plan['d2'],
                             plan['measures'], plan['order_measures'])
        result['truncated'] = len(rows) >= plan['limit']
        return result

    # ── label resolution + shaping ───────────────────────────────────────

    @api.model
    def _resolve_labels(self, dim, keys):
        """Turn raw group keys into display labels, one query per dimension."""
        kind = dim['kind']
        out = {}
        if kind == 'm2o':
            ids = [int(k) for k in keys if k not in (None, '')]
            if ids and dim['model'] and dim['model'] in self.env:
                recs = self.env[dim['model']].sudo().browse(ids).exists()
                for r in recs:
                    out[r.id] = r.display_name
            return lambda k: (out.get(int(k)) if k not in (None, '') else None) \
                or ('غير محدد' if k in (None, '') else '#%s' % k)
        if kind == 'sel':
            sel = {}
            try:
                fname = dim['sql'].split('.')[-1]
                sel = dict(self.env['pos.order']._fields[fname]
                           ._description_selection(self.env))
            except Exception:
                sel = {}
            return lambda k: sel.get(k, k) if k else 'غير محدد'
        if kind == 'line_kind':
            labels = {'item': 'صنف رئيسي', 'modifier': 'مودفاير', 'combo_item': 'عنصر كومبو'}
            return lambda k: labels.get(k, 'غير محدد')
        if kind == 'modifier_line':
            tmpl_ids = [int(k[2:]) for k in keys if isinstance(k, str) and k.startswith('p:')]
            names = {}
            if tmpl_ids:
                names = {t.id: t.display_name for t in
                         self.env['product.template'].sudo().browse(tmpl_ids).exists()}

            def mod_label(k):
                if not k:
                    return 'ليست مودفاير'
                if isinstance(k, str) and k.startswith('p:'):
                    return names.get(int(k[2:]), k)
                return k
            return mod_label
        if kind == 'weekday':
            return lambda k: AR_WEEKDAYS.get(str(k), 'غير محدد')
        if kind == 'month_no':
            return lambda k: AR_MONTHS.get(str(k), 'غير محدد')
        if kind == 'hour':
            return lambda k: ('%s:00' % k) if k is not None else 'غير محدد'
        if kind == 'week_no':
            return lambda k: ('الأسبوع %s' % int(k)) if k is not None else 'غير محدد'
        if kind == 'int':
            return lambda k: str(int(k)) if k is not None else 'غير محدد'
        if kind == 'discount':
            def disc_label(k):
                try:
                    v = float(k or 0)
                except (TypeError, ValueError):
                    return 'غير محدد'
                if v == 0:
                    return 'بدون خصم'
                # 15.00 -> "15", 12.50 -> "12.5"
                return 'خصم %s%%' % ('%g' % round(v, 2))
            return disc_label
        return lambda k: (str(k) if k not in (None, '') else 'غير محدد')

    @api.model
    def _shape(self, rows, d1, d2, measures, order_measures):
        """Fold the flat SQL result into the two-level tree the UI renders."""
        names = [n for n, _ in measures] + [n for n, _ in order_measures]

        lbl1 = self._resolve_labels(d1, {r['k1'] for r in rows})
        lbl2 = self._resolve_labels(d2, {r['k2'] for r in rows}) if d2 else None

        def blank():
            return dict((n, 0.0) for n in names)

        def add(acc, row):
            for n in names:
                v = row.get(n)
                if v is None:
                    continue
                acc[n] = (acc.get(n) or 0.0) + float(v)

        tree, totals = {}, blank()
        for r in rows:
            node = tree.setdefault(r['k1'], {
                'key': str(r['k1']), 'label': lbl1(r['k1']),
                'vals': blank(), 'children': {},
            })
            add(node['vals'], r)
            add(totals, r)
            if d2:
                ch = node['children'].setdefault(r['k2'], {
                    'key': str(r['k2']), 'label': lbl2(r['k2']), 'vals': blank(),
                })
                add(ch['vals'], r)

        def derive(v, base_net=None):
            """Ratios and differences that must not be summed, only recomputed."""
            net = v.get('net') or 0.0
            v['profit'] = round(net - (v.get('cost') or 0.0), 2)
            orders = v.get('orders') or 0
            visitors = v.get('visitors') or 0
            v['avg_order'] = round(net / orders, 2) if orders else 0.0
            v['avg_visitor'] = round(net / visitors, 2) if visitors else 0.0
            v['pct'] = round(net / base_net * 100, 1) if base_net else 0.0
            for n in names:
                if isinstance(v.get(n), float):
                    v[n] = round(v[n], 2)
            return v

        total_net = totals.get('net') or 0.0
        out = []
        for node in tree.values():
            row = {'key': node['key'], 'label': node['label'],
                   'children': []}
            row.update(derive(node['vals'], total_net))
            node_net = node['vals'].get('net') or 0.0
            for ch in node['children'].values():
                child = {'key': ch['key'], 'label': ch['label']}
                child.update(derive(ch['vals'], node_net))
                row['children'].append(child)
            row['children'].sort(key=lambda c: -(c.get('net') or 0))
            out.append(row)

        time_kinds = ('plain', 'int', 'hour', 'weekday', 'month_no', 'week_no')
        if d1['kind'] in time_kinds:
            out.sort(key=lambda r: r['key'])
        else:
            out.sort(key=lambda r: -(r.get('net') or 0))

        unavailable = []
        if not self._stored('pos.order', 'customer_count'):
            unavailable += ['visitors', 'avg_visitor']
        if not self._stored('pos.order', 'tip_amount'):
            unavailable.append('tips')
        if not self._stored('pos.order.line', 'total_cost'):
            unavailable += ['cost', 'profit']

        return {
            'group1': d1['key'],
            'group2': d2['key'] if d2 else 'none',
            'fans': bool(d1['fans'] or (d2 and d2['fans'])),
            'unavailable': unavailable,
            'rows': out,
            'totals': derive(totals, total_net),
        }

    # ── what the UI is allowed to offer ──────────────────────────────────

    @api.model
    def available_dimensions(self):
        dims = self._dimensions()
        return {
            'layer1': [{'key': d['key'], 'label': d['label'], 'fans': d['fans']}
                       for d in dims.values() if d['layer'] in (1, 3)],
            'layer2': [{'key': d['key'], 'label': d['label'], 'fans': d['fans']}
                       for d in dims.values() if d['layer'] in (2, 3)],
        }
