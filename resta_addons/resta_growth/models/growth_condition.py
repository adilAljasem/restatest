"""Data-driven level conditions.

Each condition record points to a `compute_key`; the key selects a Python function
registered below (no safe_eval, no code stored in the database). A function returns
{current_value, target, progress_pct, met, details} and reads the daily facts
(or a few live configuration counts), so the nightly run stays cheap.
"""
from datetime import timedelta

from odoo import api, fields, models

CONDITION_REGISTRY = {}


def register(key, label):
    def deco(fn):
        CONDITION_REGISTRY[key] = (label, fn)
        return fn
    return deco


def _result(current, target, met, progress=None, **details):
    if progress is None:
        progress = 100.0 if met else (min(99.0, 100.0 * current / target) if target else 0.0)
    return {"current_value": round(float(current or 0), 2), "target": float(target or 0),
            "progress_pct": round(max(0.0, min(100.0, progress)), 1), "met": bool(met), "details": details}


def _rows(profile, days, today, include_today=False):
    Daily = profile.env["resta.growth.daily"].sudo()
    end = today if include_today else today - timedelta(days=1)
    return Daily.search([("company_id", "=", profile.company_id.id),
                         ("date", ">", end - timedelta(days=days)), ("date", "<=", end)], order="date")


# ------------------------------------------------------------------ level 1
@register("pos_ready", "الكاشير مركّب")
def _pos_ready(profile, cond, today):
    configs = profile.env["pos.config"].sudo().search([("company_id", "=", profile.company_id.id)])
    ready = configs.filtered(lambda c: c.payment_method_ids)
    return _result(len(ready), cond.target or 1, len(ready) >= (cond.target or 1),
                   summary=profile.env._("%s نقطة بيع جاهزة بطرق دفع مفعّلة", len(ready)) if ready
                   else profile.env._("لا توجد نقطة بيع بطرق دفع مفعّلة"))


@register("menu_complete", "المنيو كامل")
def _menu_complete(profile, cond, today):
    Product = profile.env["product.template"].sudo()
    items = Product.search([("available_in_pos", "=", True), ("sale_ok", "=", True), ("type", "!=", "service"),
                            ("company_id", "in", (False, profile.company_id.id))])
    incomplete = items.filtered(lambda p: p.list_price <= 0 or not p.taxes_id or not p.pos_categ_ids)
    complete = len(items) - len(incomplete)
    target = int(cond.target or 15)
    met = len(items) >= target and not incomplete
    if not items:
        progress = 0.0
    else:
        progress = min(len(items) / target, 1.0) * 70 + (complete / len(items)) * 30
    return _result(complete, target, met, progress=100.0 if met else progress,
                   summary=profile.env._("%(n)s صنف متاح، %(bad)s منها ينقصه سعر أو ضريبة أو تصنيف",
                             n=len(items), bad=len(incomplete)) if incomplete
                   else (profile.env._("%s صنف متاح وكلها مكتملة", len(items)) if items
                         else profile.env._("لا توجد أصناف متاحة في نقطة البيع بعد")),
                   incomplete=[p.display_name for p in incomplete[:8]])


@register("daily_recording", "تسجيل المبيعات يومياً")
def _daily_recording(profile, cond, today):
    Daily = profile.env["resta.growth.daily"].sudo()
    recorded_days = Daily.search_count([("company_id", "=", profile.company_id.id), ("recorded", "=", True),
                                        ("date", "<", today)])
    target = int(cond.target or 30)
    planned = Daily.search([("company_id", "=", profile.company_id.id), ("planned", "=", True),
                            ("date", "<", today)], order="date desc", limit=target)
    coverage = 100.0 * len(planned.filtered("recorded")) / len(planned) if planned else 0.0
    threshold = cond.param_1 or 95.0
    met = recorded_days >= target and coverage >= threshold
    progress = min(recorded_days / target, 1.0) * 80 + min(coverage / threshold, 1.0) * 20 if target else 0
    return _result(recorded_days, target, met, progress=100.0 if met else progress,
                   coverage=round(coverage, 1), threshold=threshold, streak=_streak(planned),
                   summary=profile.env._("%(d)s يوم مسجّل من %(t)s، تغطية أيام التشغيل %(c)s%%",
                             d=recorded_days, t=target, c=round(coverage)))


def _streak(planned_desc):
    n = 0
    for row in planned_desc:
        if not row.recorded:
            break
        n += 1
    return n


@register("zatca_compliance", "فواتير متوافقة مع الزكاة والضريبة")
def _zatca(profile, cond, today):
    rows = _rows(profile, int(cond.window_days or 30), today, include_today=True)
    total = sum(rows.mapped("invoices_total"))
    sent = sum(rows.mapped("invoices_sent"))
    pending = sum(rows.mapped("invoices_pending_48h"))
    ratio = 100.0 * sent / total if total else 0.0
    target = cond.target or 99.0
    met = bool(total) and ratio >= target and pending == 0
    if not total:
        summary = profile.env._("لا توجد فواتير إلكترونية في آخر %s يوم", int(cond.window_days or 30))
    elif pending:
        summary = profile.env._("%s فاتورة معلقة أكثر من 48 ساعة", pending)
    else:
        summary = profile.env._("%(r)s%% من %(t)s فاتورة مقبولة", r=round(ratio, 1), t=total)
    return _result(ratio, target, met, pending=pending, total=total, summary=summary)


# ------------------------------------------------------------------ level 2
@register("weekly_inventory", "جرد أسبوعي متتالٍ")
def _weekly_inventory(profile, cond, today):
    """Consecutive weeks (Sunday -> Saturday) with a count covering >= param_1 % of the stock value."""
    target = int(cond.target or 6)
    min_cov = cond.param_1 or 80.0
    Daily = profile.env["resta.growth.daily"].sudo()
    start_of_week = today - timedelta(days=(today.weekday() + 1) % 7)  # Sunday
    rows = Daily.search([("company_id", "=", profile.company_id.id), ("inventory_count", "=", True),
                         ("date", ">=", start_of_week - timedelta(weeks=target + 8))])
    best = {}
    for r in rows:
        week = r.date - timedelta(days=(r.date.weekday() + 1) % 7)
        best[week] = max(best.get(week, 0.0), r.inventory_coverage)
    streak = 0
    week = start_of_week
    if best.get(week, 0.0) < min_cov:  # current week not finished yet: does not break the chain
        week -= timedelta(weeks=1)
    while best.get(week, 0.0) >= min_cov:
        streak += 1
        week -= timedelta(weeks=1)
    this_week_done = best.get(start_of_week, 0.0) >= min_cov
    return _result(streak, target, streak >= target, streak=streak, this_week_done=this_week_done,
                   min_coverage=min_cov,
                   summary=profile.env._("الأسبوع %(s)s من %(t)s في الجرد", s=min(streak, target), t=target))


@register("waste_ratio", "نسبة الهدر تحت حد نوع المطعم")
def _waste_ratio(profile, cond, today):
    rows = _rows(profile, int(cond.window_days or 42), today)
    consumption = sum(rows.mapped("consumption_value"))
    waste = sum(rows.mapped("waste_value"))
    limit = profile.restaurant_type_id.max_waste_pct or cond.target or 4.0
    ratio = 100.0 * waste / consumption if consumption else 0.0
    met = bool(consumption) and ratio < limit
    progress = 100.0 if met else (0.0 if not consumption else 100.0 * limit / max(ratio, limit))
    return _result(ratio, limit, met, progress=progress, waste=round(waste, 2),
                   consumption=round(consumption, 2),
                   summary=profile.env._("الهدر %(r)s%% والحد %(l)s%%", r=round(ratio, 1), l=limit) if consumption
                   else profile.env._("لا توجد حركة استهلاك مسجلة بعد"))


@register("cash_accuracy", "فروقات الكاشير قريبة من الصفر")
def _cash_accuracy(profile, cond, today):
    rows = _rows(profile, int(cond.window_days or 42), today, include_today=True)
    checked = sum(rows.mapped("sessions_cash_checked"))
    ok = sum(rows.mapped("sessions_cash_ok"))
    ratio = 100.0 * ok / checked if checked else 0.0
    target = cond.target or 90.0
    return _result(ratio, target, bool(checked) and ratio >= target, sessions=checked, ok=ok,
                   summary=profile.env._("%(ok)s من %(n)s جلسة مطابقة (%(r)s%%)", ok=ok, n=checked, r=round(ratio))
                   if checked else profile.env._("لا توجد جلسات مغلقة بعدّ نقدي بعد"))


class RestaGrowthCondition(models.Model):
    _name = "resta.growth.condition"
    _description = "شرط مستوى"
    _order = "level_sequence, sequence, id"

    level_id = fields.Many2one("resta.growth.level", required=True, ondelete="cascade")
    level_sequence = fields.Integer(related="level_id.sequence", store=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    name = fields.Char("الشرط", required=True, translate=True)
    description = fields.Text("الشرح", translate=True)
    compute_key = fields.Selection(lambda self: [(k, v[0]) for k, v in CONDITION_REGISTRY.items()],
                                   "دالة الحساب", required=True)
    target = fields.Float("القيمة المستهدفة")
    window_days = fields.Integer("نافذة الأيام")
    unit = fields.Char("الوحدة")
    param_1 = fields.Float("معامل 1", help="حسب الشرط: نسبة التغطية، الحد الأدنى بالريال، ...")
    param_2 = fields.Float("معامل 2")
    lesson_id = fields.Many2one("resta.growth.lesson", "الدرس / الجولة المرتبطة («ماذا أفعل؟»)")
    tip = fields.Char("نصيحة قصيرة", translate=True)

    def _evaluate(self, profile, today):
        self.ensure_one()
        fn = CONDITION_REGISTRY.get(self.compute_key)
        if not fn:
            return _result(0, self.target, False, summary="—")
        return fn[1](profile, self, today)


class RestaGrowthConditionStatus(models.Model):
    _name = "resta.growth.condition.status"
    _description = "تقدّم شرط"
    _order = "condition_sequence"

    profile_id = fields.Many2one("resta.growth.profile", required=True, ondelete="cascade", index=True)
    condition_id = fields.Many2one("resta.growth.condition", required=True, ondelete="cascade")
    condition_sequence = fields.Integer(related="condition_id.sequence", store=True)
    level_id = fields.Many2one(related="condition_id.level_id", store=True)
    current_value = fields.Float(readonly=True)
    target = fields.Float(readonly=True)
    progress_pct = fields.Float(readonly=True)
    met = fields.Boolean(readonly=True)
    met_since = fields.Datetime(readonly=True)
    last_compute = fields.Datetime(readonly=True)
    details = fields.Json(readonly=True)

    _uniq = models.Constraint("UNIQUE(profile_id, condition_id)", "حالة الشرط مكررة.")

    @api.model
    def _store(self, profile, condition, result):
        rec = self.sudo().search([("profile_id", "=", profile.id), ("condition_id", "=", condition.id)], limit=1)
        now = fields.Datetime.now()
        vals = {
            "current_value": result["current_value"], "target": result["target"],
            "progress_pct": result["progress_pct"], "met": result["met"],
            "details": result["details"], "last_compute": now,
        }
        if result["met"] and not (rec and rec.met):
            vals["met_since"] = now
        elif not result["met"]:
            vals["met_since"] = False
        if rec:
            rec.write(vals)
        else:
            rec = self.sudo().create(dict(vals, profile_id=profile.id, condition_id=condition.id))
        return rec
