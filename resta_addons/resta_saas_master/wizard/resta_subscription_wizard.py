from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models


class RestaSubscriptionWizard(models.TransientModel):
    _name = "resta.subscription.wizard"
    _description = "Activate / renew a Resta subscription"

    tenant_id = fields.Many2one("resta.tenant", required=True)
    plan_id = fields.Many2one("resta.plan", "الباقة", required=True)
    billing = fields.Selection([("monthly", "شهري"), ("yearly", "سنوي")], default="yearly", required=True)
    periods = fields.Integer("عدد الفترات", default=1)
    branches = fields.Integer("عدد الفروع", default=1)
    date_start = fields.Date("بداية الاشتراك", required=True)
    date_end = fields.Date("نهاية الاشتراك", compute="_compute_amount")
    amount = fields.Monetary("المبلغ قبل الضريبة", compute="_compute_amount", readonly=False, store=True)
    currency_id = fields.Many2one(related="plan_id.currency_id")
    create_invoice = fields.Boolean("إنشاء فاتورة", default=True)

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        tenant = self.env["resta.tenant"].browse(self.env.context.get("active_id"))
        if tenant:
            start = fields.Date.context_today(self)
            if tenant.subscription_end and tenant.subscription_end >= start:
                start = tenant.subscription_end + relativedelta(days=1)
            res.update({"tenant_id": tenant.id, "plan_id": tenant.plan_id.id or False,
                        "branches": max(tenant.branches_planned, 1), "date_start": start})
        return res

    @api.depends("plan_id", "billing", "periods", "branches", "date_start")
    def _compute_amount(self):
        for w in self:
            months = 12 if w.billing == "yearly" else 1
            n = max(w.periods, 1)
            w.date_end = w.date_start + relativedelta(months=months * n, days=-1) if w.date_start else False
            p = w.plan_id
            if not p:
                w.amount = 0.0
                continue
            base = (p.price_yearly if w.billing == "yearly" else p.price_monthly) * n
            extra = max(w.branches - p.max_branches, 0) * p.extra_branch_price * months * n
            w.amount = base + extra

    def action_confirm(self):
        self.ensure_one()
        t = self.tenant_id
        sub = self.env["resta.subscription"].create({
            "tenant_id": t.id, "plan_id": self.plan_id.id, "billing": self.billing, "branches": self.branches,
            "date_start": self.date_start, "date_end": self.date_end, "amount": self.amount})
        if self.create_invoice:
            product = self.plan_id._billing_product()
            move = self.env["account.move"].create({
                "move_type": "out_invoice", "partner_id": t.partner_id.id,
                "invoice_line_ids": [(0, 0, {
                    "product_id": product.id, "quantity": 1, "price_unit": self.amount,
                    "name": _("اشتراك ريستا %(plan)s (%(billing)s) - %(name)s - من %(start)s إلى %(end)s",
                              plan=self.plan_id.name, billing=dict(self._fields["billing"].selection)[self.billing],
                              name=t.name, start=self.date_start, end=self.date_end)})],
            })
            sub.invoice_id = move
        t.write({"plan_id": self.plan_id.id, "state": "active"})
        t.message_post(body=_("تفعيل اشتراك %(plan)s حتى %(end)s", plan=self.plan_id.name, end=self.date_end))
        if sub.invoice_id:
            return {"type": "ir.actions.act_window", "res_model": "account.move", "res_id": sub.invoice_id.id,
                    "view_mode": "form"}
        return {"type": "ir.actions.act_window_close"}
