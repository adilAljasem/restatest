"""Payment follow-up (متابعة التحصيل) for catering, corporate and delivery-platform customers.

One screen lists every customer who owes money, how late they are and the next reminder
due. A reminder can be an email with the account statement attached, a WhatsApp message
or a call to make (activity). Levels («friendly reminder after 7 days»...) are configurable
and the daily job sends the automatic ones.
"""
import re
from datetime import timedelta
from urllib.parse import quote

from markupsafe import Markup, escape

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import SQL

ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


class RestaFollowupLevel(models.Model):
    _name = "resta.followup.level"
    _description = "مستوى متابعة التحصيل"
    _order = "delay, id"

    name = fields.Char("المستوى", required=True, translate=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", "الشركة", help="فارغ = كل الشركات")
    delay = fields.Integer("بعد (يوم) من الاستحقاق", required=True, default=7)
    send_email = fields.Boolean("إرسال بريد", default=True)
    attach_statement = fields.Boolean("إرفاق كشف الحساب", default=True)
    create_activity = fields.Boolean("مهمة اتصال", help="تُنشأ مهمة «اتصال» للمسؤول عن العميل.")
    activity_note = fields.Char("نص المهمة", translate=True)
    auto = fields.Boolean("تلقائي", default=True, help="يُنفّذ يومياً بدون تدخل. وإلا يظهر في الشاشة كإجراء مستحق.")
    email_subject = fields.Char("عنوان الرسالة", translate=True, default="تذكير بالمبالغ المستحقة - {company}")
    email_body = fields.Html("نص الرسالة", translate=True, sanitize=True,
                             help="المتغيرات: {partner} {company} {overdue} {total} {days}")
    whatsapp_text = fields.Text("نص واتساب", translate=True,
                                help="المتغيرات: {partner} {company} {overdue} {total} {days}")

    def _render(self, text, values):
        text = text or ""
        for key, value in values.items():
            text = text.replace("{%s}" % key, str(value))
        return text


class ResPartner(models.Model):
    _inherit = "res.partner"

    resta_followup_level_id = fields.Many2one("resta.followup.level", "آخر مستوى متابعة", copy=False,
                                              company_dependent=True)
    resta_followup_date = fields.Date("تاريخ آخر متابعة", copy=False, company_dependent=True)
    resta_followup_next_date = fields.Date("لا تذكير قبل", copy=False, company_dependent=True,
                                           help="أجّل التذكيرات حتى هذا التاريخ (وعد بالسداد).")
    resta_followup_blocked = fields.Boolean("إيقاف المتابعة", copy=False, company_dependent=True)
    resta_followup_user_id = fields.Many2one("res.users", "مسؤول التحصيل", copy=False, company_dependent=True)
    resta_followup_note = fields.Text("ملاحظات التحصيل", copy=False, company_dependent=True)

    # ------------------------------------------------------------------ data
    def _resta_query(self, query):
        self.env.flush_all()
        return self.env.execute_query_dict(query)

    @api.model
    def _resta_open_items(self, partner_ids=None):
        """Open receivable items per commercial partner, for the selected companies."""
        conds = [SQL("aml.company_id = ANY(%s)", self.env.companies.ids), SQL("aml.parent_state = 'posted'"),
                 SQL("acc.account_type = 'asset_receivable'"), SQL("aml.reconciled IS NOT TRUE"),
                 SQL("aml.amount_residual <> 0")]
        if partner_ids:
            conds.append(SQL("p.commercial_partner_id = ANY(%s)", list(partner_ids)))
        query = SQL("""
            SELECT aml.id, p.commercial_partner_id AS partner_id, aml.move_id, m.name AS move,
                   aml.date, COALESCE(aml.date_maturity, aml.date) AS due, aml.amount_residual AS residual,
                   aml.balance AS original, COALESCE(m.ref, aml.name) AS label
              FROM account_move_line aml
              JOIN account_account acc ON acc.id = aml.account_id
              JOIN account_move m ON m.id = aml.move_id
              JOIN res_partner p ON p.id = aml.partner_id
             WHERE %s
          ORDER BY due, aml.id""", SQL(" AND ").join(conds))
        return self._resta_query(query)

    @api.model
    def _resta_levels(self):
        return self.env["resta.followup.level"].search(
            [("company_id", "in", [self.env.company.id, False])], order="delay, id")

    def _resta_due_level(self, max_days, levels=None):
        """Next level to run for this partner: the highest one reached and not done yet."""
        self.ensure_one()
        levels = levels if levels is not None else self._resta_levels()
        current = self.resta_followup_level_id
        reached = levels.filtered(lambda lv: lv.delay <= max_days)
        if not reached:
            return levels.browse()
        target = reached[-1]
        if current and current.delay >= target.delay:
            return levels.browse()
        return target

    @api.model
    def resta_followup_overview(self, only_overdue=False):
        today = fields.Date.context_today(self)
        items = self._resta_open_items()
        partners = {}
        for it in items:
            p = partners.setdefault(it["partner_id"], {"total": 0.0, "overdue": 0.0, "max_days": 0, "count": 0,
                                                       "oldest_due": None})
            p["total"] += it["residual"]
            p["count"] += 1
            days = (today - it["due"]).days
            if days > 0 and it["residual"] > 0:
                p["overdue"] += it["residual"]
                p["max_days"] = max(p["max_days"], days)
                p["oldest_due"] = min(p["oldest_due"] or it["due"], it["due"])
        records = self.browse(list(partners)).exists()
        levels = self._resta_levels()
        rows = []
        for partner in records.sorted(lambda r: -partners[r.id]["overdue"]):
            data = partners[partner.id]
            if data["total"] <= 0.0049 or (only_overdue and data["overdue"] <= 0.0049):
                continue
            due = partner._resta_due_level(data["max_days"], levels) if data["overdue"] > 0 else levels.browse()
            postponed = partner.resta_followup_next_date and partner.resta_followup_next_date > today
            rows.append({
                "id": partner.id, "name": partner.display_name, "phone": partner.phone or "",
                "email": partner.email or "", "total": round(data["total"], 2), "overdue": round(data["overdue"], 2),
                "max_days": data["max_days"], "oldest_due": data["oldest_due"] and str(data["oldest_due"]),
                "count": data["count"],
                "level": partner.resta_followup_level_id.name or "",
                "last_date": partner.resta_followup_date and str(partner.resta_followup_date),
                "next_level": due.name or "", "next_level_id": due.id or False,
                "action_due": bool(due) and not postponed and not partner.resta_followup_blocked,
                "postponed_until": postponed and str(partner.resta_followup_next_date),
                "blocked": partner.resta_followup_blocked,
                "user": partner.resta_followup_user_id.name or "", "note": partner.resta_followup_note or "",
                "whatsapp": partner._resta_whatsapp_url(due or partner.resta_followup_level_id or levels[:1],
                                                        data),
            })
        currency = self.env.company.currency_id
        return {
            "rows": rows,
            "kpi": {"total": round(sum(r["total"] for r in rows), 2), "overdue": round(sum(r["overdue"] for r in rows), 2),
                    "late_customers": sum(1 for r in rows if r["overdue"] > 0),
                    "actions_due": sum(1 for r in rows if r["action_due"])},
            "currency": {"symbol": currency.symbol, "position": currency.position},
            "levels": levels.read(["name", "delay", "auto"]),
        }

    def _resta_values(self, data):
        currency = self.env.company.currency_id
        return {"partner": self.display_name, "company": self.env.company.name,
                "overdue": currency.format(data.get("overdue", 0.0)), "total": currency.format(data.get("total", 0.0)),
                "days": data.get("max_days", 0)}

    def _resta_whatsapp_phone(self):
        digits = re.sub(r"\D", "", (self.phone or "").translate(ARABIC_DIGITS))
        if digits.startswith("00"):
            digits = digits[2:]
        if digits.startswith("05") and len(digits) == 10:
            digits = "966" + digits[1:]
        elif digits.startswith("5") and len(digits) == 9:
            digits = "966" + digits
        return digits

    def _resta_whatsapp_url(self, level, data):
        phone = self._resta_whatsapp_phone()
        if not phone:
            return ""
        text = level._render(level.whatsapp_text, self._resta_values(data)) if level else ""
        return "https://wa.me/%s%s" % (phone, "?text=" + quote(text) if text else "")

    def _resta_partner_data(self):
        self.ensure_one()
        today = fields.Date.context_today(self)
        items = self._resta_open_items([self.commercial_partner_id.id])
        late = [it for it in items if (today - it["due"]).days > 0 and it["residual"] > 0]
        return {"total": sum(it["residual"] for it in items), "overdue": sum(it["residual"] for it in late),
                "max_days": max([(today - it["due"]).days for it in late] or [0]), "items": items}

    # ------------------------------------------------------------------ actions
    def resta_followup_send(self, level_id=False):
        """Run the due level (or the given one) for these customers. Returns a short summary."""
        today = fields.Date.context_today(self)
        levels = self._resta_levels()
        sent, no_email, skipped = 0, [], 0
        for partner in self.commercial_partner_id:
            data = partner._resta_partner_data()
            if data["overdue"] <= 0.0049:
                skipped += 1
                continue
            level = self.env["resta.followup.level"].browse(level_id) if level_id else \
                partner._resta_due_level(data["max_days"], levels) or partner.resta_followup_level_id or levels[:1]
            if not level:
                raise UserError(_("أضف مستويات متابعة التحصيل أولاً (الإعدادات ← متابعة التحصيل)."))
            partner._resta_followup_execute(level, data)
            if level.send_email and not partner.email:
                no_email.append(partner.display_name)
            sent += 1
            partner.write({"resta_followup_level_id": level.id, "resta_followup_date": today})
        return {"sent": sent, "skipped": skipped, "no_email": no_email}

    def _resta_followup_execute(self, level, data):
        self.ensure_one()
        values = self._resta_values(data)
        if level.send_email and self.email:
            attachments = []
            if level.attach_statement:
                pdf, ext = self.env["ir.actions.report"]._render_qweb_pdf(
                    "resta_account.action_report_partner_statement", self.ids)
                attachments.append(("%s - %s.%s" % (_("كشف حساب"), self.display_name, ext), pdf))
            body = level._render(level.email_body or "", {k: escape(v) for k, v in values.items()})
            self.message_post(body=Markup(body), subject=level._render(level.email_subject, values),
                              partner_ids=self.ids, attachments=attachments, message_type="comment",
                              subtype_xmlid="mail.mt_comment")
        else:
            self.message_post(body=_("متابعة التحصيل: %(level)s — المتأخر %(overdue)s منذ %(days)s يوم",
                                     level=level.name, overdue=values["overdue"], days=values["days"]))
        if level.create_activity:
            self.activity_schedule(
                "mail.mail_activity_data_call", date_deadline=fields.Date.context_today(self),
                summary=level.name, note=level._render(level.activity_note or "", values),
                user_id=(self.resta_followup_user_id or self.user_id or self.env.user).id)

    def resta_followup_postpone(self, day):
        self.write({"resta_followup_next_date": day})
        return True

    def resta_followup_set_blocked(self, blocked):
        self.write({"resta_followup_blocked": bool(blocked)})
        return True

    def resta_followup_set_note(self, note):
        self.write({"resta_followup_note": note or False})
        return True

    def action_resta_statement(self):
        return self.env.ref("resta_account.action_report_partner_statement").report_action(self.commercial_partner_id)

    def action_resta_followup_open(self):
        return {"type": "ir.actions.client", "tag": "resta_account.followup", "name": _("متابعة التحصيل"),
                "params": {"partner_id": self.commercial_partner_id.id}}

    @api.model
    def _cron_resta_followup(self):
        for company in self.env["res.company"].search([]):
            env_self = self.sudo().with_company(company).with_context(allowed_company_ids=company.ids)
            overview = env_self.resta_followup_overview(only_overdue=True)
            auto_levels = {lv["id"] for lv in overview["levels"] if lv["auto"]}
            todo = [r["id"] for r in overview["rows"] if r["action_due"] and r["next_level_id"] in auto_levels]
            for partner in env_self.browse(todo):
                partner.resta_followup_send()
            # customers who paid everything start again from the first level next time
            paid = env_self.search([("resta_followup_level_id", "!=", False)])
            late = {r["id"] for r in overview["rows"] if r["overdue"] > 0}
            paid.filtered(lambda p: p.id not in late).write({"resta_followup_level_id": False})


class RestaPartnerStatementReport(models.AbstractModel):
    _name = "report.resta_account.report_partner_statement"
    _description = "كشف حساب العميل"

    @api.model
    def _get_report_values(self, docids, data=None):
        partners = self.env["res.partner"].browse(docids)
        today = fields.Date.context_today(self)
        lines = {}
        for partner in partners:
            items = partner._resta_partner_data()["items"]
            for it in items:
                it["days"] = max((today - it["due"]).days, 0)
            lines[partner.id] = items
        return {"doc_ids": docids, "doc_model": "res.partner", "docs": partners, "lines": lines, "today": today,
                "company": self.env.company, "timedelta": timedelta}
