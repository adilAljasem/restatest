from odoo import _, api, fields, models
from odoo.exceptions import UserError


class RestaAssetDispose(models.TransientModel):
    _name = "resta.asset.dispose"
    _description = "استبعاد أصل"

    asset_id = fields.Many2one("resta.asset", required=True)
    company_id = fields.Many2one(related="asset_id.company_id")
    currency_id = fields.Many2one(related="asset_id.currency_id")
    date = fields.Date("تاريخ الاستبعاد", required=True, default=fields.Date.context_today)
    mode = fields.Selection([("scrap", "إتلاف / تخريد"), ("sale", "بيع")], default="scrap", required=True)
    sale_amount = fields.Monetary("مبلغ البيع")
    sale_account_id = fields.Many2one("account.account", "حساب المبلغ المستلم", check_company=True,
                                      domain="[('account_type', 'in', ('asset_cash', 'asset_receivable', 'asset_current'))]")
    loss_account_id = fields.Many2one("account.account", "حساب ربح/خسارة الاستبعاد", required=True,
                                      check_company=True)

    def action_dispose(self):
        self.ensure_one()
        if self.mode == "sale" and (not self.sale_amount or not self.sale_account_id):
            raise UserError(_("أدخل مبلغ البيع والحساب."))
        move = self.asset_id._dispose(self.date, self.loss_account_id,
                                      self.sale_amount if self.mode == "sale" else 0.0,
                                      self.sale_account_id if self.mode == "sale" else None)
        return {"type": "ir.actions.act_window", "res_model": "account.move", "res_id": move.id, "view_mode": "form"}


class RestaLockDates(models.TransientModel):
    _name = "resta.lock.dates"
    _description = "إقفال الفترات المحاسبية"

    company_id = fields.Many2one("res.company", required=True, default=lambda s: s.env.company)
    sale_lock_date = fields.Date("إقفال المبيعات")
    purchase_lock_date = fields.Date("إقفال المشتريات")
    tax_lock_date = fields.Date("إقفال الضريبة (بعد تقديم الإقرار)")
    fiscalyear_lock_date = fields.Date("إقفال كل القيود")
    hard_lock_date = fields.Date("إقفال نهائي (لا يمكن التراجع)")

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        company = self.env.company
        for f in ("sale_lock_date", "purchase_lock_date", "tax_lock_date", "fiscalyear_lock_date", "hard_lock_date"):
            res[f] = company[f]
        return res

    def action_apply(self):
        self.ensure_one()
        if not self.env.user.has_group("account.group_account_manager"):
            raise UserError(_("الإقفال لمدير المحاسبة فقط."))
        vals = {f: self[f] for f in ("sale_lock_date", "purchase_lock_date", "tax_lock_date", "fiscalyear_lock_date",
                                     "hard_lock_date") if self[f] != self.company_id[f]}
        if vals:
            self.company_id.sudo().write(vals)
        return {"type": "ir.actions.client", "tag": "display_notification",
                "params": {"type": "success", "message": _("تم حفظ تواريخ الإقفال."), "next": {
                    "type": "ir.actions.act_window_close"}}}
