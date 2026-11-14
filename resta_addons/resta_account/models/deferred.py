"""Deferred expenses and revenue: yearly rent paid in advance, a 6-month maintenance contract,
a catering contract invoiced up front...

Put a start and an end date on the invoice / bill line. When the document is posted, its
amount is moved to a deferral account (prepaid expenses / deferred revenue) on the
document date, then recognised month by month, prorated by days, in the line's own account
and branch. Future months are created as drafts that Odoo posts automatically on their date.
"""
import calendar
from collections import defaultdict

from dateutil.relativedelta import relativedelta

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_is_zero

REVENUE_TYPES = ("income", "income_other")
EXPENSE_TYPES = ("expense", "expense_other", "expense_direct_cost", "expense_depreciation")


def _segments(start, end):
    """[(segment start, segment end)] cut at month ends."""
    out, day = [], start
    while day <= end:
        last = day.replace(day=calendar.monthrange(day.year, day.month)[1])
        out.append((day, min(last, end)))
        day = last + relativedelta(days=1)
    return out


class ResCompany(models.Model):
    _inherit = "res.company"

    resta_deferred_enabled = fields.Boolean("المصروفات والإيرادات المؤجلة", default=True)
    resta_deferred_expense_account_id = fields.Many2one("account.account", "حساب المصروفات المدفوعة مقدماً")
    resta_deferred_revenue_account_id = fields.Many2one("account.account", "حساب الإيرادات المؤجلة")
    resta_deferred_journal_id = fields.Many2one("account.journal", "يومية قيود التأجيل")

    def _resta_deferred_setup(self):
        """Pick (or create) the deferral accounts and journal the first time they are needed."""
        self.ensure_one()
        Account = self.env["account.account"].sudo().with_company(self)
        vals = {}
        if not self.resta_deferred_expense_account_id:
            account = Account.search([("account_type", "=", "asset_prepayments"),
                                      ("company_ids", "in", self.id)], limit=1)
            vals["resta_deferred_expense_account_id"] = (account or self._resta_new_account(
                "asset_prepayments", "مصروفات مدفوعة مقدماً", "asset_current")).id
        if not self.resta_deferred_revenue_account_id:
            account = Account.search([("account_type", "=", "liability_current"), ("company_ids", "in", self.id),
                                      "|", ("name", "ilike", "deferred revenue"), ("name", "ilike", "مؤجلة")], limit=1)
            vals["resta_deferred_revenue_account_id"] = (account or self._resta_new_account(
                "liability_current", "إيرادات مؤجلة", "liability_current")).id
        if not self.resta_deferred_journal_id:
            journal = self.env["account.journal"].sudo().search([("type", "=", "general"),
                                                                  ("company_id", "=", self.id)], limit=1)
            if not journal:
                raise UserError(_("أنشئ يومية «عمليات متنوعة» لقيود التأجيل."))
            vals["resta_deferred_journal_id"] = journal.id
        if vals:
            self.sudo().write(vals)

    def _resta_new_account(self, account_type, name, like_type):
        Account = self.env["account.account"].sudo().with_company(self)
        sibling = Account.search([("account_type", "=", like_type), ("company_ids", "in", self.id)],
                                 order="code desc", limit=1)
        code = Account._search_new_account_code(sibling.code) if sibling and sibling.code else False
        return Account.create({"name": name, "code": code or ("1199" if account_type.startswith("asset") else "2199"),
                               "account_type": account_type, "company_ids": [Command.set(self.ids)]})


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    resta_deferred_enabled = fields.Boolean(related="company_id.resta_deferred_enabled", readonly=False)
    resta_deferred_expense_account_id = fields.Many2one(
        related="company_id.resta_deferred_expense_account_id", readonly=False,
        domain="[('account_type', 'in', ('asset_prepayments', 'asset_current'))]")
    resta_deferred_revenue_account_id = fields.Many2one(
        related="company_id.resta_deferred_revenue_account_id", readonly=False,
        domain="[('account_type', 'in', ('liability_current', 'liability_non_current'))]")
    resta_deferred_journal_id = fields.Many2one(related="company_id.resta_deferred_journal_id", readonly=False,
                                                domain="[('type', '=', 'general')]")


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    resta_deferred_start = fields.Date("من (تأجيل)", copy=True,
                                       help="بداية الفترة التي يغطيها هذا المبلغ (مثل بداية عقد الإيجار).")
    resta_deferred_end = fields.Date("إلى (تأجيل)", copy=True,
                                     help="نهاية الفترة. يُوزّع المبلغ على أشهرها حسب الأيام.")

    @api.constrains("resta_deferred_start", "resta_deferred_end")
    def _check_resta_deferred_dates(self):
        for line in self:
            if bool(line.resta_deferred_start) != bool(line.resta_deferred_end):
                raise ValidationError(_("أدخل تاريخ البداية والنهاية معاً في السطر «%s».", line.name or ""))
            if line.resta_deferred_start and line.resta_deferred_start > line.resta_deferred_end:
                raise ValidationError(_("تاريخ بداية التأجيل بعد نهايته في السطر «%s».", line.name or ""))

    def _resta_is_deferred(self):
        self.ensure_one()
        return (self.resta_deferred_start and self.resta_deferred_end
                and self.account_id.account_type in REVENUE_TYPES + EXPENSE_TYPES
                and (self.resta_deferred_start.year, self.resta_deferred_start.month)
                != (self.resta_deferred_end.year, self.resta_deferred_end.month)
                and not self.currency_id.is_zero(self.amount_currency)
                and not self.company_currency_id.is_zero(self.balance))


class AccountMove(models.Model):
    _inherit = "account.move"

    resta_deferred_enabled = fields.Boolean(related="company_id.resta_deferred_enabled")
    resta_deferred_origin_id = fields.Many2one("account.move", "قيد التأجيل من", readonly=True, copy=False,
                                               index="btree_not_null")
    resta_deferred_move_ids = fields.One2many("account.move", "resta_deferred_origin_id", "قيود التأجيل")
    resta_deferred_count = fields.Integer(compute="_compute_resta_deferred_count")

    @api.depends("resta_deferred_move_ids")
    def _compute_resta_deferred_count(self):
        for move in self:
            move.resta_deferred_count = len(move.resta_deferred_move_ids)

    def _post(self, soft=True):
        posted = super()._post(soft)
        posted.filtered(lambda m: m.is_invoice(include_receipts=True)
                        and not m.resta_deferred_origin_id)._resta_generate_deferrals()
        return posted

    def button_draft(self):
        res = super().button_draft()
        self._resta_remove_deferrals()
        return res

    def action_open_resta_deferrals(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "name": _("قيود التأجيل"), "res_model": "account.move",
                "view_mode": "list,form", "domain": [("resta_deferred_origin_id", "=", self.id)],
                "context": {"create": False}}

    def action_open_resta_deferred_origin(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "res_model": "account.move", "view_mode": "form",
                "res_id": self.resta_deferred_origin_id.id}

    def _resta_generate_deferrals(self):
        today = fields.Date.context_today(self)
        for move in self:
            if not move.company_id.resta_deferred_enabled or move.resta_deferred_move_ids:
                continue
            lines = move.invoice_line_ids.filtered(lambda l: l._resta_is_deferred())
            if not lines:
                continue
            company = move.company_id
            company._resta_deferred_setup()
            # date -> [(account, analytic distribution, balance)]
            by_date = defaultdict(list)
            for line in lines:
                deferral = (company.resta_deferred_revenue_account_id if line.account_id.account_type in REVENUE_TYPES
                            else company.resta_deferred_expense_account_id)
                total = line.balance
                by_date[move.date].append((line.account_id, line.analytic_distribution, -total, line.name))
                by_date[move.date].append((deferral, False, total, line.name))
                segments = _segments(line.resta_deferred_start, line.resta_deferred_end)
                days = (line.resta_deferred_end - line.resta_deferred_start).days + 1
                done = 0.0
                for i, (start, end) in enumerate(segments):
                    if i == len(segments) - 1:
                        share = total - done
                    else:
                        share = company.currency_id.round(total * ((end - start).days + 1) / days)
                    done += share
                    if company.currency_id.is_zero(share):
                        continue
                    day = max(end, move.date)
                    by_date[day].append((line.account_id, line.analytic_distribution, share, line.name))
                    by_date[day].append((deferral, False, -share, line.name))
            created = self.env["account.move"]
            for day in sorted(by_date):
                created |= self._resta_deferral_move(move, day, by_date[day], first=day == move.date and not created)
            to_post = created.filtered(lambda m: m.date <= today)
            to_post.action_post()
            (created - to_post).write({"auto_post": "at_date"})

    def _resta_deferral_move(self, origin, day, items, first=False):
        merged = defaultdict(float)
        labels = {}
        for account, analytic, balance, label in items:
            key = (account.id, tuple(sorted((analytic or {}).items())))
            merged[key] += balance
            labels.setdefault(key, label)
        currency = origin.company_id.currency_id
        line_vals = []
        for (account_id, analytic), balance in merged.items():
            if float_is_zero(balance, precision_rounding=currency.rounding):
                continue
            line_vals.append(Command.create({
                "name": labels[(account_id, analytic)], "account_id": account_id,
                "debit": balance if balance > 0 else 0.0, "credit": -balance if balance < 0 else 0.0,
                "analytic_distribution": dict(analytic) or False, "partner_id": origin.partner_id.id,
            }))
        return self.env["account.move"].create({
            "move_type": "entry", "date": day, "journal_id": origin.company_id.resta_deferred_journal_id.id,
            "ref": (_("تأجيل: %s") if first else _("إثبات مؤجل: %s")) % (origin.name or ""),
            "resta_deferred_origin_id": origin.id, "line_ids": line_vals,
        })

    def _resta_remove_deferrals(self):
        deferrals = self.resta_deferred_move_ids
        if not deferrals:
            return
        drafts = deferrals.filtered(lambda m: m.state == "draft")
        posted = deferrals.filtered(lambda m: m.state == "posted")
        drafts.write({"auto_post": "no"})
        drafts.with_context(force_delete=True).unlink()
        if posted:
            today = fields.Date.context_today(self)
            reversals = posted._reverse_moves([{"date": max(m.date, today),
                                                "ref": _("عكس: %s", m.ref or "")} for m in posted], cancel=True)
            (posted | reversals).write({"resta_deferred_origin_id": False})
        deferrals.exists().filtered(lambda m: m.state == "cancel").write({"resta_deferred_origin_id": False})
