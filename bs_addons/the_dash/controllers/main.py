"""The Dash - point of sale analytics (Odoo 19).

Two deliberate differences from an ordinary Odoo dashboard:

  * **One entry point, every branch.** There is no shop picker and no
    per-shop token. ``/thedash`` opens with every point of sale the user is
    allowed to see, and narrowing happens inside the page. Access is decided
    by the user's group, not by knowing a URL.
  * **Nothing runs until asked.** The page loads empty. The dashboard and the
    multi-layer report each have their own refresh button and their own
    route, so neither can stall or break the other, and both report how long
    they took.
"""

import io
import json
import csv
import logging
import time
from datetime import datetime, timedelta, date

import pytz

from odoo import http, fields as ofields
from odoo.http import request
from odoo.tools import html_escape

_logger = logging.getLogger(__name__)


class TheDashController(http.Controller):

    # -- Access and scope ----------------------------------------------------

    def _check_access(self):
        user = request.env.user
        return (
            user.has_group('the_dash.group_the_dash_viewer')
            or user.has_group('the_dash.group_the_dash_manager')
            or user.has_group('point_of_sale.group_pos_manager')
            or user._is_admin()
        )

    def _sees_everything(self):
        """Managers ignore the per-user branch list and see the SQL on error."""
        user = request.env.user
        return (
            user.has_group('the_dash.group_the_dash_manager')
            or user.has_group('point_of_sale.group_pos_manager')
            or user._is_admin()
        )

    def _denied(self):
        html = '''<!DOCTYPE html><html dir="rtl"><head><meta charset="UTF-8"/>
        <title>لوحة البيانات · Resta</title><style>
        body{font-family:"IBM Plex Sans Arabic", system-ui,"Segoe UI",Tahoma,sans-serif;display:flex;align-items:center;
             justify-content:center;min-height:100vh;background:#0d0f14;color:#e8eaf2;margin:0}
        .box{text-align:center;padding:40px}.m{font-size:2.2rem;color:#d4af37;letter-spacing:.2em}
        .msg{font-size:1rem;margin-top:18px;color:#8b93a7;line-height:1.9}
        </style></head><body><div class="box">
        <div class="m">THE DASH</div>
        <div style="color:#8794a6;letter-spacing:.06em;margin-top:8px;font-size:.86rem">
          by <b style="color:#FF7443">BASIC</b> Solution</div>
        <h2 style="margin-top:24px;font-weight:600">لا تملك صلاحية الدخول</h2>
        <p class="msg">هذه اللوحة متاحة لأعضاء مجموعة <strong>The Dash / Viewer</strong>
        ولمدراء نقاط البيع.<br/>تواصل مع مدير النظام لإضافتك.</p>
        </div></body></html>'''
        return request.make_response(html, status=403,
            headers=[('Content-Type', 'text/html; charset=utf-8')])

    def _allowed_configs(self):
        """Every point of sale this user is permitted to see.

        Managers see all. For everyone else the list on the user record is
        the ceiling; an empty list means no restriction was configured, which
        keeps existing installs working after an upgrade.
        """
        Config = request.env['pos.config'].sudo()
        if self._sees_everything():
            return Config.search([], order='name')
        allowed = request.env.user.sudo().dash_config_ids
        if not allowed:
            return Config.search([], order='name')
        return Config.search([('id', 'in', allowed.ids)], order='name')

    def _configs(self, config_ids=None):
        """Scope for one request: permitted branches, optionally narrowed.

        The narrowing is an *intersection*, never a replacement. A viewer who
        edits config_ids in the URL cannot reach a branch that is not theirs.
        """
        allowed = self._allowed_configs()
        if not config_ids:
            return allowed
        try:
            ids = {int(i) for i in str(config_ids).split(',') if i.strip()}
        except ValueError:
            return allowed
        narrowed = allowed.filtered(lambda c: c.id in ids)
        return narrowed or allowed

    def _scope_label(self, configs, config_ids=None):
        total = len(self._allowed_configs())
        if not config_ids or len(configs) >= total:
            return 'كل الفروع'
        if len(configs) == 1:
            return configs.display_name
        return '%d فروع' % len(configs)

    def _branches(self):
        """Points of sale for the scope panel - permitted ones only.

        Ordered and read on ``name``: ``display_name`` is computed, not a
        column, so the database cannot sort on it.
        """
        return [{'id': c.id, 'name': c.display_name}
                for c in self._allowed_configs()]

    def _teams(self):
        if 'crm.team' not in request.env:
            return []
        teams = request.env['crm.team'].sudo().search([], order='name')
        return [{'id': t.id, 'name': t.display_name} for t in teams]

    # -- Domain / field helpers ----------------------------------------------

    PAID_STATES = ['paid', 'done', 'invoiced']

    def _date_field(self):
        """Operational day field when the DB has one, else the raw datetime."""
        return 'business_date' if 'business_date' in request.env['pos.order']._fields \
            else 'date_order'

    def _local_day(self, dt):
        """Business (operational) date of a naive UTC datetime.

        Same rule as pos_business_day: local time in the company timezone,
        and anything before the business day start hour belongs to the day
        before.
        """
        local = pytz.utc.localize(dt).astimezone(self._user_tz()).replace(tzinfo=None)
        return (local - timedelta(hours=self._start_hour())).date()

    def _order_domain(self, configs, dt_from, dt_to, team_ids=None, prefix=''):
        Order = request.env['pos.order']
        dom = [(prefix + 'config_id', 'in', configs.ids)]
        if 'business_date' in Order._fields:
            dom += [(prefix + 'business_date', '>=', self._local_day(dt_from)),
                    (prefix + 'business_date', '<=', self._local_day(dt_to))]
        else:
            dom += [(prefix + 'date_order', '>=', dt_from),
                    (prefix + 'date_order', '<=', dt_to)]
        if team_ids and 'crm_team_id' in Order._fields:
            dom.append((prefix + 'crm_team_id', 'in', team_ids))
        return dom

    def _paid_domain(self, configs, dt_from, dt_to, team_ids=None, prefix=''):
        return self._order_domain(configs, dt_from, dt_to, team_ids, prefix) \
            + [(prefix + 'state', 'in', self.PAID_STATES)]

    def _aggregator_field(self):
        Order = request.env['pos.order']
        f = Order._fields.get('aggregator')
        if f is not None and f.type in ('many2one', 'char', 'selection'):
            return 'aggregator'
        for fname, fld in Order._fields.items():
            if 'aggregator' in fname and fld.type in ('many2one', 'char', 'selection'):
                return fname
        return None

    def _order_type_field(self):
        """-> (field name, selection label map or None)."""
        Order = request.env['pos.order']
        if 'pos_order_type_name' in Order._fields:
            return 'pos_order_type_name', None
        if 'delivery_type' in Order._fields:
            try:
                sel = dict(Order._fields['delivery_type']._description_selection(request.env))
            except Exception:
                s = Order._fields['delivery_type'].selection
                sel = dict(s) if isinstance(s, (list, tuple)) else {}
            return 'delivery_type', sel
        return None, None

    def _apply_team_filter(self, configs, team_ids):
        """Filter by sales team, on the order when possible else on the config."""
        if not team_ids:
            return configs, None
        if 'crm_team_id' in request.env['pos.order']._fields:
            return configs, team_ids
        if 'crm_team_id' in request.env['pos.config']._fields:
            filtered = configs.filtered(lambda c: c.crm_team_id.id in team_ids)
            return (filtered or configs), None
        return configs, None

    @staticmethod
    def _m2o(val):
        """_read_group returns a recordset for many2one group values."""
        if not val:
            return 0, None
        return val.id, val.display_name

    def _localize(self, val):
        """Datetime granularity group values come back as naive UTC."""
        if isinstance(val, datetime) and val.tzinfo is None:
            return ofields.Datetime.context_timestamp(request.env.user, val)
        return val

    # -- Date range ----------------------------------------------------------

    # -- Business day (pos_business_day) -------------------------------------
    # The Dash follows the same clock as pos_business_day: the company
    # timezone (falling back to the user's) and the company's "Business Day
    # Start Hour". With a 06:00 start, an order at 02:00 belongs to the day
    # before, "today" at 02:00 is still yesterday's business day, and the
    # hourly chart runs 06:00 -> 05:00. Without the module the start hour is
    # 0 and everything falls back to plain calendar days.

    def _company(self):
        return request.env.company.sudo()

    def _start_hour(self):
        company = self._company()
        if 'business_day_start_hour' in company._fields:
            return float(company.business_day_start_hour or 0.0)
        return 0.0

    def _business_info(self):
        start = self._start_hour()
        h, m = int(start), int(round((start - int(start)) * 60))
        return {
            'start_hour': start,
            'start_label': '%02d:%02d' % (h, m),
            'tz': self._user_tz().zone,
            'source': 'pos_business_day'
                      if 'business_day_start_hour' in self._company()._fields else None,
        }

    def _user_tz(self):
        """Timezone of the business day: the company's, as pos_business_day
        uses it, else the user's."""
        name = self._company().partner_id.tz or request.env.user.tz or 'UTC'
        try:
            return pytz.timezone(name)
        except pytz.UnknownTimeZoneError:
            return pytz.utc

    def _bounds(self, d_from, d_to):
        """Business days -> naive UTC datetimes, as stored by the ORM.

        A business day runs from the start hour to the same hour the next
        calendar day, in the company timezone.
        """
        tz = self._user_tz()
        shift = timedelta(hours=self._start_hour())
        start = tz.localize(datetime.combine(d_from, datetime.min.time()) + shift)
        end = tz.localize(datetime.combine(d_to + timedelta(days=1),
                                           datetime.min.time()) + shift) \
            - timedelta(microseconds=1)
        return (start.astimezone(pytz.utc).replace(tzinfo=None),
                end.astimezone(pytz.utc).replace(tzinfo=None))

    def _date_range(self, period, date_from=None, date_to=None):
        # Today's *business* day: at 02:00 with a 06:00 start it is still
        # yesterday's shift.
        now_local = datetime.now(pytz.utc).astimezone(self._user_tz()).replace(tzinfo=None)
        today = (now_local - timedelta(hours=self._start_hour())).date()
        if period == 'custom' and date_from and date_to:
            try:
                return (datetime.strptime(date_from, '%Y-%m-%d').date(),
                        datetime.strptime(date_to, '%Y-%m-%d').date())
            except ValueError:
                pass
        if period == 'yesterday':
            y = today - timedelta(days=1)
            return y, y
        if period == 'week':
            return today - timedelta(days=7), today
        if period == 'month':
            return today - timedelta(days=30), today
        return today, today

    # -- Page ----------------------------------------------------------------

    @http.route('/thedash', auth='user', type='http', website=False)
    def dash_page(self, **kw):
        if not self._check_access():
            return self._denied()
        config = json.dumps({
            'dimensions': self._dimensions(),
            'measures': self.MEASURE_COLUMNS,
            'default_columns': self.DEFAULT_COLUMNS,
            'branches': self._branches(),
            'teams': self._teams(),
            'restricted': not self._sees_everything(),
        }, ensure_ascii=False)
        html = PAGE_TEMPLATE.replace('__CONFIG__', config.replace('</', '<\\/'))
        return request.make_response(html, headers=[
            ('Content-Type', 'text/html; charset=utf-8'),
            ('Cache-Control', 'no-store'),
        ])

    def _json(self, data):
        return request.make_response(
            json.dumps(data, default=str),
            headers=[('Content-Type', 'application/json; charset=utf-8'),
                     ('Cache-Control', 'no-store')])

    # -- Dashboard data ------------------------------------------------------

    @http.route('/thedash/data', auth='user', type='http', csrf=False)
    def dash_data(self, period='today', date_from=None, date_to=None,
                  config_ids=None, team_ids=None, **kw):
        if not self._check_access():
            return self._json({'error': 'access_denied'})
        configs = self._configs(config_ids)
        if not configs:
            return self._json({'error': 'no_pos'})

        started = time.monotonic()
        timings = {}

        def step(name, fn):
            t0 = time.monotonic()
            try:
                return fn()
            finally:
                timings[name] = round((time.monotonic() - t0) * 1000)

        try:
            team_list = [int(i) for i in str(team_ids).split(',') if i.strip()] \
                if team_ids else []
        except ValueError:
            team_list = []
        configs, tids = self._apply_team_filter(configs, team_list)

        d_from, d_to = self._date_range(period, date_from, date_to)
        dt_from, dt_to = self._bounds(d_from, d_to)
        scope = self._analytics_scope(configs, d_from, d_to, tids)
        Analytics = request.env['the.dash.analytics'].sudo()

        sales = step('items', lambda: Analytics.items_and_modifiers(scope))
        pay_rows = step('payments', lambda: Analytics.payment_rows(scope))
        closing = step('closing', lambda: Analytics.closing(scope))
        activity = step('hourly', lambda: Analytics.activity(scope))

        data = {
            'scope': self._scope_label(configs, config_ids),
            'period_label': '%s -> %s' % (d_from, d_to),
            'days': (d_to - d_from).days + 1,
            'business_day': self._business_info(),
            'kpis': step('kpis', lambda: self._kpis(configs, dt_from, dt_to, tids)),
            # Hours in business-day order (start hour first), by order type.
            'hourly_sales': activity['hours'],
            'activity': activity,
            'pay_hours': step('pay_hours', lambda: Analytics.payments_by_hour(scope)),
            'shortages': Analytics.shortage_charts(closing, scope),
            # Items: modifiers apart, combo items folded into their combo.
            'top_products': sales['items'][:100],
            'categories': self._categories(sales['items']),
            'sales': sales,
            'collections': Analytics.collections(scope, pay_rows),
            'breakdowns': step('breakdowns',
                               lambda: Analytics.breakdowns(scope, pay_rows, closing)),
            'closing': closing,
            'payments': step('journals',
                             lambda: self._payments(configs, dt_from, dt_to, tids)),
            'aggregators': step('aggregators',
                                lambda: self._aggregators(configs, dt_from, dt_to, tids)),
            'order_types': step('order_types',
                                lambda: self._order_types(configs, dt_from, dt_to, tids)),
            'kitchen': step('kitchen', lambda: self._kitchen_data(scope, configs, dt_from,
                                                                  dt_to, tids, kw)),
            'profit': step('profit', lambda: request.env['the.dash.profit'].sudo()
                           .profitability(scope)),
            'commissions': step('commissions', lambda: Analytics.app_commissions(scope)),
        }
        if data.get('profit'):
            data['menu'] = request.env['the.dash.profit'].sudo().menu_engineering(
                data['profit']['items'])
        data['duration_ms'] = round((time.monotonic() - started) * 1000)
        data['timings'] = timings
        data['last_updated'] = ofields.Datetime.context_timestamp(
            request.env.user, datetime.now()).strftime('%Y-%m-%d %H:%M:%S')
        return self._json(data)

    # -- Measure catalogue ---------------------------------------------------
    # (key, Arabic label, kind) - kind drives formatting and export width.
    #   money = 2 decimals, qty = number, int = count, pct = percentage
    MEASURE_COLUMNS = [
        ('gross',         'إجمالي المبيعات',        'money'),
        ('discount',      'مبلغ الخصم',              'money'),
        ('net',           'صافي المبيعات',           'money'),
        ('tax',           'الضرائب',                 'money'),
        ('net_incl',      'صافي المبيعات مع الضريبة', 'money'),
        ('cost',          'التكلفة',                 'money'),
        ('profit',        'الربح',                   'money'),
        ('orders',        'عدد الطلبات',             'int'),
        ('customers',     'عدد العملاء',             'int'),
        ('visitors',      'عدد الزوار',              'int'),
        ('avg_order',     'متوسط الطلب',             'money'),
        ('avg_visitor',   'المتوسط للزائر',          'money'),
        ('net_qty',       'صافي الكمية',             'qty'),
        ('refund_qty',    'كمية المرتجع',            'qty'),
        ('refund_amount', 'مبلغ الإرجاع',            'money'),
        ('cancel_amount', 'مبلغ الإلغاء',            'money'),
        ('cancel_qty',    'كمية الإلغاء',            'qty'),
        ('tips',          'الإكراميات',              'money'),
        ('pct',           '% من الصافي',             'pct'),
    ]

    # Shown by default; the rest are behind the column picker.
    DEFAULT_COLUMNS = ['net', 'orders', 'avg_order', 'discount', 'tax',
                       'profit', 'net_qty', 'pct']

    # -- KPIs (grouped queries, no recordset loading) ------------------------

    def _kpis(self, configs, dt_from, dt_to, team_ids=None):
        Order = request.env['pos.order'].sudo()

        def totals(dom):
            res = Order._read_group(dom, [], ['amount_total:sum', '__count'])
            if not res:
                return 0.0, 0
            amount, count = res[0]
            return (amount or 0.0), (count or 0)

        def customers(dom):
            return len(Order._read_group(
                dom + [('partner_id', '!=', False)], ['partner_id'], []))

        paid = self._paid_domain(configs, dt_from, dt_to, team_ids)
        total_sales, total_orders = totals(paid)
        total_customers = customers(paid)
        avg_order = total_sales / total_orders if total_orders else 0.0

        refund_amount, refund_count = totals(paid + [('amount_total', '<', 0)])
        cancel_amount, cancel_count = totals(
            self._order_domain(configs, dt_from, dt_to, team_ids) + [('state', '=', 'cancel')])

        local_from, local_to = self._local_day(dt_from), self._local_day(dt_to)
        days = (local_to - local_from).days + 1
        prev_to_d = local_from - timedelta(days=1)
        prev_from_d = prev_to_d - timedelta(days=days - 1)
        prev_dom = self._paid_domain(configs, *self._bounds(prev_from_d, prev_to_d),
                                     team_ids=team_ids)
        prev_sales, prev_orders = totals(prev_dom)
        prev_customers = customers(prev_dom)
        prev_avg = prev_sales / prev_orders if prev_orders else 0.0

        def growth(cur, prv):
            return round((cur - prv) / prv * 100, 1) if prv else 0

        return {
            'total_sales': round(total_sales, 2),
            'total_orders': total_orders,
            'total_customers': total_customers,
            'avg_order': round(avg_order, 2),
            'sales_growth': growth(total_sales, prev_sales),
            'orders_growth': growth(total_orders, prev_orders),
            'customers_growth': growth(total_customers, prev_customers),
            'avg_growth': growth(avg_order, prev_avg),
            'prev_sales': round(prev_sales, 2),
            'prev_orders': prev_orders,
            'prev_customers': prev_customers,
            'prev_avg': round(prev_avg, 2),
            'prev_label': '%s -> %s' % (prev_from_d, prev_to_d),
            'refund_count': refund_count,
            'refund_amount': round(abs(refund_amount), 2),
            'cancel_count': cancel_count,
            'cancel_amount': round(abs(cancel_amount), 2),
        }

    # -- Hourly sales (1 grouped query, timezone aware) ----------------------

    # Hourly sales now come from the.dash.analytics.activity(): one SQL pass,
    # hours in the business-day timezone, in business-day order.
    #
    # The old version grouped with _read_group('date_order:hour'). In Odoo 17+
    # that already returns the hour in the context timezone, and the value was
    # then converted to the user's timezone a second time - every bar sat
    # three hours late in Riyadh (a 04:00 close showed as 07:00).

    # -- Products / categories (1 grouped query, reused twice) ---------------

    def _product_groups(self, configs, dt_from, dt_to, team_ids=None):
        """Sales per product, rolled up to the **product template**.

        Order lines point at a variant (``product.product``), but a report
        that lists "T-shirt (Red, L)" and "T-shirt (Blue, M)" as separate
        products is not what anyone means by "top products". The grouped read
        still runs on the variant - that is the only column on the line - and
        the variants are folded onto their template afterwards, which costs
        one extra prefetched read rather than another aggregation.
        """
        rows = request.env['pos.order.line'].sudo()._read_group(
            self._paid_domain(configs, dt_from, dt_to, team_ids, prefix='order_id.'),
            ['product_id'], ['qty:sum', 'price_subtotal_incl:sum'])

        by_template = {}
        for product, qty, revenue in rows:
            if not product:
                continue
            template = product.product_tmpl_id
            source = template or product
            key = ('t', template.id) if template else ('v', product.id)
            entry = by_template.setdefault(key, {
                'name': source.display_name,
                'category': self._pos_category_name(source),
                'qty': 0.0,
                'revenue': 0.0,
                'variants': 0,
            })
            entry['qty'] += qty or 0.0
            entry['revenue'] += revenue or 0.0
            entry['variants'] += 1

        out = []
        for entry in by_template.values():
            entry['qty'] = round(entry['qty'], 2)
            entry['revenue'] = round(entry['revenue'], 2)
            out.append(entry)
        out.sort(key=lambda p: -p['revenue'])
        return out

    @staticmethod
    def _pos_category_name(record):
        """POS category of a template (or variant), not the internal category."""
        if 'pos_categ_ids' in record._fields and record.pos_categ_ids:
            return record.pos_categ_ids[0].name
        if 'pos_categ_id' in record._fields and record.pos_categ_id:
            return record.pos_categ_id.name
        return 'أخرى'

    @staticmethod
    def _top_products(prod_rows):
        return prod_rows[:100]

    @staticmethod
    def _categories(prod_rows):
        cats = {}
        for p in prod_rows:
            cats[p['category']] = cats.get(p['category'], 0.0) + p['revenue']
        total = sum(cats.values()) or 1
        return [{'name': k, 'sales': round(v, 2), 'pct': round(v / total * 100, 1)}
                for k, v in sorted(cats.items(), key=lambda x: -x[1])][:8]

    # -- Payment methods, merged by journal (1 grouped query) ----------------

    def _payments(self, configs, dt_from, dt_to, team_ids=None):
        rows = request.env['pos.payment'].sudo()._read_group(
            self._paid_domain(configs, dt_from, dt_to, team_ids, prefix='pos_order_id.'),
            ['payment_method_id'], ['amount:sum'])
        methods = {}
        for method, amount in rows:
            if not method:
                continue
            journal = method.journal_id
            key = ('j', journal.id) if journal else ('m', method.id)
            name = journal.display_name if journal else method.display_name
            entry = methods.setdefault(key, {'name': name, 'amount': 0.0})
            entry['amount'] += amount or 0.0
        total = sum(e['amount'] for e in methods.values()) or 1
        return [{'name': e['name'], 'amount': round(e['amount'], 2),
                 'pct': round(e['amount'] / total * 100, 1)}
                for e in sorted(methods.values(), key=lambda x: -x['amount'])]

    # -- Aggregator apps / order types (1 grouped query each) ----------------

    def _simple_breakdown(self, configs, dt_from, dt_to, team_ids, fname,
                          sel=None, blank_label='غير محدد'):
        rows = request.env['pos.order'].sudo()._read_group(
            self._paid_domain(configs, dt_from, dt_to, team_ids),
            [fname], ['amount_total:sum', '__count'])
        agg = {}
        for val, amount, count in rows:
            if hasattr(val, '_name'):
                name = val.display_name if val else blank_label
            elif not val:
                name = blank_label
            else:
                name = sel.get(val, val) if sel else val
            entry = agg.setdefault(name, {'name': name, 'sales': 0.0, 'orders': 0})
            entry['sales'] += amount or 0.0
            entry['orders'] += count or 0
        total = sum(e['sales'] for e in agg.values()) or 1
        result = sorted(agg.values(), key=lambda x: -x['sales'])
        for r in result:
            r['sales'] = round(r['sales'], 2)
            r['pct'] = round(r['sales'] / total * 100, 1)
        return result

    def _aggregators(self, configs, dt_from, dt_to, team_ids=None):
        fname = self._aggregator_field()
        if not fname:
            return None
        return self._simple_breakdown(configs, dt_from, dt_to, team_ids, fname,
                                      blank_label='مباشر / بدون تطبيق')

    def _order_types(self, configs, dt_from, dt_to, team_ids=None):
        fname, sel = self._order_type_field()
        if not fname:
            return None
        return self._simple_breakdown(configs, dt_from, dt_to, team_ids, fname, sel=sel)

    def _analytics_scope(self, configs, d_from, d_to, team_ids=None):
        utc_from, utc_to = self._bounds(d_from, d_to)
        return {
            'config_ids': configs.ids,
            'utc_from': utc_from,
            'utc_to': utc_to,
            'd_from': d_from,
            'd_to': d_to,
            'team_ids': team_ids or [],
            'tz': self._user_tz().zone,
            'start_hour': self._start_hour(),
        }

    def _kitchen_data(self, scope, configs, dt_from, dt_to, team_ids, kw):
        try:
            late = max(1, min(240, int(kw.get('late_minutes') or 15)))
        except (TypeError, ValueError):
            late = 15
        result = request.env['the.dash.analytics'].sudo().kitchen(scope, late_minutes=late)
        if result is None:
            # Older preparation displays without order stages.
            return self._kitchen(configs, dt_from, dt_to, team_ids)
        return result

    # -- Kitchen (KDS) performance -------------------------------------------

    def _kitchen(self, configs, dt_from, dt_to, team_ids=None):
        env = request.env
        if 'pos_preparation_display.order' not in env:
            return None
        prep = env['pos_preparation_display.order'].sudo().search_read(
            self._order_domain(configs, dt_from, dt_to, team_ids, prefix='pos_order_id.'),
            ['pos_order_id', 'create_date', 'write_date'], limit=20000)
        if not prep:
            return {'avg': 0, 'count': 0, 'by_branch': []}

        order_ids = [p['pos_order_id'][0] for p in prep if p['pos_order_id']]
        order_cfg = {o['id']: o['config_id']
                     for o in env['pos.order'].sudo().search_read(
                         [('id', 'in', order_ids)], ['config_id'])}

        durations, by_branch = [], {}
        for p in prep:
            if not (p['create_date'] and p['write_date'] and p['pos_order_id']):
                continue
            minutes = (p['write_date'] - p['create_date']).total_seconds() / 60.0
            if minutes <= 0 or minutes > 240:
                continue
            durations.append(minutes)
            cfg = order_cfg.get(p['pos_order_id'][0])
            if cfg:
                b = by_branch.setdefault(cfg[0], {'name': cfg[1], 'total': 0.0, 'count': 0})
                b['total'] += minutes
                b['count'] += 1

        if not durations:
            return {'avg': 0, 'count': 0, 'by_branch': []}
        return {
            'avg': round(sum(durations) / len(durations), 1),
            'count': len(durations),
            'by_branch': sorted(
                [{'name': b['name'], 'avg': round(b['total'] / b['count'], 1),
                  'count': b['count']} for b in by_branch.values()],
                key=lambda x: x['avg']),
        }

    # -- Break-even (own route and button) -----------------------------------
    # Reads the accounting and re-explodes every recipe, so it never runs
    # with the dashboard refresh.

    def _branch_analytics(self, configs, config_ids):
        """Cost centers of the selected branches when the scope is narrowed;
        None means company-wide expenses."""
        if not config_ids or len(configs) >= len(self._allowed_configs()):
            return None, 'company'
        if 'dash_cost_center_id' not in configs._fields:
            return None, 'company_no_cc'
        ids = configs.mapped('dash_cost_center_id').ids
        if not ids:
            return None, 'company_no_cc'
        return ids, 'branches'

    @http.route('/thedash/breakeven', auth='user', type='http', csrf=False)
    def dash_breakeven(self, period='today', date_from=None, date_to=None,
                       config_ids=None, team_ids=None, basis='avg', months='3', **kw):
        if not self._check_access():
            return self._json({'error': 'access_denied'})
        configs = self._configs(config_ids)
        if not configs:
            return self._json({'error': 'no_pos'})
        try:
            team_list = [int(i) for i in str(team_ids).split(',') if i.strip()] if team_ids else []
        except ValueError:
            team_list = []
        configs, tids = self._apply_team_filter(configs, team_list)
        try:
            months = max(1, min(12, int(months)))
        except (TypeError, ValueError):
            months = 3
        basis = 'period' if basis == 'period' else 'avg'
        d_from, d_to = self._date_range(period, date_from, date_to)
        scope = self._analytics_scope(configs, d_from, d_to, tids)
        analytic_ids, expense_scope = self._branch_analytics(configs, config_ids)
        started = time.monotonic()
        try:
            data = request.env['the.dash.profit'].sudo().breakeven(
                scope, basis=basis, months=months, analytic_ids=analytic_ids)
        except Exception as exc:  # noqa: BLE001 - shown in the card
            _logger.exception('The Dash break-even failed')
            request.env.cr.rollback()
            return self._json({'error': 'breakeven_failed',
                               'detail': '%s: %s' % (type(exc).__name__, exc)})
        data.update({
            'expense_scope': expense_scope,
            'period_label': '%s -> %s' % (d_from, d_to),
            'duration_ms': round((time.monotonic() - started) * 1000),
            'last_updated': ofields.Datetime.context_timestamp(
                request.env.user, datetime.now()).strftime('%Y-%m-%d %H:%M:%S'),
        })
        return self._json(data)

    # -- Operations across apps (own route and button) -------------------------

    @http.route('/thedash/ops', auth='user', type='http', csrf=False)
    def dash_ops(self, period='today', date_from=None, date_to=None,
                 config_ids=None, team_ids=None, sections=None, **kw):
        if not self._check_access():
            return self._json({'error': 'access_denied'})
        configs = self._configs(config_ids)
        if not configs:
            return self._json({'error': 'no_pos'})
        try:
            team_list = [int(i) for i in str(team_ids).split(',') if i.strip()] if team_ids else []
        except ValueError:
            team_list = []
        configs, tids = self._apply_team_filter(configs, team_list)
        d_from, d_to = self._date_range(period, date_from, date_to)
        scope = self._analytics_scope(configs, d_from, d_to, tids)
        analytic_ids, _expense_scope = self._branch_analytics(configs, config_ids)
        only = [s for s in (sections or '').split(',') if s] or None
        started = time.monotonic()
        data = request.env['the.dash.ops'].sudo().compute(scope, configs, analytic_ids, only)
        data.update({
            'period_label': '%s -> %s' % (d_from, d_to),
            'duration_ms': round((time.monotonic() - started) * 1000),
            'last_updated': ofields.Datetime.context_timestamp(
                request.env.user, datetime.now()).strftime('%Y-%m-%d %H:%M:%S'),
        })
        return self._json(data)

    # -- Actual vs recipe usage (own route and button) -------------------------

    @http.route('/thedash/variance', auth='user', type='http', csrf=False)
    def dash_variance(self, period='today', date_from=None, date_to=None,
                      config_ids=None, team_ids=None, **kw):
        if not self._check_access():
            return self._json({'error': 'access_denied'})
        configs = self._configs(config_ids)
        if not configs:
            return self._json({'error': 'no_pos'})
        try:
            team_list = [int(i) for i in str(team_ids).split(',') if i.strip()] if team_ids else []
        except ValueError:
            team_list = []
        configs, tids = self._apply_team_filter(configs, team_list)
        d_from, d_to = self._date_range(period, date_from, date_to)
        scope = self._analytics_scope(configs, d_from, d_to, tids)
        started = time.monotonic()
        try:
            data = request.env['the.dash.profit'].sudo().variance(scope)
        except Exception as exc:  # noqa: BLE001 - shown in the card
            _logger.exception('The Dash variance failed')
            request.env.cr.rollback()
            return self._json({'error': 'variance_failed',
                               'detail': '%s: %s' % (type(exc).__name__, exc)})
        if data.get('error'):
            return self._json(data)
        data.update({
            'period_label': '%s -> %s' % (d_from, d_to),
            'duration_ms': round((time.monotonic() - started) * 1000),
            'last_updated': ofields.Datetime.context_timestamp(
                request.env.user, datetime.now()).strftime('%Y-%m-%d %H:%M:%S'),
        })
        return self._json(data)

    # -- Multi-layer grouped report ------------------------------------------
    # Delegated to the the.dash.report engine, which builds a single SQL
    # statement from a whitelist of dimensions and measures. See
    # models/pos_dashboard_report.py for why _read_group cannot do this.
    #
    # It lives on its own route and its own button: it is by far the heaviest
    # part of the page, and when it failed inside the main payload it took the
    # whole dashboard down with it and said nothing.

    def _grouped_report(self, configs, dt_from, dt_to, group1='pos', group2='none',
                        team_ids=None):
        return request.env['the.dash.report'].sudo().with_context(
            dash_tz=self._user_tz().zone).build(
            configs.ids, dt_from, dt_to, group1, group2, team_ids,
            local_from=self._local_day(dt_from), local_to=self._local_day(dt_to))

    def _dimensions(self):
        return request.env['the.dash.report'].sudo().available_dimensions()

    @http.route('/thedash/report', auth='user', type='http', csrf=False)
    def dash_report(self, period='today', date_from=None, date_to=None,
                    config_ids=None, team_ids=None, group1='pos',
                    group2='none', **kw):
        """Grouped report only. Same period semantics as the main dashboard."""
        if not self._check_access():
            return self._json({'error': 'access_denied'})
        configs = self._configs(config_ids)
        if not configs:
            return self._json({'error': 'invalid'})

        try:
            team_list = [int(i) for i in str(team_ids).split(',') if i.strip()] \
                if team_ids else []
        except ValueError:
            team_list = []
        configs, tids = self._apply_team_filter(configs, team_list)

        d_from, d_to = self._date_range(period, date_from, date_to)
        dt_from, dt_to = self._bounds(d_from, d_to)

        started = time.monotonic()
        try:
            report = self._grouped_report(configs, dt_from, dt_to,
                                          group1, group2, tids)
        except Exception as exc:  # noqa: BLE001 - the message is the point
            _logger.exception('POS dashboard report failed (%s / %s)', group1, group2)
            # A failed report used to be a blank card. Hand the real database
            # message back so it can be acted on instead of guessed at.
            payload = {
                'error': 'report_failed',
                'detail': '%s: %s' % (type(exc).__name__, exc),
                'group1': group1,
                'group2': group2,
            }
            # The cursor is poisoned once a statement fails inside a request,
            # so roll back before touching the ORM again.
            request.env.cr.rollback()
            if self._sees_everything():
                try:
                    payload['sql'] = request.env['the.dash.report'].sudo().with_context(
                        dash_tz=self._user_tz().zone).prepare(
                        configs.ids, dt_from, dt_to, group1, group2, tids,
                        local_from=d_from, local_to=d_to)['query']
                except Exception:  # noqa: BLE001 - debug aid only
                    payload['sql'] = ''
            return self._json(payload)

        return self._json({
            'report': report,
            'period_label': '%s -> %s' % (d_from, d_to),
            'duration_ms': round((time.monotonic() - started) * 1000),
            'last_updated': ofields.Datetime.context_timestamp(
                request.env.user, datetime.now()).strftime('%Y-%m-%d %H:%M:%S'),
        })

    # -- Export helpers ------------------------------------------------------

    def _export_context(self, period, date_from, date_to, config_ids, team_ids):
        configs = self._configs(config_ids)
        if not configs:
            return None
        try:
            team_list = [int(i) for i in str(team_ids).split(',') if i.strip()] if team_ids else []
        except ValueError:
            team_list = []
        configs, tids = self._apply_team_filter(configs, team_list)
        d_from, d_to = self._date_range(period, date_from, date_to)
        dt_from, dt_to = self._bounds(d_from, d_to)
        return {
            'configs': configs,
            'label': self._scope_label(configs, config_ids),
            'd_from': d_from,
            'd_to': d_to,
            'dt_from': dt_from,
            'dt_to': dt_to,
            'team_ids': tids,
            'scope': self._analytics_scope(configs, d_from, d_to, tids),
        }

    # -- Export: Excel (xlsxwriter; xlwt is not used in Odoo 19) --------------

    @http.route('/thedash/export/excel', auth='user', type='http')
    def export_excel(self, period='today', date_from=None, date_to=None,
                     config_ids=None, team_ids=None, group1='pos',
                     group2='none', **kw):
        if not self._check_access():
            return request.not_found()
        c = self._export_context(period, date_from, date_to, config_ids, team_ids)
        if not c:
            return request.not_found()
        try:
            import xlsxwriter
        except ImportError:
            return request.make_response('xlsxwriter is not installed', status=500)

        configs, dt_from, dt_to, tids = c['configs'], c['dt_from'], c['dt_to'], c['team_ids']
        output = io.BytesIO()
        wb = xlsxwriter.Workbook(output, {'in_memory': True})
        bold = wb.add_format({'bold': True})
        money = wb.add_format({'num_format': '#,##0.00'})

        ws = wb.add_worksheet('Summary')
        ws.set_column(0, 0, 26)
        k = self._kpis(configs, dt_from, dt_to, tids)
        ws.write(0, 0, 'The Dash export', bold)
        ws.write(1, 0, 'Shop: %s' % c['label'])
        ws.write(2, 0, 'Period: %s to %s' % (c['d_from'], c['d_to']))
        summary = [
            ('Total Sales', k['total_sales'], money),
            ('Total Orders', k['total_orders'], None),
            ('Customers', k['total_customers'], None),
            ('Avg Order Value', k['avg_order'], money),
            ('Sales Growth %', k['sales_growth'], None),
            ('Refunds', k['refund_amount'], money),
            ('Cancelled', k['cancel_amount'], money),
        ]
        for i, (lbl, val, fmt) in enumerate(summary, start=4):
            ws.write(i, 0, lbl, bold)
            if fmt:
                ws.write(i, 1, val, fmt)
            else:
                ws.write(i, 1, val)

        Analytics = request.env['the.dash.analytics'].sudo()
        scope = c['scope']
        sales = Analytics.items_and_modifiers(scope, limit=5000)
        pay_rows = Analytics.payment_rows(scope)
        closing = Analytics.closing(scope)
        prod_rows = sales['items']

        def sheet(title, headers, rows, widths=None):
            sh = wb.add_worksheet(title)
            sh.right_to_left()
            for ci, h in enumerate(headers):
                sh.write(0, ci, h, bold)
                sh.set_column(ci, ci, (widths or {}).get(ci, 16))
            for ri, row in enumerate(rows, start=1):
                for ci, val in enumerate(row):
                    if isinstance(val, float):
                        sh.write(ri, ci, val, money)
                    else:
                        sh.write(ri, ci, val if val is not None else '')
            return sh

        sheet('مبيعات الأصناف', ['الصنف', 'الفئة', 'الكمية', 'الصافي قبل الضريبة',
                                 'المبيعات شامل الضريبة', '%'],
              [[p['name'], p['category'], p['qty'], p['net'], p['revenue'], p['pct']]
               for p in prod_rows], {0: 36, 1: 20})
        sheet('مبيعات المودفايرز', ['المودفاير', 'الكمية', 'الصافي قبل الضريبة',
                                    'المبيعات شامل الضريبة', '%'],
              [[m['name'], m['qty'], m['net'], m['revenue'], m['pct']]
               for m in sales['modifiers']], {0: 36})
        sheet('الخيارات', ['الخيار', 'الكمية', 'قيمة الإضافة'],
              [[a['name'], a['qty'], a['extra']] for a in sales['attributes']], {0: 36})

        prof = request.env['the.dash.profit'].sudo().profitability(scope, limit=5000)
        sheet('ربحية الأصناف', ['الصنف', 'النوع', 'الكمية', 'المبيعات قبل الضريبة',
                                'تكلفة الوحدة', 'التكلفة', 'الربح', 'الهامش %',
                                'عمولة التطبيقات', 'الربح بعد العمولة', 'ملاحظات'],
              [[e['name'], 'مودفاير' if e['kind'] == 'modifier' else 'صنف', e['qty'], e['net'],
                e['unit_cost'], e['cost'], e['profit'], e['margin'], e['commission'],
                e['profit_after'], e['note']]
               for e in prof['items'] + prof['modifiers']], {0: 34, 8: 60})
        menu = request.env['the.dash.profit'].sudo().menu_engineering(prof['items'])
        sheet('هندسة المنيو', ['الصنف', 'الفئة', 'الكمية', 'سعر البيع', 'المساهمة للوحدة',
                               'إجمالي المساهمة', 'التصنيف (كل المنيو)', 'التصنيف (داخل الفئة)',
                               'التوصية'],
              [[m['name'], m['category'], m['qty'], m['price'], m['cm_unit'], m['contribution'],
                menu['labels'][m['class']], menu['labels'][m['class_cat']],
                menu['advice'][m['class']]] for m in menu['items']], {0: 30, 8: 50})
        comm = Analytics.app_commissions(scope)
        if comm:
            sheet('عمولات التطبيقات', ['التطبيق', 'الطلبات', 'مبيعات التطبيق (GMV)',
                                       'المبيعات قبل الضريبة', 'العمولة', 'منها مقدّرة',
                                       'نسبة العمولة %', 'العمولة للطلب', 'الصافي بعد العمولة'],
                  [[a['name'], a['orders'], a['gmv'], a['net'], a['commission'], a['estimated'],
                    a['rate'], a['per_order'], a['after']] for a in comm['apps']], {0: 24})
            sheet('الربحية حسب القناة', ['القناة', 'المبيعات قبل الضريبة', 'تكلفة الرسبي',
                                         'العمولة', 'الربح بعد الرسبي', 'الربح بعد العمولة',
                                         'الهامش بعد العمولة %'],
                  [[c['name'], c['net'], c['cost'], c['commission'], c['profit'],
                    c['profit_after'], c['margin_after']] for c in prof['channels']], {0: 24})
        sheet('بدون رسبي', ['المنتج', 'يُباع كـ', 'الكمية', 'المبيعات قبل الضريبة',
                            'تكلفة الوحدة المستخدمة', 'ملاحظات'],
              [[n['name'], n['role'], n['qty'], n['net'], n['unit_cost'], n['note']]
               for n in prof['missing']], {0: 34, 5: 50})

        act = Analytics.activity(scope)
        tkeys = [k for k, _l in Analytics.ORDER_TYPES]
        sheet('حسب الساعة', ['الساعة', 'الطلبات', 'المبيعات', 'متوسط الطلب']
              + [act['type_labels'][k] for k in tkeys],
              [[h['hour'], h['orders'], h['sales'], h['avg']]
               + [h['by_type'][k] for k in tkeys] for h in act['hours']])
        sheet('نوع الطلب', ['النوع', 'الطلبات', 'المبيعات', 'متوسط الطلب', '%'],
              [[t['name'], t['orders'], t['sales'], t['avg'], t['pct']] for t in act['types']]
              + [['', '', '', '', '']]
              + [['%s (%s)' % (p['name'], p['type_name']), p['orders'], p['sales'], '', p['pct']]
                 for p in act['presets']], {0: 34})
        if len(act['days']) > 1:
            sheet('حسب اليوم التشغيلي', ['اليوم', 'اليوم من الأسبوع', 'الطلبات', 'المبيعات',
                                         'متوسط الطلب'] + [act['type_labels'][k] for k in tkeys],
                  [[d['day'], d['weekday'], d['orders'], d['sales'], d['avg']]
                   + [d['by_type'][k] for k in tkeys] for d in act['days']], {0: 14})

        col = Analytics.collections(scope, pay_rows)
        sheet('التحصيل', ['النوع / التطبيق', 'المبلغ', '%', 'الطلبات'],
              [[k['name'], k['amount'], k['pct'], ''] for k in col['kinds']]
              + [['', '', '', '']]
              + [['تطبيق: ' + a['name'], a['amount'], a['pct'], a['orders']] for a in col['apps']]
              + [['', '', '', '']]
              + [['طريقة دفع: %s (%s)' % (m['name'], m['kind_label']), m['amount'], m['pct'], '']
                 for m in col['methods']], {0: 40})

        dims = Analytics.breakdowns(scope, pay_rows, closing)
        for dim, title in (('pos', 'حسب نقطة البيع'), ('cost_center', 'حسب مركز التكلفة'),
                           ('region', 'حسب المنطقة'), ('cashier', 'حسب الكاشير')):
            sheet(title, ['الاسم', 'الطلبات', 'المبيعات', 'المرتجعات', 'نقدي', 'بنك',
                          'تطبيقات', 'آجل', 'الجلسات', 'النقد المتوقع', 'النقد المعدود',
                          'العجز', 'الزيادة', 'صافي فرق النقد', 'فرق غير نقدي'],
                  [[r['name'], r['orders'], r['sales'], r['refunds'], r['cash'], r['bank'],
                    r['app'], r['credit'], r['sessions'], r['expected_cash'],
                    r['counted_cash'], r['shortage'], r['overage'], r['difference'],
                    r['bank_difference']] for r in dims[dim]], {0: 30})

        sheet('الإغلاقات والفروقات', ['الجلسة', 'نقطة البيع', 'الكاشير', 'الحالة', 'الفتح',
                                      'الإغلاق', 'رصيد البداية', 'النقد المتوقع',
                                      'النقد المعدود', 'فرق النقد', 'فرق غير نقدي'],
              [[x['name'], x['pos'], x['cashier'], x['state_label'],
                ofields.Datetime.to_string(ofields.Datetime.context_timestamp(
                    request.env.user, x['start_at'])) if x['start_at'] else '',
                ofields.Datetime.to_string(ofields.Datetime.context_timestamp(
                    request.env.user, x['stop_at'])) if x['stop_at'] else '',
                x['opening'], x['expected'], x['counted'], x['difference'],
                x['bank_difference']] for x in closing['sessions']], {0: 22, 1: 24, 2: 20})

        kitchen = Analytics.kitchen(scope)
        if kitchen:
            sheet('وقت الخدمة', ['', 'الطلبات', 'المتوسط (دقيقة)', 'الوسيط', 'P90',
                                 'الأقصى', 'متأخرة', '% متأخرة'],
                  [['الإجمالي', kitchen['count'], kitchen['avg'], kitchen['median'],
                    kitchen['p90'], kitchen['max'], kitchen['late'], kitchen['late_pct']]]
                  + [['نقطة البيع: ' + b['name'], b['count'], b['avg'], b['median'], b['p90'],
                      b['max'], b['late'], b['late_pct']] for b in kitchen['by_pos']]
                  + [['الشاشة: ' + b['name'], b['count'], b['avg'], b['median'], b['p90'],
                      b['max'], b['late'], b['late_pct']] for b in kitchen['by_display']],
                  {0: 34})

        ws3 = wb.add_worksheet('Categories')
        ws3.set_column(0, 0, 28)
        for ci, h in enumerate(['Category', 'Sales', '% Share']):
            ws3.write(0, ci, h, bold)
        for ri, cat in enumerate(self._categories(prod_rows), start=1):
            ws3.write(ri, 0, cat['name'])
            ws3.write(ri, 1, cat['sales'], money)
            ws3.write(ri, 2, cat['pct'])

        ws4 = wb.add_worksheet('Payments')
        ws4.set_column(0, 0, 28)
        for ci, h in enumerate(['Method', 'Amount', '% Share']):
            ws4.write(0, ci, h, bold)
        for ri, p in enumerate(self._payments(configs, dt_from, dt_to, tids), start=1):
            ws4.write(ri, 0, p['name'])
            ws4.write(ri, 1, p['amount'], money)
            ws4.write(ri, 2, p['pct'])

        report = self._grouped_report(configs, dt_from, dt_to, group1, group2, tids)
        ws5 = wb.add_worksheet('Grouped Report')
        ws5.set_column(0, 1, 28)
        ws5.write(0, 0, 'Group by: %s%s' % (
            report['group1'],
            ' / %s' % report['group2'] if report['group2'] != 'none' else ''), bold)
        if report.get('fans'):
            ws5.write(0, 3, 'Note: a many-to-many dimension is in use; '
                            'a line is counted once per value.', bold)

        headers = ['Group', 'Sub-group'] + [lbl for _k, lbl, _t in self.MEASURE_COLUMNS]
        for ci, h in enumerate(headers):
            ws5.write(1, ci, h, bold)

        def write_measures(row_idx, vals):
            for mi, (key, _lbl, kind) in enumerate(self.MEASURE_COLUMNS):
                val = vals.get(key)
                col = 2 + mi
                if val is None:
                    ws5.write(row_idx, col, '')
                elif kind == 'money':
                    ws5.write(row_idx, col, val, money)
                else:
                    ws5.write(row_idx, col, val)

        ri = 2
        for row in report['rows']:
            ws5.write(ri, 0, row['label'], bold)
            write_measures(ri, row)
            ri += 1
            for ch in row['children']:
                ws5.write(ri, 1, ch['label'])
                write_measures(ri, ch)
                ri += 1
        ws5.write(ri, 0, 'TOTAL', bold)
        write_measures(ri, report['totals'])

        wb.close()
        filename = 'the_dash_%s.xlsx' % c['d_from']
        return request.make_response(output.getvalue(), headers=[
            ('Content-Type',
             'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
            ('Content-Disposition', 'attachment; filename="%s"' % filename),
        ])

    # -- Export: CSV ---------------------------------------------------------

    @http.route('/thedash/export/csv', auth='user', type='http')
    def export_csv(self, period='today', date_from=None, date_to=None,
                   config_ids=None, team_ids=None, group1='pos', group2='none', **kw):
        if not self._check_access():
            return request.not_found()
        c = self._export_context(period, date_from, date_to, config_ids, team_ids)
        if not c:
            return request.not_found()

        configs, dt_from, dt_to, tids = c['configs'], c['dt_from'], c['dt_to'], c['team_ids']
        output = io.StringIO()
        writer = csv.writer(output)

        writer.writerow(['=== TOP PRODUCTS ==='])
        writer.writerow(['Product', 'Category', 'Qty Sold', 'Revenue'])
        for p in self._top_products(self._product_groups(configs, dt_from, dt_to, tids)):
            writer.writerow([p['name'], p['category'], p['qty'], p['revenue']])

        writer.writerow([])
        writer.writerow(['=== GROUPED REPORT ==='])
        report = self._grouped_report(configs, dt_from, dt_to, group1, group2, tids)
        writer.writerow(['Group by', report['group1'], report['group2']])
        if report.get('fans'):
            writer.writerow(['Note', 'many-to-many dimension in use; '
                                     'a line is counted once per value'])
        keys = [k for k, _l, _t in self.MEASURE_COLUMNS]
        writer.writerow(['Group', 'Sub-group']
                        + [lbl for _k, lbl, _t in self.MEASURE_COLUMNS])
        for row in report['rows']:
            writer.writerow([row['label'], '']
                            + [row.get(k, '') for k in keys])
            for ch in row['children']:
                writer.writerow(['', ch['label']]
                                + [ch.get(k, '') for k in keys])
        writer.writerow(['TOTAL', '']
                        + [report['totals'].get(k, '') for k in keys])

        filename = 'the_dash_%s.csv' % c['d_from']
        return request.make_response(
            output.getvalue().encode('utf-8-sig'), headers=[
                ('Content-Type', 'text/csv; charset=utf-8'),
                ('Content-Disposition', 'attachment; filename="%s"' % filename),
            ])




# -- Page shell. Behaviour lives in the served JS asset. ---------------------
# The page arrives with no figures at all: both the dashboard and the report
# wait for an explicit click.

PAGE_TEMPLATE = '''<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>لوحة البيانات · Resta</title>
<link rel="stylesheet" href="/the_dash/static/src/page/dash.css"/>
<script src="/web/static/lib/Chart/Chart.js"></script>
</head>
<body data-theme="light">
<div id="app">

<header>
  <div class="brand">
    <span class="rs-tile">R</span>
    <span class="lock">
      <span class="wordmark">لوحة البيانات</span>
      <span class="by">The Dash · <b>Resta</b></span>
    </span>
  </div>

  <div class="header-right">
    <div id="scopeWrap">
      <button id="scopeBtn" type="button"><span id="scopeLabel">كل الفروع</span> &#9662;</button>
      <div id="scopePanel">
        <div class="scope-sec" id="branchSec">
          <div class="scope-head">
            <span>الفروع</span>
            <button class="linkbtn" type="button" id="branchAll">تحديد الكل</button>
          </div>
          <div id="branchList"></div>
        </div>
        <div class="scope-sec" id="teamSec" style="display:none">
          <div class="scope-head">
            <span>فرق المبيعات</span>
            <button class="linkbtn" type="button" id="teamAll">تحديد الكل</button>
          </div>
          <div id="teamList"></div>
        </div>
        <button class="btn btn-accent scope-apply" type="button" id="scopeApply">تطبيق</button>
      </div>
    </div>

    <div class="tabs" id="tabs">
      <button class="tab active" type="button" data-period="today">اليوم</button>
      <button class="tab" type="button" data-period="yesterday">أمس</button>
      <button class="tab" type="button" data-period="week">7 أيام</button>
      <button class="tab" type="button" data-period="month">30 يوم</button>
      <button class="tab" type="button" data-period="custom">مخصص</button>
    </div>

    <button class="iconbtn" id="themeBtn" type="button" title="تبديل الوضع">&#9728;</button>
    <div class="export-group">
      <button class="iconbtn" type="button" id="btnPdf" title="طباعة / PDF">&#128424;</button>
      <button class="iconbtn" type="button" id="btnExcel" title="تصدير Excel">&#128200;</button>
      <button class="iconbtn" type="button" id="btnCsv" title="تصدير CSV">&#128203;</button>
    </div>
    <button class="btn btn-accent" id="refreshBtn" type="button">تحديث اللوحة</button>
  </div>
</header>

<div class="statusbar" id="statusBar">
  <span class="pill pill-idle" id="stState">لم يتم التحميل</span>
  <span id="stStale">اضغط «تحديث اللوحة» لتشغيل الاستعلام</span>
  <span id="stDuration"></span>
  <span id="stPeriod"></span>
  <span class="grow"></span>
  <button class="linkbtn" id="stDetailsBtn" type="button" style="display:none">تفاصيل الأداء</button>
</div>
<div class="timings" id="stTimings"></div>

<div class="pending-note" id="pendingNote">
  غيّرت الفلاتر — اضغط «تحديث اللوحة» لتطبيقها. لا شيء يعمل على الخادم قبل ذلك.
</div>

<div id="customRow">
  <span class="dlabel">نطاق مخصص</span>
  <div class="drow"><span class="dlabel">من</span><input type="date" id="dateFrom" class="dinput"/></div>
  <div class="drow"><span class="dlabel">إلى</span><input type="date" id="dateTo" class="dinput"/></div>
  <span class="drange-result" id="dRangeResult"></span>
</div>

<div class="idle-card" id="idleCard">
  <div class="idle-mark"><i></i><i></i><i></i></div>
  <div class="idle-title">لا شيء يعمل حتى تطلبه</div>
  <div class="idle-text">
    ذاداش لا يسحب أي بيانات تلقائياً ولا يحدّث نفسه، ولا يبطئ نقاط البيع.<br/>
    اللوحة والتقرير لكلٍّ منهما زر تحديث مستقل.
  </div>
  <button class="btn btn-accent btn-lg" id="idleBtn" type="button">تحميل اللوحة الآن</button>
</div>

<div id="content" style="display:none">

  <div class="kpi-grid" id="kpiGrid"></div>

  <div class="card mb20" id="hoursCard">
    <div class="card-head">
      <div>
        <div class="card-title nomb">المبيعات حسب ساعات اليوم التشغيلي</div>
        <div class="card-sub" id="bdNote"></div>
      </div>
      <div class="ctrl-row">
        <div class="seg" id="hrMetric" data-pref="hrMetric">
          <button type="button" data-v="sales" class="on">المبيعات</button>
          <button type="button" data-v="orders">الطلبات</button>
          <button type="button" data-v="avg">متوسط الطلب</button>
        </div>
        <div class="seg" id="hrSplit" data-pref="hrSplit">
          <button type="button" data-v="total" class="on">إجمالي</button>
          <button type="button" data-v="type">نوع الطلب</button>
          <button type="button" data-v="pay">طريقة الدفع</button>
        </div>
        <div class="seg" id="hrRange" data-pref="hrRange">
          <button type="button" data-v="work" class="on">ساعات العمل</button>
          <button type="button" data-v="all">24 ساعة</button>
        </div>
      </div>
    </div>
    <div class="stat-grid" id="hoursStats"></div>
    <div class="chart-box tall"><canvas id="hourlyChart"></canvas></div>
    <div class="card-note mt10" id="hrNote"></div>
  </div>

  <div class="card mb20" id="typeCard">
    <div class="card-head">
      <div class="card-title nomb">نوع الطلب: محلي · سفري · توصيل · تطبيقات</div>
      <div class="seg" id="typeMetric" data-pref="typeMetric">
        <button type="button" data-v="sales" class="on">المبيعات</button>
        <button type="button" data-v="orders">الطلبات</button>
      </div>
    </div>
    <div class="stat-grid" id="typeStats"></div>
    <div class="grid3">
      <div><div class="sub-title">الحصة</div><div class="chart-box"><canvas id="typeShareChart"></canvas></div></div>
      <div><div class="sub-title">الطلبات ومتوسط الطلب</div><div class="chart-box"><canvas id="typeAvgChart"></canvas></div></div>
      <div><div class="sub-title">مزيج الأنواع عبر ساعات العمل (%)</div><div class="chart-box"><canvas id="typeMixChart"></canvas></div></div>
    </div>
    <div class="sub-title mt10" id="presetTitle">حسب الإعداد المسبق في نقطة البيع (Preset)</div>
    <div id="presetTable"></div>
    <div class="card-note mt10" id="typeNote"></div>
  </div>

  <div class="card mb20" id="trendCard" style="display:none">
    <div class="card-head">
      <div class="card-title nomb">حسب اليوم التشغيلي</div>
      <div class="seg" id="trendSplit" data-pref="trendSplit">
        <button type="button" data-v="type" class="on">نوع الطلب</button>
        <button type="button" data-v="pay">طريقة الدفع</button>
        <button type="button" data-v="orders">الطلبات ومتوسط الطلب</button>
      </div>
    </div>
    <div class="chart-box tall"><canvas id="trendChart"></canvas></div>
    <div class="sub-title mt10">خريطة الذروة: متوسط المبيعات لكل يوم من الأسبوع × ساعة</div>
    <div class="scrollx nomt"><div id="heatBox"></div></div>
  </div>

  <div class="charts-grid">
    <div class="card"><div class="card-title">أعلى الأصناف مبيعاً</div><div class="chart-box tall"><canvas id="topChart"></canvas></div></div>
    <div class="card"><div class="card-title">حسب فئة نقاط البيع</div><div class="chart-box tall"><canvas id="catChart"></canvas></div></div>
  </div>

  <div class="bottom-grid">
    <div class="card">
      <div class="card-head">
        <div class="card-title nomb">مبيعات الأصناف</div>
        <button class="btn btn-soft" id="moreProdBtn" type="button" style="display:none">عرض المزيد</button>
      </div>
      <div class="card-note">الأصناف الرئيسية بدون المودفايرز، والكومبو بسعره الكامل (عناصره داخله).</div>
      <div class="scrollbox">
        <table class="dtable">
          <thead><tr><th>#</th><th>الصنف</th><th>الفئة</th><th>الكمية</th><th>المبيعات</th><th>%</th></tr></thead>
          <tbody id="topBody"></tbody>
        </table>
      </div>
    </div>
    <div class="card" id="modCard">
      <div class="card-title">مبيعات المودفايرز</div>
      <div class="stat-grid" id="salesStats"></div>
      <div class="scrollbox">
        <table class="dtable">
          <thead><tr><th>#</th><th>المودفاير</th><th>الكمية</th><th>المبيعات</th><th>%</th></tr></thead>
          <tbody id="modBody"></tbody>
        </table>
        <div id="attrBox"></div>
      </div>
    </div>
  </div>

  <div class="card mb20" id="profitCard">
    <div class="card-head">
      <div>
        <div class="card-title nomb">ربحية الأصناف حسب تكلفة الرسبي</div>
        <div class="card-sub" id="profitSub"></div>
      </div>
      <div class="seg" id="profitView" data-pref="profitView">
        <button type="button" data-v="all" class="on">الكل</button>
        <button type="button" data-v="missing">بدون رسبي</button>
        <button type="button" data-v="low">أقل هامش</button>
        <button type="button" data-v="modifiers">المودفايرز</button>
      </div>
    </div>
    <div class="stat-grid" id="profitStats"></div>
    <div class="grid2 mb16">
      <div><div class="sub-title">أعلى الأصناف ربحاً</div><div class="chart-box tall"><canvas id="profitChart"></canvas></div></div>
      <div><div class="sub-title">هامش الربح % لأعلى الأصناف مبيعاً</div><div class="chart-box tall"><canvas id="marginChart"></canvas></div></div>
    </div>
    <div class="scrollbox"><div id="profitTable"></div></div>
    <div class="sub-title mt10" id="missingTitle">منتجات بدون رسبي أو مكونات بدون تكلفة</div>
    <div class="scrollbox"><div id="missingTable"></div></div>
    <div class="card-note mt10">التكلفة = رسبي الصنف (قائمة المواد في التصنيع) مفكوكة حتى المكونات بتكلفتها، مع الرسبيات الفرعية وتحويل الوحدات. الكومبو تكلفته من رسبي عناصره. الصنف بدون رسبي يأخذ تكلفة المنتج نفسه ويظهر بملاحظة. المبيعات قبل الضريبة.</div>
  </div>

  <div class="card mb20" id="menuCard" style="display:none">
    <div class="card-head">
      <div>
        <div class="card-title nomb">هندسة المنيو</div>
        <div class="card-sub">الشعبية (الكمية) مقابل المساهمة للوحدة بعد الرسبي والعمولة</div>
      </div>
      <div class="seg" id="menuScope" data-pref="menuScope">
        <button type="button" data-v="all" class="on">كل المنيو</button>
        <button type="button" data-v="cat">داخل كل فئة</button>
      </div>
    </div>
    <div class="stat-grid" id="menuStats"></div>
    <div class="grid2 mb16">
      <div><div class="sub-title">المصفوفة</div><div class="chart-box tall"><canvas id="menuChart"></canvas></div></div>
      <div><div class="sub-title">المساهمة حسب التصنيف</div><div class="chart-box tall"><canvas id="menuShareChart"></canvas></div></div>
    </div>
    <div class="scrollbox"><div id="menuTable"></div></div>
    <div class="card-note mt10" id="menuNote"></div>
  </div>

  <div class="card mb20" id="commCard" style="display:none">
    <div class="card-head">
      <div>
        <div class="card-title nomb">عمولات التطبيقات (كنكت)</div>
        <div class="card-sub">العمولة من طلبات كنكت؛ والطلب بدون عمولة تُقدّر له من «نسبة العمولة الافتراضية» في الربط</div>
      </div>
    </div>
    <div class="stat-grid" id="commStats"></div>
    <div class="grid2 mb16">
      <div><div class="sub-title">العمولة ونسبتها لكل تطبيق</div><div class="chart-box"><canvas id="commAppChart"></canvas></div></div>
      <div><div class="sub-title">الهامش حسب القناة: بعد الرسبي وبعد العمولة</div><div class="chart-box"><canvas id="channelChart"></canvas></div></div>
    </div>
    <div id="commDayBox"><div class="sub-title">العمولة اليومية حسب التطبيق</div>
      <div class="chart-box mb16"><canvas id="commDayChart"></canvas></div></div>
    <div class="sub-title">قائمة العمولات حسب التطبيق</div>
    <div id="commTable"></div>
    <div class="sub-title mt10">الربحية حسب القناة</div>
    <div id="channelTable"></div>
    <div class="card-note mt10" id="commNote"></div>
  </div>

  <div class="card mb20" id="colCard">
    <div class="card-title">التحصيل: نقدي · بنك · تطبيقات</div>
    <div class="stat-grid" id="colStats"></div>
    <div class="grid3 mb16">
      <div><div class="sub-title">حسب النوع</div><div class="chart-box"><canvas id="kindChart"></canvas></div></div>
      <div><div class="sub-title">طرق الدفع</div><div class="chart-box"><canvas id="methodChart"></canvas></div></div>
      <div><div class="sub-title">تطبيقات التوصيل</div><div class="chart-box"><canvas id="appChart"></canvas></div></div>
    </div>
    <div class="bottom-grid nomb">
      <div>
        <div class="sub-title">كل تطبيق</div>
        <div id="appTable"></div>
      </div>
      <div>
        <div class="sub-title">طرق الدفع</div>
        <div id="methodTable"></div>
      </div>
    </div>
  </div>

  <div class="bottom-grid">
    <div class="card" id="aggCard" style="display:none">
      <div class="card-title">المبيعات حسب التطبيق</div>
      <div class="chart-box"><canvas id="aggChart"></canvas></div>
      <div id="aggTable" class="mt10"></div>
    </div>
    <div class="card" id="otCard" style="display:none">
      <div class="card-title">المبيعات حسب نوع الطلب</div>
      <div class="chart-box"><canvas id="otChart"></canvas></div>
      <div id="otTable" class="mt10"></div>
    </div>
  </div>

  <div class="card mb20" id="dimCard">
    <div class="card-head">
      <div class="card-title nomb">المبيعات والتحصيل والفروقات حسب</div>
      <div class="tabs" id="dimTabs">
        <button class="tab active" type="button" data-dim="pos">نقطة البيع</button>
        <button class="tab" type="button" data-dim="cost_center">مركز التكلفة</button>
        <button class="tab" type="button" data-dim="region">المنطقة</button>
        <button class="tab" type="button" data-dim="cashier">الكاشير</button>
      </div>
    </div>
    <div class="scrollx"><table class="dtable rpt-table" id="dimTable"></table></div>
    <div class="card-note" id="dimNote"></div>
  </div>

  <div class="card mb20" id="closeCard">
    <div class="card-head">
      <div class="card-title nomb">الإغلاقات: العجز والزيادة والفروقات</div>
      <div class="seg" id="shortDim" data-pref="shortDim">
        <button type="button" data-v="by_pos" class="on">نقطة البيع</button>
        <button type="button" data-v="by_cashier">الكاشير</button>
        <button type="button" data-v="by_day">اليوم التشغيلي</button>
      </div>
    </div>
    <div class="stat-grid" id="closeStats"></div>
    <div class="grid2 mb16">
      <div><div class="sub-title" id="shortTitle">العجز والزيادة</div><div class="chart-box tall"><canvas id="shortChart"></canvas></div></div>
      <div><div class="sub-title">أكبر الفروقات حسب الجلسة (نقدي + غير نقدي)</div><div class="chart-box tall"><canvas id="sessChart"></canvas></div></div>
    </div>
    <div class="scrollx"><table class="dtable rpt-table" id="closeTable"></table></div>
    <div class="card-note">الفرق = المعدود − المتوقع: بالسالب عجز، وبالموجب زيادة. الفرق غير النقدي (الشبكة) من قيود الإغلاق على حسابات الربح والخسارة لدفتر طريقة الدفع.</div>
  </div>

  <div class="card mb20" id="kitchenCard" style="display:none">
    <div class="card-head">
      <div class="card-title nomb">وقت الخدمة حسب شاشة المطبخ</div>
      <span class="dlabel" id="kitchenSource"></span>
    </div>
    <div id="kitchenBox"></div>
    <div class="grid2 mt10" id="kitchenCharts">
      <div><div class="sub-title">متوسط وقت الخدمة والمتأخرة حسب الساعة</div><div class="chart-box"><canvas id="kHourChart"></canvas></div></div>
      <div><div class="sub-title">متوسط وقت الخدمة حسب الشاشة</div><div class="chart-box"><canvas id="kDispChart"></canvas></div></div>
    </div>
  </div>

</div><!-- content -->

<div class="card mb20" id="opsCard">
  <div class="card-head">
    <div>
      <div class="card-title nomb">العمليات: المخزون · المشتريات · المحاسبة · الإنتاج · الموظفون · رضا الضيوف</div>
      <div class="card-sub">من تطبيقات أودو المثبتة فقط، للفترة والفروع المحددة</div>
    </div>
    <div class="ops-tabs" id="opsTabs"></div>
    <button class="btn btn-accent" id="opsRefreshBtn" type="button">تحديث العمليات</button>
  </div>
  <div class="statusbar" id="opsStatus">
    <span class="pill pill-idle" id="opsState">لم يُحمَّل</span>
    <span id="opsStale">اضغط «تحديث العمليات» لحسابها للفترة والفروع المحددة</span>
    <span id="opsDuration"></span>
    <span id="opsPeriod"></span>
  </div>
  <div class="pending-note" id="opsPending">تغيّرت الفترة أو الفروع — اضغط «تحديث العمليات».</div>
  <div class="err-box" id="opsError"></div>
  <div id="opsContent" style="display:none"></div>
</div>

<div class="card mb20" id="varCard">
  <div class="card-head">
    <div>
      <div class="card-title nomb">التكلفة الفعلية مقابل الرسبي (الهدر والفروقات)</div>
      <div class="card-sub">تحديث مستقل — يفك الرسبيات ويقرأ حركات المخزون في مستودعات الفروع</div>
    </div>
    <div class="ctrl-row">
      <div class="seg" id="varView" data-pref="varView">
        <button type="button" data-v="all" class="on">الكل</button>
        <button type="button" data-v="over">فرق أكثر من 5%</button>
        <button type="button" data-v="waste">الهالك</button>
      </div>
      <button class="btn btn-accent" id="varRefreshBtn" type="button">تحديث المقارنة</button>
    </div>
  </div>
  <div class="statusbar">
    <span class="pill pill-idle" id="varState">لم يُحمَّل</span>
    <span id="varStale">اضغط «تحديث المقارنة» لحسابها للفترة والفروع المحددة</span>
    <span id="varDuration"></span>
    <span id="varPeriod"></span>
  </div>
  <div class="pending-note" id="varPending">تغيّرت الفترة أو الفروع — اضغط «تحديث المقارنة».</div>
  <div class="err-box" id="varError"></div>
  <div id="varContent" style="display:none">
    <div class="stat-grid" id="varStats"></div>
    <div class="grid2 mb16">
      <div><div class="sub-title">أكبر الفروقات بالقيمة (فعلي − نظري)</div><div class="chart-box tall"><canvas id="varChart"></canvas></div></div>
      <div><div class="sub-title">أين ذهب المكون: مبيعات · إنتاج · هالك · جرد</div><div class="chart-box tall"><canvas id="varMixChart"></canvas></div></div>
    </div>
    <div class="scrollbox"><div id="varTable"></div></div>
    <div class="card-note mt10" id="varNote"></div>
  </div>
</div>

<div class="card mb20" id="beCard">
  <div class="card-head">
    <div>
      <div class="card-title nomb">نقطة التعادل: الطلبات مقابل المصاريف</div>
      <div class="card-sub">تحديث مستقل — يقرأ المحاسبة والرسبيات فلا يعمل مع تحديث اللوحة</div>
    </div>
    <div class="ctrl-row">
      <div class="seg" id="beBasis">
        <button type="button" data-v="avg" class="on">متوسط آخر 3 أشهر</button>
        <button type="button" data-v="period">مصاريف الفترة الفعلية</button>
      </div>
      <button class="btn btn-accent" id="beRefreshBtn" type="button">تحديث نقطة التعادل</button>
    </div>
  </div>
  <div class="statusbar" id="beStatus">
    <span class="pill pill-idle" id="beState">لم يُحمَّل</span>
    <span id="beStale">اضغط «تحديث نقطة التعادل» لحسابها للفترة والفروع المحددة</span>
    <span id="beDuration"></span>
    <span id="bePeriod"></span>
  </div>
  <div class="pending-note" id="bePending">تغيّرت الفترة أو الفروع أو أساس المصاريف — اضغط «تحديث نقطة التعادل».</div>
  <div class="err-box" id="beError"></div>
  <div id="beContent" style="display:none">
    <div class="stat-grid" id="beStats"></div>
    <div class="grid2 mb16">
      <div><div class="sub-title">منحنى التعادل: الإيراد مقابل المصاريف حسب عدد الطلبات</div><div class="chart-box tall"><canvas id="beCurveChart"></canvas></div></div>
      <div><div class="sub-title">الطلبات اليومية مقابل طلبات التعادل</div><div class="chart-box tall"><canvas id="beDailyChart"></canvas></div></div>
    </div>
    <div class="grid2">
      <div><div class="sub-title">المساهمة التراكمية مقابل المصاريف التراكمية</div><div class="chart-box"><canvas id="beCumChart"></canvas></div></div>
      <div><div class="sub-title">المصاريف حسب الحساب</div><div class="chart-box"><canvas id="beExpChart"></canvas></div></div>
    </div>
    <div class="card-note mt10" id="beNote"></div>
  </div>
</div>

<div class="card mb20" id="rptCard">
  <div class="card-head">
    <div class="card-title nomb">التقرير متعدد الطبقات</div>
    <button class="btn btn-accent" id="rptRefreshBtn" type="button">تحديث التقرير</button>
  </div>

  <div class="statusbar" id="rptStatus">
    <span class="pill pill-idle" id="rptState">لم يُحمَّل</span>
    <span id="rptStale">للتقرير زر تحديث مستقل ولا يعمل مع تحديث اللوحة</span>
    <span id="rptDuration"></span>
    <span id="rptPeriod"></span>
  </div>

  <div class="rpt-controls">
    <span class="dlabel">تجميع بـ</span>
    <select id="grp1" class="rsel"></select>
    <span class="dlabel">ثم بـ</span>
    <select id="grp2" class="rsel"></select>
    <div id="colWrap">
      <button id="colBtn" type="button">الأعمدة</button>
      <div id="colPanel"></div>
    </div>
    <span class="grow"></span>
    <span class="dlabel" id="rptHint"></span>
  </div>

  <div class="pending-note" id="rptPending">
    تغيّرت الفترة أو التجميع — اضغط «تحديث التقرير» لإعادة الحساب.
  </div>
  <div class="fan-note" id="fanNote">
    بُعد متعدد القيم مُستخدَم (الموديفاير): السطر الواحد يُحتسب مرة لكل قيمة،
    لذلك مجموع المجموعات يتجاوز الإجمالي الفعلي.
  </div>
  <div class="fan-note" id="truncNote">
    تجاوزت النتيجة الحد الأقصى للصفوف وتم اقتطاعها — ضيّق الفترة أو غيّر التجميع.
  </div>

  <div class="err-box" id="rptError"></div>

  <div class="idle-card slim" id="rptIdle">
    <div class="idle-text">
      التقرير أثقل جزء في اللوحة، فلا يُحسب إلا عند الطلب.<br/>
      اختر الفترة والتجميع ثم اضغط «تحديث التقرير».
    </div>
  </div>

  <div id="rptContent" style="display:none">
    <div class="totals-strip" id="rptTotals"></div>
    <div class="chart-box"><canvas id="rptChart"></canvas></div>
    <div class="scrollx">
      <table class="dtable rpt-table">
        <thead><tr id="rptHead"></tr></thead>
        <tbody id="rptBody"></tbody>
        <tfoot><tr id="rptFoot"></tr></tfoot>
      </table>
    </div>
  </div>
</div>

</div><!-- app -->

<script id="dashConfig" type="application/json">__CONFIG__</script>
<script src="/the_dash/static/src/page/dash.js"></script>
</body>
</html>'''
