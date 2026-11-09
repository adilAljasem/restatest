"""Discipline score (0-1000), data-quality index (0-100) and the nightly engine.

The score is computed on the server only, over a moving 90-day window of daily
facts, and stored as one immutable snapshot per company per day.
"""
import logging
import math
from collections import defaultdict
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

DEFAULTS = {
    "w_sales": 300, "w_inventory": 300, "w_invoices": 200, "w_margin": 200,
    "band_1": 400, "band_2": 600, "band_3": 800,
    "quality_threshold": 85, "forming_days": 14, "window_days": 90,
}
BANDS = ["يحتاج تأسيس", "في الطريق", "منضبط", "جاهز لفرع ثانٍ"]


def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


class RestaGrowthScoreSnapshot(models.Model):
    _name = "resta.growth.score.snapshot"
    _description = "لقطة يومية لمؤشر الانضباط"
    _order = "date desc"

    company_id = fields.Many2one("res.company", required=True, index=True, ondelete="cascade")
    date = fields.Date(required=True, index=True, readonly=True)
    score = fields.Integer("المؤشر", readonly=True)
    band = fields.Char("الشريحة", readonly=True)
    forming = fields.Boolean("قيد التكوين", readonly=True)
    c_sales = fields.Integer("انتظام تسجيل المبيعات", readonly=True)
    c_inventory = fields.Integer("دقة الجرد", readonly=True)
    c_invoices = fields.Integer("سلامة الفواتير", readonly=True)
    c_margin = fields.Integer("استقرار الهامش والنمو", readonly=True)
    anomaly_deduction = fields.Integer("خصم التلاعب", readonly=True)
    quality_index = fields.Integer("مؤشر جودة الإدخالات", readonly=True)
    details = fields.Json(readonly=True)
    simulated = fields.Boolean(readonly=True)

    _company_date_uniq = models.Constraint("UNIQUE(company_id, date)", "يوجد لقطة لهذا اليوم.")

    def write(self, vals):
        if not self.env.context.get("resta_growth_engine"):
            raise UserError(_("لقطات المؤشر غير قابلة للتعديل."))
        today = fields.Date.context_today(self)
        if any(s.date != today and not s.simulated for s in self):
            raise UserError(_("لقطات الأيام السابقة غير قابلة للتعديل."))
        return super().write(vals)

    def unlink(self):
        if not self.env.context.get("resta_growth_simulator"):
            raise UserError(_("لقطات المؤشر غير قابلة للحذف."))
        return super().unlink()


class RestaGrowthEngine(models.AbstractModel):
    _name = "resta.growth.engine"
    _description = "محرك رحلة النمو"

    # ------------------------------------------------------------- settings
    @api.model
    def _param(self, key):
        raw = self.env["ir.config_parameter"].sudo().get_param("resta_growth.%s" % key)
        try:
            return float(raw) if raw not in (None, "", False) else float(DEFAULTS[key])
        except ValueError:
            return float(DEFAULTS[key])

    @api.model
    def _band(self, score):
        if score >= self._param("band_3"):
            return BANDS[3]
        if score >= self._param("band_2"):
            return BANDS[2]
        if score >= self._param("band_1"):
            return BANDS[1]
        return BANDS[0]

    # ------------------------------------------------------------- nightly
    @api.model
    def _cron_nightly(self):
        for company in self.env["res.company"].sudo().search([]):
            profile = self.env["resta.growth.profile"]._get(company)
            try:
                with self.env.cr.savepoint():
                    self._run_for_profile(profile)
            except Exception:  # noqa: BLE001 - one company must not stop the others
                _logger.exception("resta_growth: nightly run failed for company %s", company.id)

    @api.model
    def _run_for_profile(self, profile, backfill_days=7):
        Daily = self.env["resta.growth.daily"].sudo()
        company = profile.company_id
        today = fields.Date.context_today(self)
        if not profile.simulated:
            has_rows = Daily.search_count([("company_id", "=", company.id)], limit=1)
            days = backfill_days if has_rows else 120
            Daily._compute_range(company, today - timedelta(days=days), today)
        self._compute_conditions(profile, today)
        profile._refresh_level_state()
        self._snapshot(profile, today)
        profile.last_compute = fields.Datetime.now()
        profile._notify("computed")

    @api.model
    def _compute_conditions(self, profile, today):
        Status = self.env["resta.growth.condition.status"].sudo()
        for cond in self.env["resta.growth.condition"].sudo().search([]):
            try:
                result = cond._evaluate(profile, today)
            except Exception:  # noqa: BLE001
                _logger.exception("resta_growth: condition %s failed", cond.compute_key)
                continue
            Status._store(profile, cond, result)

    # ------------------------------------------------------------- score
    @api.model
    def _snapshot(self, profile, day):
        result = self._compute_score(profile, day)
        Snap = self.env["resta.growth.score.snapshot"].sudo().with_context(resta_growth_engine=True)
        vals = {
            "score": result["score"], "band": result["band"], "forming": result["forming"],
            "c_sales": result["components"]["sales"]["points"],
            "c_inventory": result["components"]["inventory"]["points"],
            "c_invoices": result["components"]["invoices"]["points"],
            "c_margin": result["components"]["margin"]["points"],
            "anomaly_deduction": result["anomaly_deduction"],
            "quality_index": result["quality_index"],
            "details": result, "simulated": profile.simulated,
        }
        snap = Snap.search([("company_id", "=", profile.company_id.id), ("date", "=", day)], limit=1)
        if snap:
            snap.write(vals)
        else:
            snap = Snap.create(dict(vals, company_id=profile.company_id.id, date=day))
        profile.write({"score": result["score"], "score_band": result["band"],
                       "quality_index": result["quality_index"]})
        return snap

    @api.model
    def _compute_score(self, profile, day):
        window = int(self._param("window_days"))
        Daily = self.env["resta.growth.daily"].sudo()
        start = max(day - timedelta(days=window - 1), profile.activation_date or day)
        rows = Daily.search([("company_id", "=", profile.company_id.id),
                             ("date", ">=", start), ("date", "<=", day)], order="date")
        data_days = len(rows.filtered(lambda r: r.orders > 0))
        forming = data_days < self._param("forming_days")
        comps = {
            "sales": self._c_sales(profile, rows, self._param("w_sales")),
            "inventory": self._c_inventory(profile, rows, self._param("w_inventory")),
            "invoices": self._c_invoices(rows, self._param("w_invoices")),
            "margin": self._c_margin(rows, day, self._param("w_margin")),
        }
        deduction = 0  # anomaly rules arrive in phase 2
        score = int(round(sum(c["points"] for c in comps.values()) - deduction))
        score = max(0, min(1000, score))
        return {
            "score": score, "band": self._band(score), "forming": forming, "data_days": data_days,
            "components": comps, "anomaly_deduction": deduction,
            "quality_index": self._quality_index(profile, day),
        }

    def _comp(self, weight, ratio, reason, tip, lesson=None, parts=None):
        return {"points": int(round(weight * clamp(ratio))), "max": int(weight), "ratio": round(clamp(ratio), 3),
                "reason": reason, "tip": tip, "lesson": lesson, "parts": parts or {}}

    def _c_sales(self, profile, rows, w):
        planned = rows.filtered("planned")
        cov = len(planned.filtered("recorded")) / len(planned) if planned else 0.0
        closed = sum(rows.mapped("sessions_closed"))
        on_time = 1 - (sum(rows.mapped("sessions_late")) / closed) if closed else 0.0
        checked = sum(rows.mapped("sessions_cash_checked"))
        orders = sum(rows.mapped("orders"))
        cash = sum(rows.mapped("sessions_cash_ok")) / checked if checked else (0.5 if orders else 0.0)
        backdated = clamp(1 - 10 * (sum(rows.mapped("backdated_orders")) / orders)) if orders else 0.0
        ratio = 0.55 * cov + 0.15 * on_time + 0.20 * cash + 0.10 * backdated
        parts = {"coverage": round(cov * 100), "on_time": round(on_time * 100), "cash": round(cash * 100),
                 "backdated": round(backdated * 100)}
        weakest = min([("coverage", cov), ("on_time", on_time), ("cash", cash), ("backdated", backdated)],
                      key=lambda x: x[1])[0]
        reason = _("سجّلت مبيعات %(c)s%% من أيام التشغيل، وطابق الصندوق في %(k)s%% من الجلسات",
                   c=parts["coverage"], k=parts["cash"])
        tips = {
            "coverage": (_("افتح وأغلق جلسة الكاشير في كل يوم تشغيل، وأعلن أيام العطلة مسبقاً."), "l1_session"),
            "on_time": (_("أغلق الجلسة في نهاية نفس اليوم ولا تتركها مفتوحة لليوم التالي."), "l1_session"),
            "cash": (_("عُدّ الصندوق بدقة عند الإغلاق وسجّل أي سحب أو إيداع نقدي."), "l2_cash"),
            "backdated": (_("سجّل الطلبات وقت حدوثها بدل إدخالها لاحقاً."), "l1_session"),
        }
        return self._comp(w, ratio, reason, tips[weakest][0], tips[weakest][1], parts)

    def _c_inventory(self, profile, rows, w):
        counts = rows.filtered("inventory_count")
        cond = self.env["resta.growth.condition"].sudo().search([("compute_key", "=", "weekly_inventory")], limit=1)
        min_cov = cond.param_1 if cond else 80.0
        span_days = (rows[-1].date - rows[0].date).days + 1 if rows else 0
        weeks_total = max(1, math.ceil(span_days / 7)) if rows else 1
        weeks = defaultdict(float)
        for r in counts:
            week = r.date - timedelta(days=(r.date.weekday() + 1) % 7)
            weeks[week] = max(weeks[week], r.inventory_coverage)
        regular = len([v for v in weeks.values() if v >= min_cov]) / weeks_total
        avg_cov = (sum(weeks.values()) / len(weeks) / 100.0) if weeks else 0.0
        consumption = sum(rows.mapped("consumption_value"))
        variance = (sum(rows.mapped("inventory_adjust_value")) / consumption) if consumption else None
        var_part = clamp((0.10 - variance) / 0.08) if (variance is not None and counts) else 0.0
        ratio = 0.5 * clamp(regular) + 0.2 * clamp(avg_cov) + 0.3 * var_part
        inv_feature = self.env["resta.growth.feature"].sudo().search([("key", "=", "inventory")], limit=1)
        if not counts:
            reason = _("لا يوجد جرد مسجّل في آخر 90 يوماً")
            tip = (_("تُفتح لك إدارة المخزون بإكمال المستوى 1، وبعدها جرد أسبوعي واحد يرفع هذا الجزء.")
                   if inv_feature and not inv_feature.unlocked
                   else _("ابدأ بجرد أسبوعي للمواد الخام الأعلى قيمة."))
            return self._comp(w, ratio, reason, tip, "l2_count", {"regular": 0, "coverage": 0, "variance": None})
        reason = _("جردت في %(n)s من %(t)s أسابيع، وفروقات الجرد %(v)s%% من الاستهلاك",
                   n=len(weeks), t=weeks_total, v=round((variance or 0) * 100, 1))
        if regular < 0.8:
            tip, lesson = _("اجعل الجرد موعداً ثابتاً كل أسبوع (مثلاً صباح الأحد)."), "l2_count"
        elif var_part < 0.7:
            tip, lesson = _("فروقات الجرد عالية: سجّل الهدر فور حدوثه وراجع وصفات الأصناف."), "l2_waste"
        else:
            tip, lesson = _("ممتاز. وسّع الجرد ليغطي كل المواد الخام."), "l2_count"
        return self._comp(w, ratio, reason, tip, lesson, {
            "regular": round(regular * 100), "coverage": round(avg_cov * 100),
            "variance": round((variance or 0) * 100, 1)})

    def _c_invoices(self, rows, w):
        total = sum(rows.mapped("invoices_total"))
        sent = sum(rows.mapped("invoices_sent"))
        pending = sum(rows.mapped("invoices_pending_48h"))
        sales = sum(rows.mapped("sales_total"))
        refunds = sum(rows.mapped("refunds_total"))
        if not total:
            return self._comp(w, 0.0, _("لا توجد فواتير إلكترونية مسجّلة"),
                              _("تأكد أن الربط مع هيئة الزكاة والضريبة مفعّل لنقطة البيع."), "l1_einvoice")
        acc = sent / total
        refund_ratio = refunds / sales if sales else 0.0
        ratio = 0.6 * clamp((acc - 0.80) / 0.19) + 0.25 * (1.0 if not pending else 0.0) \
            + 0.15 * clamp((0.10 - refund_ratio) / 0.07)
        reason = _("%(a)s%% من الفواتير مقبولة، %(p)s معلقة، والمرتجعات %(r)s%% من المبيعات",
                   a=round(acc * 100, 1), p=pending, r=round(refund_ratio * 100, 1))
        tip = (_("راجع الفواتير المعلقة وأعد إرسالها خلال 48 ساعة.") if pending
               else _("استمر: كل فاتورة تُرسل تلقائياً عند الدفع."))
        return self._comp(w, ratio, reason, tip, "l1_einvoice",
                          {"accepted": round(acc * 100, 1), "pending": pending,
                           "refunds": round(refund_ratio * 100, 1)})

    def _c_margin(self, rows, day, w):
        months = defaultdict(lambda: [0.0, 0.0])
        for r in rows:
            m = months[(r.date.year, r.date.month)]
            m[0] += r.sales_untaxed
            m[1] += r.consumption_value
        margins = [100.0 * (s - c) / s for (s, c) in months.values() if s > 0 and c > 0]
        if len(margins) >= 2:
            vol = max(margins) - min(margins)
            stability = clamp((15.0 - vol) / 12.0)
        else:
            vol, stability = None, 0.5
        last = sum(r.sales_total for r in rows if r.date > day - timedelta(days=30))
        prev = sum(r.sales_total for r in rows if day - timedelta(days=60) < r.date <= day - timedelta(days=30))
        if prev:
            growth = (last - prev) / prev
            trend = clamp((growth + 0.20) / 0.20)
        else:
            growth, trend = None, 0.5
        ratio = 0.6 * stability + 0.4 * trend
        if not any(r.sales_total for r in rows):
            ratio = 0.0  # no sales yet: no neutral credit
        if vol is None:
            reason = _("نحتاج بيانات شهرين على الأقل بتكلفة محسوبة لقياس ثبات الهامش")
        else:
            reason = _("تذبذب الهامش %(v)s نقطة، والمبيعات %(g)s%% مقارنة بالشهر السابق",
                       v=round(vol, 1), g=round((growth or 0) * 100, 1))
        tip = (_("احسب تكلفة كل طبق (وصفة) حتى يظهر هامشك الحقيقي.") if vol is None
               else _("ثبّت أسعار الموردين وراجع تسعير الأطباق الأعلى مبيعاً."))
        return self._comp(w, ratio, reason, tip, "l1_menu",
                          {"volatility": vol and round(vol, 1), "growth": growth and round(growth * 100, 1)})

    # ------------------------------------------------------------- quality
    @api.model
    def _quality_index(self, profile, day):
        """0-100, used for the «free month» decision in months 4-6."""
        Daily = self.env["resta.growth.daily"].sudo()
        month_start = day.replace(day=1)
        rows = Daily.search([("company_id", "=", profile.company_id.id),
                             ("date", ">=", max(month_start, profile.activation_date or month_start)),
                             ("date", "<=", day)])
        planned = rows.filtered("planned")
        reg = len(planned.filtered("recorded")) / len(planned) if planned else 0.0
        menu = self.env["resta.growth.condition.status"].sudo().search([
            ("profile_id", "=", profile.id), ("condition_id.compute_key", "=", "menu_complete")], limit=1)
        items = 0.0
        if menu and menu.details:
            n = menu.current_value or 0
            bad = len(menu.details.get("incomplete") or [])
            items = 1.0 if menu.met else (n / (n + bad) if (n + bad) else 0.0)
        inv_feature = self.env["resta.growth.feature"].sudo().search([("key", "=", "inventory")], limit=1)
        if inv_feature and not inv_feature.unlocked:
            inv = 1.0  # cannot be blamed for a locked feature
        else:
            weeks_elapsed = max(1, math.ceil(((day - month_start).days + 1) / 7))
            weeks = {r.date - timedelta(days=(r.date.weekday() + 1) % 7) for r in rows if r.inventory_count}
            inv = clamp(len(weeks) / weeks_elapsed)
        total = sum(rows.mapped("invoices_total"))
        inv_ok = clamp(((sum(rows.mapped("invoices_sent")) / total) - 0.80) / 0.19) if total else 0.0
        return int(round(40 * reg + 20 * items + 20 * inv + 20 * inv_ok))
