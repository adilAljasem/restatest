"""Fixed assets for restaurants (kitchen equipment, furniture, fit-out, vehicles).

Straight-line monthly depreciation, posted automatically every month. An asset can be
created by hand or straight from a vendor bill line booked on an asset account.
"""
import calendar

from dateutil.relativedelta import relativedelta

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_is_zero


def _month_end(day):
    return day.replace(day=calendar.monthrange(day.year, day.month)[1])


class RestaAssetType(models.Model):
    _name = "resta.asset.type"
    _description = "نوع الأصل"
    _order = "name"
    _check_company_auto = True

    name = fields.Char("النوع", required=True, translate=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda s: s.env.company)
    months = fields.Integer("مدة الإهلاك (شهر)", required=True, default=60)
    prorata = fields.Boolean("حسب الأيام في الشهر الأول", default=True,
                             help="أول قسط يُحسب من تاريخ الشراء حتى نهاية الشهر، والباقي يُضاف في آخر شهر.")
    asset_account_id = fields.Many2one("account.account", "حساب الأصل", required=True, check_company=True,
                                       domain="[('account_type', 'in', ('asset_fixed', 'asset_non_current'))]")
    depreciation_account_id = fields.Many2one("account.account", "مجمع الإهلاك", required=True, check_company=True,
                                              domain="[('account_type', 'in', ('asset_fixed', 'asset_non_current'))]")
    expense_account_id = fields.Many2one("account.account", "مصروف الإهلاك", required=True, check_company=True,
                                         domain="[('account_type', 'in', ('expense_depreciation', 'expense'))]")
    journal_id = fields.Many2one("account.journal", "اليومية", required=True, check_company=True,
                                 domain="[('type', '=', 'general')]")
    auto_from_bills = fields.Boolean("إنشاء أصل تلقائياً من فواتير الموردين", default=True,
                                     help="كل سطر فاتورة على حساب الأصل ينشئ أصلاً (مسودة) لتراجعه وتؤكده.")


class RestaAsset(models.Model):
    _name = "resta.asset"
    _description = "أصل ثابت"
    _inherit = ["mail.thread", "analytic.mixin"]
    _order = "acquisition_date desc, id desc"
    _check_company_auto = True

    name = fields.Char("الأصل", required=True, tracking=True)
    code = fields.Char("الرمز")
    type_id = fields.Many2one("resta.asset.type", "النوع", check_company=True, tracking=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda s: s.env.company)
    currency_id = fields.Many2one(related="company_id.currency_id")
    state = fields.Selection([("draft", "مسودة"), ("open", "قيد الإهلاك"), ("closed", "مُهلك بالكامل"),
                              ("disposed", "مستبعد")], string="الحالة", default="draft", tracking=True,
                             required=True)
    acquisition_date = fields.Date("تاريخ الشراء", required=True, default=fields.Date.context_today, tracking=True)
    original_value = fields.Monetary("قيمة الشراء", required=True, tracking=True)
    salvage_value = fields.Monetary("القيمة التخريدية")
    months = fields.Integer("مدة الإهلاك (شهر)", required=True, default=60, tracking=True)
    prorata = fields.Boolean("حسب الأيام في الشهر الأول", default=True)
    already_depreciated = fields.Monetary("إهلاك سابق (قبل النظام)",
                                          help="لأصل قديم: ما أُهلك قبل بداية استخدام النظام.")
    asset_account_id = fields.Many2one("account.account", "حساب الأصل", required=True, check_company=True)
    depreciation_account_id = fields.Many2one("account.account", "مجمع الإهلاك", required=True, check_company=True)
    expense_account_id = fields.Many2one("account.account", "مصروف الإهلاك", required=True, check_company=True)
    journal_id = fields.Many2one("account.journal", "اليومية", required=True, check_company=True)
    partner_id = fields.Many2one("res.partner", "المورد")
    bill_line_id = fields.Many2one("account.move.line", "سطر الفاتورة", readonly=True, copy=False)
    line_ids = fields.One2many("resta.asset.line", "asset_id", "جدول الإهلاك", copy=False)
    depreciated_value = fields.Monetary("الإهلاك المرحّل", compute="_compute_values")
    book_value = fields.Monetary("القيمة الدفترية", compute="_compute_values")
    disposal_date = fields.Date("تاريخ الاستبعاد", readonly=True, copy=False)
    disposal_move_id = fields.Many2one("account.move", "قيد الاستبعاد", readonly=True, copy=False)
    note = fields.Text("ملاحظات")

    @api.depends("line_ids.move_id.state", "line_ids.amount", "original_value", "already_depreciated")
    def _compute_values(self):
        for asset in self:
            posted = sum(asset.line_ids.filtered(lambda l: l.move_id.state == "posted").mapped("amount"))
            asset.depreciated_value = posted + asset.already_depreciated
            asset.book_value = asset.original_value - asset.depreciated_value

    @api.onchange("type_id")
    def _onchange_type_id(self):
        t = self.type_id
        if t:
            self.update({"months": t.months, "prorata": t.prorata, "asset_account_id": t.asset_account_id,
                         "depreciation_account_id": t.depreciation_account_id,
                         "expense_account_id": t.expense_account_id, "journal_id": t.journal_id})

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            t = self.env["resta.asset.type"].browse(vals.get("type_id"))
            if t:
                for field in ("asset_account_id", "depreciation_account_id", "expense_account_id", "journal_id"):
                    vals.setdefault(field, t[field].id)
                vals.setdefault("months", t.months)
                vals.setdefault("prorata", t.prorata)
        return super().create(vals_list)

    @api.constrains("original_value", "salvage_value", "months", "already_depreciated")
    def _check_values(self):
        for asset in self:
            if asset.months <= 0:
                raise ValidationError(_("مدة الإهلاك يجب أن تكون شهراً واحداً على الأقل."))
            if asset.original_value < 0 or asset.salvage_value < 0 \
                    or asset.salvage_value + asset.already_depreciated > asset.original_value:
                raise ValidationError(_("القيمة التخريدية والإهلاك السابق لا يتجاوزان قيمة الشراء."))

    # ------------------------------------------------------------------ board
    def _board(self):
        """[(date, amount)] straight-line, month ends, rounding absorbed by the last month."""
        self.ensure_one()
        cur = self.currency_id
        base = self.original_value - self.salvage_value - self.already_depreciated
        if float_is_zero(base, precision_rounding=cur.rounding):
            return []
        monthly = base / self.months
        start = self.acquisition_date
        schedule = []
        if self.prorata and start.day > 1:
            days_in_month = calendar.monthrange(start.year, start.month)[1]
            first = monthly * (days_in_month - start.day + 1) / days_in_month
            schedule.append((_month_end(start), first))
            months = self.months
        else:
            months = self.months
            schedule.append((_month_end(start), monthly))
            months -= 1
        day = _month_end(start)
        for _i in range(months):
            day = _month_end(day + relativedelta(days=1))
            schedule.append((day, monthly))
        rounded, total = [], 0.0
        for i, (d, amount) in enumerate(schedule):
            amount = cur.round(amount) if i < len(schedule) - 1 else cur.round(base - total)
            total += amount
            if not float_is_zero(amount, precision_rounding=cur.rounding):
                rounded.append((d, amount))
        return rounded

    def action_compute_board(self):
        for asset in self:
            if asset.line_ids.filtered("move_id"):
                raise UserError(_("الجدول فيه قيود مرحّلة؛ لا يمكن إعادة حسابه."))
            asset.line_ids.unlink()
            remaining = asset.original_value - asset.salvage_value - asset.already_depreciated
            vals, cumulative = [], asset.already_depreciated
            for d, amount in asset._board():
                cumulative += amount
                remaining -= amount
                vals.append({"asset_id": asset.id, "date": d, "amount": amount, "cumulative": cumulative,
                             "remaining": asset.original_value - cumulative})
            self.env["resta.asset.line"].create(vals)
        return True

    def action_confirm(self):
        for asset in self.filtered(lambda a: a.state == "draft"):
            if not asset.line_ids:
                asset.action_compute_board()
            asset.state = "open"
        self._post_due(fields.Date.context_today(self))
        return True

    def action_draft(self):
        for asset in self:
            if asset.line_ids.filtered("move_id"):
                raise UserError(_("لا يمكن إرجاع أصل له قيود إهلاك مرحّلة إلى مسودة."))
        self.state = "draft"

    def _post_due(self, until):
        lines = self.line_ids.filtered(lambda l: not l.move_id and l.date <= until and l.asset_id.state == "open")
        lines._create_move()
        for asset in self.filtered(lambda a: a.state == "open"):
            if asset.line_ids and all(asset.line_ids.mapped("move_id")):
                asset.state = "closed"

    @api.model
    def _cron_post_depreciation(self):
        today = fields.Date.context_today(self)
        for company in self.env["res.company"].search([]):
            assets = self.with_company(company).search([("state", "=", "open"), ("company_id", "=", company.id)])
            assets._post_due(today)

    def action_open_dispose(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "res_model": "resta.asset.dispose", "view_mode": "form",
                "target": "new", "context": {"default_asset_id": self.id}}

    def _dispose(self, day, loss_account, sale_amount=0.0, sale_account=None):
        self.ensure_one()
        if self.state not in ("open", "closed"):
            raise UserError(_("الأصل يجب أن يكون مؤكداً."))
        self._post_due(day)
        self.line_ids.filtered(lambda l: not l.move_id).unlink()
        accumulated = self.depreciated_value
        cur = self.currency_id
        lines = [
            Command.create({"name": self.name, "account_id": self.asset_account_id.id, "credit": self.original_value,
                            "debit": 0.0, "analytic_distribution": self.analytic_distribution}),
        ]
        if not float_is_zero(accumulated, precision_rounding=cur.rounding):
            lines.append(Command.create({"name": _("مجمع إهلاك %s", self.name), "debit": accumulated, "credit": 0.0,
                                         "account_id": self.depreciation_account_id.id}))
        if sale_amount and sale_account:
            lines.append(Command.create({"name": _("بيع %s", self.name), "debit": sale_amount, "credit": 0.0,
                                         "account_id": sale_account.id}))
        diff = cur.round(self.original_value - accumulated - (sale_amount or 0.0))
        if not float_is_zero(diff, precision_rounding=cur.rounding):
            lines.append(Command.create({"name": _("ربح/خسارة استبعاد %s", self.name), "account_id": loss_account.id,
                                         "debit": diff if diff > 0 else 0.0, "credit": -diff if diff < 0 else 0.0,
                                         "analytic_distribution": self.analytic_distribution}))
        move = self.env["account.move"].create({"move_type": "entry", "journal_id": self.journal_id.id, "date": day,
                                                "ref": _("استبعاد أصل: %s", self.name), "line_ids": lines})
        move.action_post()
        self.write({"state": "disposed", "disposal_date": day, "disposal_move_id": move.id})
        return move


class RestaAssetLine(models.Model):
    _name = "resta.asset.line"
    _description = "قسط إهلاك"
    _order = "date, id"

    asset_id = fields.Many2one("resta.asset", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="asset_id.company_id", store=True)
    currency_id = fields.Many2one(related="asset_id.currency_id")
    date = fields.Date("التاريخ", required=True)
    amount = fields.Monetary("القسط")
    cumulative = fields.Monetary("الإهلاك المتراكم")
    remaining = fields.Monetary("القيمة الدفترية بعده")
    move_id = fields.Many2one("account.move", "القيد", readonly=True, copy=False)
    posted = fields.Boolean("مرحّل", compute="_compute_posted")

    @api.depends("move_id.state")
    def _compute_posted(self):
        for line in self:
            line.posted = line.move_id.state == "posted"

    def _create_move(self):
        moves = self.env["account.move"]
        for line in self:
            asset = line.asset_id
            move = self.env["account.move"].create({
                "move_type": "entry", "journal_id": asset.journal_id.id, "date": line.date,
                "ref": _("إهلاك %(asset)s - %(month)s", asset=asset.name, month=line.date.strftime("%Y/%m")),
                "line_ids": [
                    Command.create({"name": _("إهلاك %s", asset.name), "account_id": asset.expense_account_id.id,
                                    "debit": line.amount, "credit": 0.0, "partner_id": False,
                                    "analytic_distribution": asset.analytic_distribution}),
                    Command.create({"name": _("إهلاك %s", asset.name), "account_id": asset.depreciation_account_id.id,
                                    "debit": 0.0, "credit": line.amount}),
                ],
            })
            move.action_post()
            line.move_id = move
            moves |= move
        return moves


class AccountMove(models.Model):
    _inherit = "account.move"

    resta_asset_ids = fields.One2many("resta.asset", compute="_compute_resta_asset_ids", string="الأصول")

    def _compute_resta_asset_ids(self):
        for move in self:
            move.resta_asset_ids = self.env["resta.asset"].search([("bill_line_id.move_id", "=", move.id)]) \
                if move.id else False

    def _post(self, soft=True):
        posted = super()._post(soft)
        posted.filtered(lambda m: m.move_type == "in_invoice")._resta_create_assets()
        return posted

    def _resta_create_assets(self):
        Type = self.env["resta.asset.type"].sudo()
        Asset = self.env["resta.asset"].sudo()
        for move in self:
            types = Type.search([("company_id", "=", move.company_id.id), ("auto_from_bills", "=", True)])
            by_account = {t.asset_account_id.id: t for t in types}
            for line in move.invoice_line_ids.filtered(lambda l: l.account_id.id in by_account and l.balance > 0):
                if Asset.search_count([("bill_line_id", "=", line.id)]):
                    continue
                t = by_account[line.account_id.id]
                Asset.create({
                    "name": line.name or line.product_id.display_name or move.name, "type_id": t.id,
                    "company_id": move.company_id.id, "acquisition_date": move.invoice_date or move.date,
                    "original_value": line.balance, "partner_id": move.partner_id.id, "bill_line_id": line.id,
                    "analytic_distribution": line.analytic_distribution,
                })

    def action_open_resta_assets(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "res_model": "resta.asset", "name": _("الأصول"),
                "view_mode": "list,form", "domain": [("id", "in", self.resta_asset_ids.ids)]}
