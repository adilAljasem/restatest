"""Restaurant profile (one per company) and the state of each level of its journey."""
import json
import logging
from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

_logger = logging.getLogger(__name__)

# Python weekday() -> profile field. The Saudi work week starts on Sunday.
WEEKDAY_FIELDS = {6: "off_sun", 0: "off_mon", 1: "off_tue", 2: "off_wed", 3: "off_thu", 4: "off_fri", 5: "off_sat"}
REGIONS = [
    ("riyadh", "منطقة الرياض"), ("makkah", "منطقة مكة المكرمة"), ("madinah", "منطقة المدينة المنورة"),
    ("eastern", "المنطقة الشرقية"), ("qassim", "منطقة القصيم"), ("asir", "منطقة عسير"), ("tabuk", "منطقة تبوك"),
    ("hail", "منطقة حائل"), ("northern", "منطقة الحدود الشمالية"), ("jazan", "منطقة جازان"),
    ("najran", "منطقة نجران"), ("baha", "منطقة الباحة"), ("jouf", "منطقة الجوف"),
]
LEVEL_STATES = [("locked", "مقفل"), ("progress", "قيد التقدم"), ("ready", "جاهز للاستلام"), ("achieved", "مُنجز")]


class RestaGrowthHoliday(models.Model):
    _name = "resta.growth.holiday"
    _description = "إجازة معلنة"
    _order = "date_from desc"

    profile_id = fields.Many2one("resta.growth.profile", required=True, ondelete="cascade")
    name = fields.Char("السبب", required=True)
    date_from = fields.Date("من", required=True)
    date_to = fields.Date("إلى", required=True)


class RestaGrowthLevelState(models.Model):
    _name = "resta.growth.level.state"
    _description = "حالة مستوى في رحلة المطعم"
    _order = "level_sequence"

    profile_id = fields.Many2one("resta.growth.profile", required=True, ondelete="cascade", index=True)
    level_id = fields.Many2one("resta.growth.level", required=True, ondelete="cascade")
    level_sequence = fields.Integer(related="level_id.sequence", store=True)
    state = fields.Selection(LEVEL_STATES, default="locked", required=True)
    ready_since = fields.Datetime(readonly=True)
    started_date = fields.Date(readonly=True)
    achieved_date = fields.Datetime(readonly=True)
    skipped = fields.Boolean("تم التخطي (اشتراك كامل)", readonly=True)

    _uniq = models.Constraint("UNIQUE(profile_id, level_id)", "حالة المستوى مكررة.")


class RestaGrowthProfile(models.Model):
    _name = "resta.growth.profile"
    _description = "ملف المطعم في رحلة النمو"
    _rec_name = "company_id"

    company_id = fields.Many2one("res.company", required=True, ondelete="cascade",
                                 default=lambda s: s.env.company)
    restaurant_type_id = fields.Many2one("resta.growth.restaurant.type", "نوع المطعم")
    city = fields.Char("المدينة", default="الرياض")
    region = fields.Selection(REGIONS, "المنطقة", default="riyadh")
    seats = fields.Integer("عدد المقاعد")
    branches = fields.Integer("عدد الفروع", default=1)
    tz = fields.Char(default="Asia/Riyadh")
    activation_date = fields.Date("تاريخ التفعيل", required=True, default=fields.Date.context_today)
    plan = fields.Selection([("trial", "تجربة"), ("standard", "اشتراك قياسي"), ("paid_full", "مدفوع بالكامل")],
                            "الخطة", default="trial", required=True)
    off_sun = fields.Boolean("الأحد")
    off_mon = fields.Boolean("الإثنين")
    off_tue = fields.Boolean("الثلاثاء")
    off_wed = fields.Boolean("الأربعاء")
    off_thu = fields.Boolean("الخميس")
    off_fri = fields.Boolean("الجمعة")
    off_sat = fields.Boolean("السبت")
    holiday_ids = fields.One2many("resta.growth.holiday", "profile_id", "الإجازات المعلنة")
    consent_benchmark = fields.Boolean("المشاركة في المقارنات المجهولة", default=True)
    consent_league = fields.Boolean("المشاركة في الدوريات", default=True)
    consent_financing = fields.Boolean("مشاركة المؤشر مع شركاء التمويل")
    welcome_state = fields.Selection([("new", "لم يظهر"), ("shown", "ظهر ولم يكتمل"), ("done", "مكتمل")],
                                     default="new", required=True)
    level_state_ids = fields.One2many("resta.growth.level.state", "profile_id", "المستويات")
    current_level_id = fields.Many2one("resta.growth.level", "المستوى الحالي", readonly=True)
    avg_ticket_baseline = fields.Float("خط أساس متوسط الفاتورة", readonly=True)
    waste_pct_baseline = fields.Float("خط أساس نسبة الهدر", readonly=True)
    score = fields.Integer("مؤشر الانضباط", readonly=True)
    score_band = fields.Char("الشريحة", readonly=True)
    quality_index = fields.Integer("مؤشر جودة الإدخالات", readonly=True)
    last_compute = fields.Datetime("آخر حساب", readonly=True)
    simulated = fields.Boolean("بيانات المحاكي", readonly=True)

    _company_uniq = models.Constraint("UNIQUE(company_id)", "يوجد ملف لهذه الشركة.")

    # ----------------------------------------------------------------- access
    @api.model
    def _get(self, company=None):
        company = company or self.env.company
        profile = self.sudo().search([("company_id", "=", company.id)], limit=1)
        if not profile:
            profile = self.sudo().create({"company_id": company.id})
        return profile

    @api.model_create_multi
    def create(self, vals_list):
        profiles = super().create(vals_list)
        for profile in profiles:
            profile._ensure_level_states()
        return profiles

    def _ensure_level_states(self):
        Level = self.env["resta.growth.level"].sudo()
        for profile in self:
            have = profile.level_state_ids.level_id
            missing = Level.search([("id", "not in", have.ids)])
            if missing:
                self.env["resta.growth.level.state"].sudo().create(
                    [{"profile_id": profile.id, "level_id": lvl.id} for lvl in missing])
            if not profile.current_level_id:
                first = profile.level_state_ids.sorted("level_sequence")[:1]
                if first:
                    first.write({"state": "progress", "started_date": fields.Date.context_today(self)})
                    profile.current_level_id = first.level_id
            if profile.plan == "paid_full":
                profile._apply_paid_full()

    # -------------------------------------------------------------- calendar
    def _is_planned_day(self, day):
        self.ensure_one()
        if self.activation_date and day < self.activation_date:
            return False
        if self[WEEKDAY_FIELDS[day.weekday()]]:
            return False
        return not any(h.date_from <= day <= h.date_to for h in self.holiday_ids)

    def _month_index(self, day=None):
        """1-based subscription month since activation."""
        self.ensure_one()
        day = day or fields.Date.context_today(self)
        delta = relativedelta(day, self.activation_date)
        return delta.years * 12 + delta.months + 1

    # -------------------------------------------------------------- plan
    def write(self, vals):
        protected = {"plan", "activation_date", "current_level_id", "simulated"} & set(vals)
        if protected and not (self.env.su or self.env.user.has_group("resta_growth.group_resta_support")):
            raise AccessError(_("هذه البيانات يغيّرها فريق ريستا فقط."))
        res = super().write(vals)
        if vals.get("plan") == "paid_full":
            self._apply_paid_full()
        return res

    def _apply_paid_full(self):
        """Existing restaurants on a full paid plan: every feature open at once."""
        self.env["resta.growth.feature"].sudo().search([("unlocked", "=", False)])._unlock()

    # -------------------------------------------------------------- transitions
    def _states_by_seq(self):
        return {s.level_sequence: s for s in self.level_state_ids}

    def _refresh_level_state(self):
        """After the conditions were computed: progress -> ready when every condition is met."""
        Status = self.env["resta.growth.condition.status"].sudo()
        for profile in self:
            state = profile.level_state_ids.filtered(lambda s: s.level_id == profile.current_level_id)
            if not state or state.state not in ("progress", "ready"):
                continue
            conditions = profile.current_level_id.condition_ids.filtered("active")
            if not conditions:
                continue  # a level without conditions yet ("coming soon") is never ready
            statuses = Status.search([("profile_id", "=", profile.id), ("condition_id", "in", conditions.ids)])
            all_met = len(statuses) == len(conditions) and all(statuses.mapped("met"))
            if all_met and state.state == "progress":
                state.write({"state": "ready", "ready_since": fields.Datetime.now()})
                profile._notify("ready")

    def _check_owner(self):
        user = self.env.user
        if not (self.env.su or user.has_group("resta_growth.group_resta_owner")):
            raise AccessError(_("استلام الجائزة لصاحب الحساب فقط."))

    def action_claim_reward(self):
        """«استلم جائزتك»: achieve the current level, unlock its features, start the next one."""
        self.ensure_one()
        self._check_owner()
        profile = self.sudo()
        states = profile._states_by_seq()
        current = profile.current_level_id
        state = states.get(current.sequence)
        if not state or state.state != "ready":
            raise UserError(_("المستوى لم يكتمل بعد."))
        return profile._achieve(state)

    def _achieve(self, state, skipped=False):
        self.ensure_one()
        now = fields.Datetime.now()
        level = state.level_id
        state.write({"state": "achieved", "achieved_date": now, "skipped": skipped})
        unlocked = level.feature_ids._unlock()
        next_state = self.level_state_ids.filtered(
            lambda s: s.level_sequence > level.sequence and s.state != "achieved").sorted("level_sequence")[:1]
        vals = {}
        if next_state:
            next_state.write({"state": "progress", "started_date": fields.Date.context_today(self)})
            vals["current_level_id"] = next_state.level_id.id
            if next_state.level_id.code == "profitability":
                vals["avg_ticket_baseline"] = self._avg_ticket(30)
        if vals:
            self.write(vals)
            self._refresh_level_state()  # the next level may already be complete
        self._notify("achieved")
        return self._celebration_data(level, unlocked)

    def action_skip_level(self):
        """Full paid plan: the owner may skip the current level (journey and score stay visible)."""
        self.ensure_one()
        self._check_owner()
        profile = self.sudo()
        if profile.plan != "paid_full":
            raise UserError(_("التخطي متاح للاشتراك المدفوع بالكامل فقط."))
        state = profile._states_by_seq().get(profile.current_level_id.sequence)
        if state and state.state in ("progress", "ready"):
            return profile._achieve(state, skipped=True)
        return False

    def _avg_ticket(self, days):
        today = fields.Date.context_today(self)
        rows = self.env["resta.growth.daily"].sudo().search([
            ("company_id", "=", self.company_id.id), ("date", ">", today - timedelta(days=days))])
        orders = sum(rows.mapped("orders"))
        return round(sum(rows.mapped("sales_total")) / orders, 2) if orders else 0.0

    def _data_days(self):
        return self.env["resta.growth.daily"].sudo().search_count([
            ("company_id", "=", self.company_id.id), ("orders", ">", 0)])

    def _celebration_data(self, level, features):
        message = (level.celebration or "").replace("{days}", str(self._data_days()))
        first = features[:1] or level.feature_ids[:1]
        return {
            "level": level.sequence, "name": level.name, "color": level.color, "icon": level.icon,
            "message": message, "reward": level.reward or "",
            "feature": first.ui_data()[0] if first else False,
            "entry_action": first.entry_action_xmlid or False,
            "tour": first.tour_name or False,
        }

    def _notify(self, kind):
        """Live refresh of the badge for the owners and managers of this database."""
        group = self.env.ref("resta_growth.group_resta_manager", raise_if_not_found=False)
        if not group:
            return
        for user in group.sudo().all_user_ids:
            user._bus_send("resta_growth/update", {"kind": kind})

    # ------------------------------------------------------------- welcome
    @api.model
    def welcome_mark_shown(self):
        profile = self._get()
        if profile.welcome_state == "new":
            profile.welcome_state = "shown"
        return True

    @api.model
    def welcome_save(self, vals):
        """Steps 3 and 5 of the welcome wizard (profile + consents). Owner only."""
        self.browse()._check_owner()
        allowed = {"restaurant_type_id", "city", "region", "seats", "branches", "consent_benchmark",
                   "consent_league", "consent_financing", "welcome_state"} | set(WEEKDAY_FIELDS.values())
        clean = {k: v for k, v in (vals or {}).items() if k in allowed}
        profile = self._get()
        if clean:
            profile.write(clean)
            if any(k in WEEKDAY_FIELDS.values() for k in clean):
                # planned days changed: refresh the flags of the stored days
                for row in self.env["resta.growth.daily"].sudo().search([("company_id", "=", profile.company_id.id)]):
                    planned = profile._is_planned_day(row.date)
                    if row.planned != planned:
                        row.planned = planned
        return True

    @api.model
    def add_holiday(self, name, date_from, date_to):
        self.browse()._check_owner()
        profile = self._get()
        profile.holiday_ids = [(0, 0, {"name": name, "date_from": date_from, "date_to": date_to})]
        Daily = self.env["resta.growth.daily"].sudo()
        for row in Daily.search([("company_id", "=", profile.company_id.id),
                                 ("date", ">=", date_from), ("date", "<=", date_to)]):
            row.planned = profile._is_planned_day(row.date)
        return True

    @api.model
    def _reset_for_new_tenant(self, business_type=None):
        """A database cloned from a template starts its own journey today."""
        profile = self._get()
        ctx = {"resta_growth_simulator": True}
        self.env["resta.growth.score.snapshot"].sudo().with_context(**ctx).search([]).unlink()
        self.env["resta.growth.daily"].sudo().search([]).unlink()
        self.env["resta.growth.condition.status"].sudo().search([]).unlink()
        rtype = self.env["resta.growth.restaurant.type"].sudo().search([("code", "=", business_type)], limit=1) \
            if business_type else False
        profile.write({"activation_date": fields.Date.context_today(self), "welcome_state": "new",
                       "simulated": False, "score": 0, "score_band": False, "quality_index": 0,
                       "restaurant_type_id": rtype.id if rtype else profile.restaurant_type_id.id,
                       "current_level_id": False})
        profile.level_state_ids.write({"state": "locked", "achieved_date": False, "ready_since": False,
                                       "started_date": False, "skipped": False})
        profile._ensure_level_states()
        return profile

    # ------------------------------------------------------------- support
    def action_recompute_now(self):
        """«تحديث الآن» (support team): aggregate, conditions, score."""
        if not (self.env.su or self.env.user.has_group("resta_growth.group_resta_support")):
            raise AccessError(_("لفريق الدعم فقط."))
        Engine = self.env["resta.growth.engine"].sudo()
        for profile in self.sudo():
            Engine._run_for_profile(profile, backfill_days=7)
        return True

    @api.model
    def ui_profile_options(self):
        types = self.env["resta.growth.restaurant.type"].sudo().search([])
        return {
            "types": [{"id": t.id, "name": t.name} for t in types],
            "regions": [{"id": k, "name": v} for k, v in REGIONS],
            "weekdays": [{"field": WEEKDAY_FIELDS[i], "name": self._fields[WEEKDAY_FIELDS[i]].string}
                         for i in (6, 0, 1, 2, 3, 4, 5)],
        }

    def _profile_ui(self):
        self.ensure_one()
        return {
            "restaurant_type_id": self.restaurant_type_id.id or False,
            "restaurant_type": self.restaurant_type_id.name or "",
            "city": self.city or "", "region": self.region or False,
            "seats": self.seats, "branches": self.branches,
            "consent_benchmark": self.consent_benchmark, "consent_league": self.consent_league,
            "consent_financing": self.consent_financing,
            "off_days": {f: self[f] for f in WEEKDAY_FIELDS.values()},
            "holidays": [{"name": h.name, "from": str(h.date_from), "to": str(h.date_to)}
                         for h in self.holiday_ids[:10]],
        }

    def _json(self, value):
        return json.dumps(value, ensure_ascii=False)
