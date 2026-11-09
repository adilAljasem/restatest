"""Read-only data for the web client: one RPC for the badge, one for the journey page.

Everything here reads stored results (statuses, snapshots); nothing is computed
from the raw tables when the owner opens a screen.
"""
from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError


class RestaGrowthEngineUI(models.AbstractModel):
    _inherit = "resta.growth.engine"

    @api.model
    def _role(self):
        user = self.env.user
        if user.has_group("resta_growth.group_resta_support"):
            return "support"
        if user.has_group("resta_growth.group_resta_owner"):
            return "owner"
        if user.has_group("resta_growth.group_resta_manager"):
            return "manager"
        return False

    @api.model
    def _subscription(self, profile):
        today = fields.Date.context_today(self)
        icp = self.env["ir.config_parameter"].sudo()
        free = int(self._param_int("free_months", 3))
        half_until = int(self._param_int("half_until_month", 6))
        threshold = int(self._param("quality_threshold"))
        month = profile._month_index(today)
        q = profile.quality_index
        res = {"month": month, "quality": q, "threshold": threshold, "plan": profile.plan,
               "platform_state": icp.get_param("resta.subscription_state") or "",
               "platform_days_left": int(icp.get_param("resta.days_left") or 0)}
        if profile.plan == "paid_full":
            res.update(stage="paid_full", title=_("اشتراك مدفوع بالكامل"),
                       line=_("كل الميزات مفتوحة، والرحلة والمؤشر مستمران معك."))
        elif month <= free:
            end = profile.activation_date + relativedelta(months=free)
            res.update(stage="free", title=_("الشهر %(m)s من %(f)s المجانية", m=month, f=free),
                       days_left=(end - today).days,
                       line=_("مرحلة التعلم: باقي %s يوم مجانية", (end - today).days))
        elif month <= half_until:
            ok = q >= threshold
            res.update(stage="half", title=_("الشهر %s: خصم 50%%", month), free_this_month=ok,
                       line=(_("مؤشر جودة إدخالاتك %(q)s من %(t)s المطلوبة: هذا الشهر مجاني إن حافظت عليه",
                               q=q, t=threshold) if ok else
                             _("ارفع مؤشر جودة الإدخالات إلى %(t)s ليصبح هذا الشهر مجانياً (الآن %(q)s)",
                               t=threshold, q=q)))
        else:
            res.update(stage="full", title=_("اشتراك كامل"),
                       line=_("انضباطك يحفظ لك المزايا: رسوم أقل وخصومات الفروع."))
        return res

    @api.model
    def _param_int(self, key, default):
        raw = self.env["ir.config_parameter"].sudo().get_param("resta_growth.%s" % key)
        try:
            return int(raw) if raw else default
        except ValueError:
            return default

    @api.model
    def _conditions_ui(self, profile, level):
        statuses = {s.condition_id.id: s for s in self.env["resta.growth.condition.status"].sudo().search(
            [("profile_id", "=", profile.id), ("condition_id.level_id", "=", level.id)])}
        out = []
        for cond in level.condition_ids.filtered("active"):
            s = statuses.get(cond.id)
            details = (s.details if s else None) or {}
            out.append({
                "id": cond.id, "key": cond.compute_key, "name": cond.name, "description": cond.description or "",
                "progress": s.progress_pct if s else 0.0, "met": bool(s and s.met),
                "current": s.current_value if s else 0.0, "target": s.target if s else cond.target,
                "summary": details.get("summary") or _("لم يُحسب بعد: يُحدّث كل ليلة"),
                "details": details, "tip": cond.tip or "",
                "lesson": cond.lesson_id and {"key": cond.lesson_id.key, "name": cond.lesson_id.name,
                                              "tour": cond.lesson_id.tour_name or ""},
            })
        return out

    @api.model
    def get_badge_data(self):
        role = self._role()
        if not role:
            return {"visible": False}
        profile = self.env["resta.growth.profile"]._get()
        levels = []
        current_state = False
        for st in profile.level_state_ids.sorted("level_sequence"):
            lvl = st.level_id
            levels.append({"seq": lvl.sequence, "code": lvl.code, "name": lvl.name, "subtitle": lvl.subtitle or "",
                           "color": lvl.color, "icon": lvl.icon, "state": st.state, "reward": lvl.reward or "",
                           "reward_note": lvl.reward_note or "", "skipped": st.skipped,
                           "features": lvl.feature_ids.ui_data(),
                           "has_conditions": bool(lvl.condition_ids.filtered("active"))})
            if lvl == profile.current_level_id:
                current_state = st.state
        level = profile.current_level_id
        conditions = self._conditions_ui(profile, level) if level else []
        progress = round(sum(c["progress"] for c in conditions) / len(conditions)) if conditions else 0
        snaps = self.env["resta.growth.score.snapshot"].sudo().search(
            [("company_id", "=", profile.company_id.id)], limit=8)
        last = snaps[:1]
        week_ago = snaps.filtered(lambda s: last and s.date <= last.date - timedelta(days=7))[:1]
        trend = (last.score - week_ago.score) if (last and week_ago) else 0
        if last and not last.forming and trend:
            insight = (_("مؤشرك ارتفع %s نقطة خلال أسبوع. استمر!", trend) if trend > 0
                       else _("مؤشرك نزل %s نقطة خلال أسبوع. شوف النصائح في صفحة الرحلة.", -trend))
        elif last and last.forming:
            insight = _("مؤشرك قيد التكوين: يظهر بعد 14 يوماً من البيانات (عندك %s).",
                        (last.details or {}).get("data_days", 0))
        else:
            insight = _("أول حساب للمؤشر الليلة الساعة 3 فجراً.")
        is_owner = role in ("owner", "support")
        return {
            "visible": True, "role": role, "is_owner": is_owner, "is_support": role == "support",
            "level": level and {"seq": level.sequence, "name": level.name, "color": level.color,
                                "icon": level.icon, "reward": level.reward or "",
                                "reward_note": level.reward_note or "",
                                "features": level.feature_ids.ui_data(),
                                "state": current_state, "has_conditions": bool(conditions)},
            "levels": levels, "conditions": conditions, "progress": progress,
            "can_claim": is_owner and current_state == "ready",
            "can_skip": is_owner and profile.plan == "paid_full" and current_state in ("progress", "ready"),
            "score": {"value": last.score if last else 0, "band": last.band if last else "",
                      "forming": bool(last.forming) if last else True, "trend": trend,
                      "date": str(last.date) if last else ""},
            "quality": profile.quality_index,
            "subscription": self._subscription(profile) if is_owner else False,
            "league": False,  # phase 3 (hub)
            "insight": insight,
            "welcome": profile.welcome_state if is_owner else "done",
            "user_name": self.env.user.name,
            "profile_id": profile.id,
            "business_name": profile.company_id.name,
        }

    @api.model
    def get_journey_data(self):
        data = self.get_badge_data()
        if not data.get("visible"):
            raise AccessError(_("رحلة النمو لصاحب الحساب ومدير الفرع."))
        profile = self.env["resta.growth.profile"]._get()
        Snap = self.env["resta.growth.score.snapshot"].sudo()
        last = Snap.search([("company_id", "=", profile.company_id.id)], limit=1)
        history = Snap.search([("company_id", "=", profile.company_id.id)], limit=90, order="date desc")
        details = (last.details or {}) if last else {}
        comps = details.get("components") or {}
        labels = {"sales": _("انتظام تسجيل المبيعات"), "inventory": _("دقة الجرد ومطابقته"),
                  "invoices": _("سلامة الفواتير"), "margin": _("استقرار الهامش والنمو")}
        Lesson = self.env["resta.growth.lesson"].sudo()
        done = self.env["resta.growth.lesson.progress"]._done_lesson_ids()
        lessons_by_key = {l.key: l for l in Lesson.search([])}
        components = []
        for key in ("sales", "inventory", "invoices", "margin"):
            c = comps.get(key) or {}
            lesson = lessons_by_key.get(c.get("lesson") or "")
            components.append({"key": key, "label": labels[key], "points": c.get("points", 0),
                               "max": c.get("max", 0), "ratio": c.get("ratio", 0), "reason": c.get("reason", ""),
                               "tip": c.get("tip", ""), "parts": c.get("parts", {}),
                               "lesson": lesson and {"key": lesson.key, "name": lesson.name,
                                                     "tour": lesson.tour_name or ""}})
        icp = self.env["ir.config_parameter"].sudo()
        data.update({
            "components": components,
            "anomaly_deduction": last.anomaly_deduction if last else 0,
            "history": [{"date": str(s.date), "score": s.score, "forming": s.forming}
                        for s in history.sorted("date")],
            "bands": [int(self._param("band_1")), int(self._param("band_2")), int(self._param("band_3"))],
            "lessons": Lesson.search([("level_id.sequence", "<=", 2)])._ui(done)
                       + Lesson.search([("level_id", "=", False)])._ui(done),
            "profile": profile._profile_ui(),
            "profile_options": profile.ui_profile_options(),
            "last_compute": fields.Datetime.to_string(profile.last_compute) if profile.last_compute else "",
            "simulator": data["is_support"] and icp.get_param("resta_growth.simulator_enabled") == "True",
            "simulated": profile.simulated,
        })
        return data

    @api.model
    def get_welcome_data(self):
        role = self._role()
        if role not in ("owner", "support"):
            return {"visible": False}
        Profile = self.env["resta.growth.profile"]
        profile = Profile._get()
        Profile.welcome_mark_shown()
        levels = [{"seq": l.sequence, "name": l.name, "subtitle": l.subtitle or "", "reward": l.reward or "",
                   "color": l.color, "icon": l.icon}
                  for l in self.env["resta.growth.level"].sudo().search([])]
        return {
            "visible": True,
            "owner_name": self.env.user.name,
            "business_name": profile.company_id.name,
            "levels": levels,
            "profile": profile._profile_ui(),
            "options": Profile.ui_profile_options(),
            "subscription": self._subscription(profile),
        }
