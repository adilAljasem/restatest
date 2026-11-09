"""«محاكي الرحلة»: demo data for sales, training and investors.

Support team only, and only when the explicit flag
`resta_growth.simulator_enabled` is set (Settings > رحلة النمو). It writes
synthetic daily facts (flagged `simulated`), never POS orders or journal entries.
"""
import random
from datetime import timedelta

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError

SCENARIOS = {
    # coverage, cash_ok, waste %, inventory weekly prob, invoices ok, growth/day
    "disciplined": (0.99, 0.95, 2.6, 1.0, 0.997, 0.0012),
    "average": (0.93, 0.85, 4.8, 0.65, 0.985, 0.0004),
    "struggling": (0.78, 0.60, 7.5, 0.25, 0.94, -0.0010),
}


class RestaGrowthSimulator(models.AbstractModel):
    _name = "resta.growth.simulator"
    _description = "محاكي رحلة النمو"

    @api.model
    def _check(self):
        if not (self.env.su or self.env.user.has_group("resta_growth.group_resta_support")):
            raise AccessError(_("المحاكي لفريق الدعم فقط."))
        if self.env["ir.config_parameter"].sudo().get_param("resta_growth.simulator_enabled") != "True":
            raise UserError(_("المحاكي غير مفعّل لهذه القاعدة. فعّله من الإعدادات (ليس لقاعدة إنتاج)."))
        return self.env["resta.growth.profile"]._get().sudo().with_context(resta_growth_simulator=True)

    @api.model
    def generate(self, scenario="disciplined", days=120, seed=7):
        profile = self._check()
        rng = random.Random(seed)
        cov, cash_ok, waste_pct, inv_prob, inv_ok, growth = SCENARIOS.get(scenario, SCENARIOS["disciplined"])
        rtype = profile.restaurant_type_id or self.env["resta.growth.restaurant.type"].sudo().search([], limit=1)
        today = fields.Date.context_today(self)
        start = today - timedelta(days=days)
        self._clear(profile)
        profile.write({"activation_date": start, "simulated": True, "restaurant_type_id": rtype.id,
                       "off_fri": False})
        self._ensure_demo_menu(rtype)
        Daily = self.env["resta.growth.daily"].sudo()
        rows = []
        base_orders = rtype.orders_per_day or 120
        ticket = rtype.avg_ticket or 45.0
        for i in range(days + 1):
            day = start + timedelta(days=i)
            planned = profile._is_planned_day(day)
            weekend = day.weekday() in (3, 4)  # Thursday / Friday nights are busier
            works = planned and rng.random() < cov
            orders = int(base_orders * (1 + growth * i) * (1.25 if weekend else 1.0) * rng.uniform(0.85, 1.15)) \
                if works else 0
            avg = ticket * (1 + 0.0006 * i if scenario == "disciplined" else 1) * rng.uniform(0.95, 1.05)
            sales = round(orders * avg, 2)
            untaxed = round(sales / 1.15, 2)
            consumption = round(untaxed * rng.uniform(0.30, 0.34), 2)
            sessions = 1 if works else 0
            ok = 1 if (sessions and rng.random() < cash_ok) else 0
            invoices = orders
            sent = int(round(invoices * min(1.0, rng.gauss(inv_ok, 0.003)))) if invoices else 0
            count = planned and day.weekday() == 6 and rng.random() < inv_prob  # Sunday counts
            rows.append({
                "company_id": profile.company_id.id, "date": day, "simulated": True,
                "planned": planned, "recorded": bool(orders), "orders": orders,
                "sales_total": sales, "sales_untaxed": untaxed, "cash_sales": round(sales * 0.22, 2),
                "refunds_total": round(sales * rng.uniform(0.0, 0.015), 2),
                "backdated_orders": 0 if scenario != "struggling" else int(orders * 0.02),
                "sessions_closed": sessions, "sessions_late": 1 if (sessions and rng.random() > cov) else 0,
                "sessions_cash_checked": sessions, "sessions_cash_ok": ok,
                "cash_diff_abs": 0.0 if ok else round(rng.uniform(15, 120), 2),
                "invoices_total": invoices, "invoices_sent": sent,
                "invoices_pending_48h": 0 if scenario == "disciplined" or rng.random() > 0.05 else 1,
                "consumption_value": consumption,
                "waste_value": round(consumption * waste_pct / 100 * rng.uniform(0.8, 1.2), 2),
                "inventory_count": bool(count),
                "inventory_coverage": round(rng.uniform(82, 97), 1) if count else 0.0,
                "inventory_adjust_value": round(consumption * 7 * rng.uniform(0.01, 0.03), 2) if count else 0.0,
            })
        Daily.create(rows)
        Engine = self.env["resta.growth.engine"].sudo()
        # replay the daily snapshots so the 90-day curve exists
        Snap = self.env["resta.growth.score.snapshot"].sudo().with_context(resta_growth_engine=True)
        for i in range(max(0, days - 89), days):
            day = start + timedelta(days=i)
            res = Engine._compute_score(profile, day)
            Snap.create({"company_id": profile.company_id.id, "date": day, "score": res["score"],
                         "band": res["band"], "forming": res["forming"], "simulated": True,
                         "c_sales": res["components"]["sales"]["points"],
                         "c_inventory": res["components"]["inventory"]["points"],
                         "c_invoices": res["components"]["invoices"]["points"],
                         "c_margin": res["components"]["margin"]["points"],
                         "quality_index": res["quality_index"], "details": res})
        Engine._run_for_profile(profile)  # today: conditions, level state, today's snapshot
        return True

    @api.model
    def _ensure_demo_menu(self, rtype):
        """15+ complete POS items so «المنيو كامل» can be met in a demo database."""
        Product = self.env["product.template"].sudo()
        if Product.search_count([("available_in_pos", "=", True), ("type", "!=", "service")]) >= 15:
            return
        company = self.env.company
        tax = self.env["account.tax"].sudo().search([("company_id", "=", company.id), ("type_tax_use", "=", "sale"),
                                                     ("amount", "=", 15)], limit=1)
        categ = self.env["pos.category"].sudo().search([], limit=1) or \
            self.env["pos.category"].sudo().create({"name": "الأصناف الرئيسية"})
        names = ["برجر كلاسيك", "برجر دبل", "برجر دجاج", "بطاطس", "بطاطس بالجبن", "حلقات بصل", "سلطة",
                 "شاورما دجاج", "شاورما لحم", "صحن مشكل", "مياه", "مشروب غازي", "عصير برتقال", "قهوة", "كيك"]
        Product.create([{"name": n, "list_price": round(8 + i * 2.5, 2), "available_in_pos": True,
                         "is_storable": False, "sale_ok": True, "type": "consu",
                         "pos_categ_ids": [Command.set(categ.ids)],
                         "taxes_id": [Command.set(tax.ids)] if tax else False}
                        for i, n in enumerate(names)])

    @api.model
    def set_level(self, sequence, ready=False):
        """Move the restaurant to level `sequence` (previous levels achieved, features opened)."""
        profile = self._check()
        for st in profile.level_state_ids.sorted("level_sequence"):
            if st.level_sequence < sequence:
                st.write({"state": "achieved", "achieved_date": fields.Datetime.now()})
                st.level_id.feature_ids._unlock()
            elif st.level_sequence == sequence:
                st.write({"state": "ready" if ready else "progress", "started_date": fields.Date.context_today(self),
                          "ready_since": fields.Datetime.now() if ready else False, "achieved_date": False})
                profile.current_level_id = st.level_id
                st.level_id.feature_ids.with_context(resta_growth_simulator=True)._relock_for_simulator()
            else:
                st.write({"state": "locked", "achieved_date": False, "ready_since": False})
                st.level_id.feature_ids.with_context(resta_growth_simulator=True)._relock_for_simulator()
        profile._notify("simulator")
        return True

    @api.model
    def celebration(self, sequence):
        """Preview the level-up celebration of any level (no state change)."""
        profile = self._check()
        level = self.env["resta.growth.level"].sudo().search([("sequence", "=", sequence)], limit=1)
        return profile._celebration_data(level, level.feature_ids)

    @api.model
    def reset(self):
        profile = self._check()
        self._clear(profile)
        profile.write({"simulated": False, "activation_date": fields.Date.context_today(self),
                       "avg_ticket_baseline": 0, "score": 0, "score_band": False, "quality_index": 0})
        self.set_level(1)
        return True

    def _clear(self, profile):
        ctx = {"resta_growth_simulator": True}
        self.env["resta.growth.score.snapshot"].sudo().with_context(**ctx).search(
            [("company_id", "=", profile.company_id.id)]).unlink()
        self.env["resta.growth.daily"].sudo().search([("company_id", "=", profile.company_id.id)]).unlink()
        self.env["resta.growth.condition.status"].sudo().search([("profile_id", "=", profile.id)]).unlink()
