# -*- coding: utf-8 -*-
"""Operations: The Dash beyond the till.

One section per Odoo app - inventory, purchasing, accounting, manufacturing,
people and guests - computed only when that app is installed, scoped to the
branches (points of sale) picked in the page. Every section is computed in its
own savepoint: one failing section shows its error and the others still load.

Section shape (rendered generically by dash.js):
    {key, title, link, note,
     stats:  [{label, value, note, kind, tone}],   kind: money|int|pct|qty|text
     tables: [{title, head: [...], rows: [[...]], kinds: [...]}],
     chart:  {type, labels, series: [{label, data, tone}]}}
"""
import logging
from datetime import date, datetime, time, timedelta

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

INCOME = ('income', 'income_other')
COGS = ('expense_direct_cost',)
EXPENSES = ('expense', 'expense_depreciation')


def _stat(label, value, note='', kind='money', tone=''):
    return {'label': label, 'value': value, 'note': note, 'kind': kind, 'tone': tone}


class TheDashOps(models.AbstractModel):
    _name = 'the.dash.ops'
    _description = 'The Dash - operations across apps'

    # ------------------------------------------------------------------ utils
    def _has(self, *model_names):
        return all(m in self.env for m in model_names)

    def _installed(self, module):
        return bool(self.env['ir.module.module'].sudo().search_count(
            [('name', '=', module), ('state', '=', 'installed')]))

    def _round(self, v, digits=2):
        return round(v or 0.0, digits)

    def _warehouses(self, configs):
        if 'stock.warehouse' not in self.env:
            return self.env['res.partner'].browse()  # empty recordset placeholder
        whs = configs.mapped('picking_type_id.warehouse_id')
        return whs or self.env['stock.warehouse'].search(
            [('company_id', 'in', configs.company_id.ids)])

    def _companies(self, configs):
        return configs.company_id or self.env.company

    def _bounds(self, d_from, d_to):
        return (datetime.combine(d_from, time.min), datetime.combine(d_to, time.max))

    def _action_link(self, xmlid):
        rec = self.env.ref(xmlid, raise_if_not_found=False)
        return '/odoo/action-%s' % xmlid if rec else None

    # ------------------------------------------------------------- dispatcher
    SECTIONS = ('inventory', 'purchase', 'accounting', 'manufacturing', 'people', 'guests')

    @api.model
    def compute(self, scope, configs, analytic_ids=None, only=None):
        out, timings = [], {}
        for key in self.SECTIONS:
            if only and key not in only:
                continue
            t0 = datetime.now()
            try:
                with self.env.cr.savepoint():
                    sec = getattr(self, '_sec_%s' % key)(scope, configs, analytic_ids)
            except Exception as exc:  # noqa: BLE001 - shown in the section
                _logger.exception('The Dash operations section %s failed', key)
                sec = {'key': key, 'title': key, 'error': '%s: %s' % (type(exc).__name__, exc)}
            if sec:
                out.append(sec)
            timings[key] = round((datetime.now() - t0).total_seconds() * 1000)
        return {'sections': out, 'timings': timings}

    # -------------------------------------------------------------- inventory
    def _sec_inventory(self, scope, configs, analytic_ids):
        if not self._has('stock.quant', 'stock.warehouse'):
            return None
        whs = self._warehouses(configs)
        companies = self._companies(configs)
        loc_dom = [('location_id.usage', '=', 'internal'),
                   ('location_id.warehouse_id', 'in', whs.ids)]
        Quant = self.env['stock.quant'].sudo()
        groups = Quant._read_group(loc_dom, ['product_id'], ['quantity:sum'])
        value, negatives, rows = 0.0, 0, []
        for product, qty in groups:
            cost = product.with_company(companies[:1]).standard_price
            v = qty * cost
            value += v
            if qty < 0:
                negatives += 1
            rows.append((v, product.display_name, qty, product.uom_id.name, cost))
        rows.sort(key=lambda r: -r[0])

        below = 0
        if 'stock.warehouse.orderpoint' in self.env:
            ops = self.env['stock.warehouse.orderpoint'].sudo().search(
                [('warehouse_id', 'in', whs.ids), ('trigger', 'in', ('auto', 'manual'))])
            below = len(ops.filtered(lambda o: o.qty_forecast < o.product_min_qty))

        now = fields.Datetime.now()
        late = self.env['stock.picking'].sudo().search_count([
            ('picking_type_id.warehouse_id', 'in', whs.ids),
            ('state', 'in', ('confirmed', 'waiting', 'assigned')),
            ('scheduled_date', '<', now)])

        dt_from, dt_to = self._bounds(scope['d_from'], scope['d_to'])
        scrap_value = 0.0
        if 'stock.scrap' in self.env:
            for s in self.env['stock.scrap'].sudo().search([
                    ('state', '=', 'done'), ('date_done', '>=', dt_from), ('date_done', '<=', dt_to),
                    ('location_id.warehouse_id', 'in', whs.ids)]):
                scrap_value += s.scrap_qty * s.product_id.with_company(s.company_id).standard_price

        stats = [
            _stat('قيمة المخزون الحالية', self._round(value), '%d صنف في %d مستودع' % (len(groups), len(whs))),
            _stat('أصناف تحت الحد الأدنى', below, 'حسب قواعد إعادة الطلب', 'int', 'neg' if below else ''),
            _stat('أرصدة سالبة', negatives, 'أصناف رصيدها أقل من صفر', 'int', 'neg' if negatives else ''),
            _stat('تحويلات متأخرة', late, 'تجاوزت الموعد المجدول', 'int', 'neg' if late else ''),
            _stat('قيمة الهدر (Scrap)', self._round(scrap_value), 'خلال الفترة', 'money', 'neg' if scrap_value else ''),
        ]
        if 'stock.request.order' in self.env:
            pend = self.env['stock.request.order'].sudo()._read_group(
                [('state', 'in', ('submitted', 'ready', 'in_transit'))], ['state'], ['__count'])
            labels = {'submitted': 'بانتظار المستودع', 'ready': 'جاهزة', 'in_transit': 'في الطريق'}
            total = sum(c for _s, c in pend)
            stats.append(_stat('طلبات الفروع المفتوحة', total,
                               ' · '.join('%s %d' % (labels.get(s, s), c) for s, c in pend) or 'لا يوجد',
                               'int', 'warn' if total else ''))
        if 'stock.inventory' in self.env and 'wait_for_approval' in dict(
                self.env['stock.inventory']._fields['state'].selection):
            wait = self.env['stock.inventory'].sudo().search_count([('state', '=', 'wait_for_approval')])
            stats.append(_stat('جرد بانتظار الاعتماد', wait, '', 'int', 'warn' if wait else ''))
        return {
            'key': 'inventory', 'title': 'المخزون',
            'link': self._action_link('stock.dashboard_open_quants') or self._action_link('stock.action_view_inventory_tree'),
            'note': 'القيمة = الكمية × التكلفة الحالية للصنف، في مستودعات نقاط البيع المحددة.',
            'stats': stats,
            'tables': [{'title': 'أعلى 10 أصناف قيمةً', 'head': ['الصنف', 'الكمية', 'الوحدة', 'التكلفة', 'القيمة'],
                        'kinds': ['text', 'qty', 'text', 'money', 'money'],
                        'rows': [[r[1], self._round(r[2], 3), r[3], self._round(r[4]), self._round(r[0])] for r in rows[:10]]}],
        }

    # --------------------------------------------------------------- purchase
    def _sec_purchase(self, scope, configs, analytic_ids):
        if not self._has('purchase.order'):
            return None
        companies = self._companies(configs)
        dt_from, dt_to = self._bounds(scope['d_from'], scope['d_to'])
        PO = self.env['purchase.order'].sudo()
        dom = [('company_id', 'in', companies.ids), ('state', 'in', ('purchase', 'done')),
               ('date_approve', '>=', dt_from), ('date_approve', '<=', dt_to)]
        spend = sum(a for (a,) in PO._read_group(dom, [], ['amount_untaxed:sum']))
        count = PO.search_count(dom)
        vendors = PO._read_group(dom, ['partner_id'], ['amount_untaxed:sum', '__count'],
                                 order='amount_untaxed:sum desc', limit=10)
        open_dom = [('company_id', 'in', companies.ids), ('state', '=', 'purchase'),
                    ('receipt_status', 'in', ('pending', 'partial'))]
        to_receive = PO.search_count(open_dom)
        late = PO.search_count(open_dom + [('date_planned', '<', fields.Datetime.now())])
        to_approve = PO.search_count([('company_id', 'in', companies.ids), ('state', '=', 'to approve')])
        bills, overdue = 0.0, 0
        if 'account.move' in self.env:
            Move = self.env['account.move'].sudo()
            bdom = [('company_id', 'in', companies.ids), ('move_type', '=', 'in_invoice'),
                    ('state', '=', 'posted'), ('payment_state', 'in', ('not_paid', 'partial'))]
            bills = sum(a for (a,) in Move._read_group(bdom, [], ['amount_residual_signed:sum']))
            overdue = Move.search_count(bdom + [('invoice_date_due', '<', fields.Date.today())])
        return {
            'key': 'purchase', 'title': 'المشتريات',
            'link': self._action_link('purchase.purchase_form_action'),
            'stats': [
                _stat('مشتريات الفترة', self._round(spend), '%d أمر شراء معتمد (قبل الضريبة)' % count),
                _stat('بانتظار الاستلام', to_receive, 'أوامر لم تُستلم بالكامل', 'int'),
                _stat('استلام متأخر', late, 'تجاوزت تاريخ الاستلام المتوقع', 'int', 'neg' if late else ''),
                _stat('بانتظار الاعتماد', to_approve, '', 'int', 'warn' if to_approve else ''),
                _stat('فواتير موردين مستحقة', self._round(abs(bills)), '%d فاتورة متأخرة السداد' % overdue,
                      'money', 'neg' if overdue else ''),
            ],
            'tables': [{'title': 'أكبر الموردين في الفترة', 'head': ['المورد', 'الأوامر', 'المبلغ'],
                        'kinds': ['text', 'int', 'money'],
                        'rows': [[p.display_name, c, self._round(a)] for p, a, c in vendors]}],
        }

    # ------------------------------------------------------------- accounting
    def _aml_sum(self, companies, types, d_from, d_to, analytic_ids=None):
        params = {'types': tuple(types), 'companies': tuple(companies.ids) or (0,),
                  'df': d_from, 'dt': d_to}
        analytic = ''
        if analytic_ids:
            analytic = """AND aml.analytic_distribution IS NOT NULL AND EXISTS (
                SELECT 1 FROM jsonb_object_keys(aml.analytic_distribution) k
                WHERE string_to_array(k, ',') && %(analytic)s)"""
            params['analytic'] = [str(i) for i in analytic_ids]
        self.env.cr.execute("""
SELECT COALESCE(SUM(aml.balance), 0)
FROM account_move_line aml JOIN account_account a ON a.id = aml.account_id
WHERE aml.parent_state = 'posted' AND aml.company_id IN %(companies)s
  AND a.account_type IN %(types)s AND aml.date BETWEEN %(df)s AND %(dt)s {analytic}
""".format(analytic=analytic), params)
        return self.env.cr.fetchone()[0] or 0.0

    def _sec_accounting(self, scope, configs, analytic_ids):
        if not self._has('account.move.line', 'account.journal'):
            return None
        companies = self._companies(configs)
        d_from, d_to = scope['d_from'], scope['d_to']
        revenue = -self._aml_sum(companies, INCOME, d_from, d_to, analytic_ids)
        cogs = self._aml_sum(companies, COGS, d_from, d_to, analytic_ids)
        expenses = self._aml_sum(companies, EXPENSES, d_from, d_to, analytic_ids)
        gross = revenue - cogs
        net = gross - expenses
        AML = self.env['account.move.line'].sudo()
        journals = self.env['account.journal'].sudo().search(
            [('company_id', 'in', companies.ids), ('type', 'in', ('cash', 'bank'))])
        jrows = []
        for j in journals:
            if not j.default_account_id:
                continue
            bal = sum(b for (b,) in AML._read_group(
                [('account_id', '=', j.default_account_id.id), ('parent_state', '=', 'posted')],
                [], ['balance:sum']))
            jrows.append([j.name, 'نقدي' if j.type == 'cash' else 'بنك', self._round(bal)])
        cash_total = sum(r[2] for r in jrows)
        open_dom = [('company_id', 'in', companies.ids), ('parent_state', '=', 'posted'), ('reconciled', '=', False)]
        receivable = sum(a for (a,) in AML._read_group(
            open_dom + [('account_type', '=', 'asset_receivable')], [], ['amount_residual:sum']))
        payable = -sum(a for (a,) in AML._read_group(
            open_dom + [('account_type', '=', 'liability_payable')], [], ['amount_residual:sum']))
        tax_dom = [('company_id', 'in', companies.ids), ('parent_state', '=', 'posted'),
                   ('tax_line_id', '!=', False), ('date', '>=', d_from), ('date', '<=', d_to)]
        vat_out = -sum(b for (b,) in AML._read_group(tax_dom + [('tax_line_id.type_tax_use', '=', 'sale')], [], ['balance:sum']))
        vat_in = sum(b for (b,) in AML._read_group(tax_dom + [('tax_line_id.type_tax_use', '=', 'purchase')], [], ['balance:sum']))

        # Six months of revenue vs costs for the chart
        labels, rev_s, cost_s = [], [], []
        first = d_to.replace(day=1)
        for i in range(5, -1, -1):
            y, m = first.year, first.month - i
            while m <= 0:
                m += 12
                y -= 1
            m_from = date(y, m, 1)
            m_to = (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1))
            labels.append(m_from.strftime('%Y-%m'))
            rev_s.append(self._round(-self._aml_sum(companies, INCOME, m_from, m_to, analytic_ids)))
            cost_s.append(self._round(self._aml_sum(companies, COGS + EXPENSES, m_from, m_to, analytic_ids)))
        margin = round(gross / revenue * 100, 1) if revenue else None
        return {
            'key': 'accounting', 'title': 'المحاسبة',
            'link': self._action_link('account.action_account_moves_all') or self._action_link('account.action_move_journal_line'),
            'note': ('مفلترة على مراكز تكلفة الفروع المحددة.' if analytic_ids else
                     'على مستوى الشركة. اربط مراكز تكلفة بنقاط البيع لعرض كل فرع على حدة.') +
                    ' الإيراد والتكاليف من القيود المرحّلة فقط.',
            'stats': [
                _stat('الإيرادات', self._round(revenue), 'حسابات الدخل خلال الفترة'),
                _stat('تكلفة المبيعات', self._round(cogs), 'حسابات التكلفة المباشرة'),
                _stat('مجمل الربح', self._round(gross), '%s%% من الإيراد' % margin if margin is not None else '',
                      'money', 'pos' if gross >= 0 else 'neg'),
                _stat('المصاريف التشغيلية', self._round(expenses), ''),
                _stat('صافي النتيجة', self._round(net), 'مجمل الربح − المصاريف', 'money', 'pos' if net >= 0 else 'neg'),
                _stat('النقد والبنوك', self._round(cash_total), '%d صندوق/حساب' % len(jrows), 'money', 'accent'),
                _stat('ذمم العملاء', self._round(receivable), 'غير محصّلة'),
                _stat('ذمم الموردين', self._round(payable), 'غير مسددة'),
                _stat('ضريبة القيمة المضافة المستحقة', self._round(vat_out - vat_in),
                      'مخرجات %s − مدخلات %s' % (self._round(vat_out), self._round(vat_in)), 'money', 'warn'),
            ],
            'chart': {'type': 'bar', 'labels': labels, 'series': [
                {'label': 'الإيرادات', 'data': rev_s, 'tone': 'pos'},
                {'label': 'التكاليف والمصاريف', 'data': cost_s, 'tone': 'neg'}]},
            'tables': [{'title': 'أرصدة الصناديق والبنوك', 'head': ['الدفتر', 'النوع', 'الرصيد'],
                        'kinds': ['text', 'text', 'money'], 'rows': jrows}],
        }

    # ---------------------------------------------------------- manufacturing
    def _sec_manufacturing(self, scope, configs, analytic_ids):
        if not self._has('mrp.production'):
            return None
        companies = self._companies(configs)
        dt_from, dt_to = self._bounds(scope['d_from'], scope['d_to'])
        MO = self.env['mrp.production'].sudo()
        base = [('company_id', 'in', companies.ids)]
        done_dom = base + [('state', '=', 'done'), ('date_finished', '>=', dt_from), ('date_finished', '<=', dt_to)]
        done = MO.search_count(done_dom)
        top = MO._read_group(done_dom, ['product_id'], ['product_qty:sum', '__count'],
                             order='__count desc', limit=10)
        running = MO.search_count(base + [('state', 'in', ('confirmed', 'progress', 'to_close'))])
        late = MO.search_count(base + [('state', 'in', ('confirmed', 'progress')),
                                       ('date_start', '<', fields.Datetime.now())])
        no_recipe = 0
        if 'mrp.bom' in self.env:
            Product = self.env['product.template'].sudo()
            pos_items = Product.search([('available_in_pos', '=', True), ('type', '=', 'consu'),
                                        ('company_id', 'in', companies.ids + [False])])
            with_bom = set(self.env['mrp.bom'].sudo().search(
                [('product_tmpl_id', 'in', pos_items.ids)]).mapped('product_tmpl_id').ids)
            no_recipe = len([p for p in pos_items if p.id not in with_bom and not getattr(p, 'combo_ids', False)])
        return {
            'key': 'manufacturing', 'title': 'الإنتاج والوصفات',
            'link': self._action_link('mrp.mrp_production_action'),
            'stats': [
                _stat('أوامر إنتاج منجزة', done, 'خلال الفترة', 'int', 'pos'),
                _stat('قيد التنفيذ', running, '', 'int'),
                _stat('متأخرة', late, 'تجاوزت تاريخ البدء', 'int', 'neg' if late else ''),
                _stat('أصناف بيع بدون وصفة', no_recipe, 'لا تدخل في تكلفة الرسبي', 'int', 'warn' if no_recipe else ''),
            ],
            'tables': [{'title': 'أكثر الأصناف إنتاجاً', 'head': ['الصنف', 'الأوامر', 'الكمية'],
                        'kinds': ['text', 'int', 'qty'],
                        'rows': [[p.display_name, c, self._round(q, 3)] for p, q, c in top]}],
        }

    # ----------------------------------------------------------------- people
    def _ess_pending(self):
        """Pending self-service requests across every bs_hr_ess request model."""
        out = []
        for name in self.env.registry:
            if name == 'bs.hr.request.mixin':
                continue
            model = self.env.registry[name]
            inherits = model._inherit if isinstance(model._inherit, (list, tuple)) else [model._inherit]
            if 'bs.hr.request.mixin' in inherits and not model._abstract and not model._transient:
                n = self.env[name].sudo().search_count([('state', 'in', ('submitted', 'in_approval'))])
                if n:
                    out.append([self.env[name]._description or name, n])
        return out

    def _sec_people(self, scope, configs, analytic_ids):
        if not self._has('hr.employee'):
            return None
        companies = self._companies(configs)
        Emp = self.env['hr.employee'].sudo()
        employees = Emp.search_count([('company_id', 'in', companies.ids)])
        stats = [_stat('الموظفون', employees, '', 'int')]
        tables = []
        dt_from, dt_to = self._bounds(scope['d_from'], scope['d_to'])
        today_from, today_to = self._bounds(fields.Date.context_today(self), fields.Date.context_today(self))
        if 'hr.attendance' in self.env:
            Att = self.env['hr.attendance'].sudo()
            present = Att.search_count([('employee_id.company_id', 'in', companies.ids),
                                        ('check_out', '=', False), ('check_in', '>=', today_from)])
            stats.append(_stat('على رأس العمل الآن', present, 'سجّلوا حضوراً اليوم ولم ينصرفوا', 'int', 'pos'))
            att = Att.search([('employee_id.company_id', 'in', companies.ids),
                              ('check_in', '>=', dt_from), ('check_in', '<=', dt_to)])
            hours = sum(att.mapped('worked_hours'))
            stats.append(_stat('ساعات العمل', self._round(hours, 1), 'خلال الفترة', 'qty'))
            if 'hourly_cost' in Emp._fields:
                labor = sum(a.worked_hours * (a.employee_id.hourly_cost or 0.0) for a in att)
                Order = self.env['pos.order'].sudo()
                sales = sum(t - x for t, x in Order._read_group(
                    [('config_id', 'in', configs.ids), ('state', 'in', ('paid', 'done', 'invoiced')),
                     ('date_order', '>=', dt_from), ('date_order', '<=', dt_to)],
                    [], ['amount_total:sum', 'amount_tax:sum']))
                pct = round(labor / sales * 100, 1) if sales else None
                stats.append(_stat('تكلفة العمالة', self._round(labor),
                                   'الساعات × تكلفة الساعة للموظف' + (' · %s%% من المبيعات' % pct if pct is not None else ''),
                                   'money', 'warn' if pct and pct > 30 else ''))
        if 'hr.leave' in self.env:
            Leave = self.env['hr.leave'].sudo()
            on_leave = Leave.search_count([('employee_id.company_id', 'in', companies.ids), ('state', '=', 'validate'),
                                           ('date_from', '<=', today_to), ('date_to', '>=', today_from)])
            waiting = Leave.search_count([('employee_id.company_id', 'in', companies.ids),
                                          ('state', 'in', ('confirm', 'validate1'))])
            stats.append(_stat('في إجازة اليوم', on_leave, '', 'int'))
            stats.append(_stat('إجازات بانتظار الاعتماد', waiting, '', 'int', 'warn' if waiting else ''))
        if 'bs.hr.request.mixin' in self.env:
            pend = self._ess_pending()
            stats.append(_stat('طلبات الخدمة الذاتية', sum(n for _l, n in pend), 'بانتظار الموافقة',
                               'int', 'warn' if pend else ''))
            tables.append({'title': 'طلبات الموظفين المعلّقة', 'head': ['النوع', 'العدد'],
                           'kinds': ['text', 'int'], 'rows': pend})
        if 'hr.payslip' in self.env:
            slips = self.env['hr.payslip'].sudo()._read_group(
                [('company_id', 'in', companies.ids), ('state', 'in', ('done', 'paid')),
                 ('date_to', '>=', scope['d_from']), ('date_from', '<=', scope['d_to'])],
                [], ['net_wage:sum'])
            stats.append(_stat('صافي الرواتب', self._round(sum(a for (a,) in slips)), 'قسائم الفترة'))
        return {'key': 'people', 'title': 'الموظفون', 'stats': stats, 'tables': tables,
                'link': self._action_link('hr.open_view_employee_list_my'),
                'note': 'على مستوى الشركة.'}

    # ----------------------------------------------------------------- guests
    def _sec_guests(self, scope, configs, analytic_ids):
        Order = self.env['pos.order'].sudo()
        if 'nps_score' not in Order._fields:
            return None
        dt_from, dt_to = self._bounds(scope['d_from'], scope['d_to'])
        dom = [('config_id', 'in', configs.ids), ('date_order', '>=', dt_from),
               ('date_order', '<=', dt_to), ('nps_score', '>=', 0)]
        dist = dict(Order._read_group(dom, ['nps_score'], ['__count']))
        n = sum(dist.values())
        prom = sum(c for s, c in dist.items() if s >= 9)
        detr = sum(c for s, c in dist.items() if s <= 6)
        nps = round((prom - detr) / n * 100) if n else None
        avg = round(sum(s * c for s, c in dist.items()) / n, 1) if n else None
        low = Order.search(dom + [('nps_score', '<=', 6)], order='date_order desc', limit=10)
        return {
            'key': 'guests', 'title': 'رضا الضيوف (NPS)',
            'stats': [
                _stat('صافي نقاط الترويج', nps if nps is not None else '—', 'المروّجون − المنتقدون', 'int',
                      'pos' if nps and nps > 30 else ('neg' if nps is not None and nps < 0 else '')),
                _stat('التقييمات', n, 'من صفحة تتبع الطلب', 'int'),
                _stat('متوسط التقييم', avg if avg is not None else '—', 'من 10', 'text'),
                _stat('مروّجون (9-10)', prom, '', 'int', 'pos'),
                _stat('منتقدون (0-6)', detr, '', 'int', 'neg' if detr else ''),
            ],
            'chart': {'type': 'bar', 'labels': [str(i) for i in range(11)],
                      'series': [{'label': 'عدد التقييمات', 'data': [dist.get(i, 0) for i in range(11)], 'tone': 'accent'}]},
            'tables': [{'title': 'آخر التقييمات المنخفضة', 'head': ['الطلب', 'نقطة البيع', 'التاريخ', 'التقييم'],
                        'kinds': ['text', 'text', 'text', 'int'],
                        'rows': [[o.name, o.config_id.name,
                                  fields.Datetime.context_timestamp(self, o.date_order).strftime('%Y-%m-%d %H:%M'),
                                  o.nps_score] for o in low]}],
        }
