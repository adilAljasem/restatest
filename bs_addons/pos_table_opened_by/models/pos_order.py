from odoo import api, fields, models


class PosOrder(models.Model):
    _inherit = "pos.order"

    opened_by_name = fields.Char(
        string="Opened By",
        readonly=True,
        copy=False,
        help="اسم الكاشير الذي فتح الطلب أول مرة. لا يتغيّر إذا عدّل الطلب كاشير آخر.",
    )

    def _tob_cashier_name(self):
        self.ensure_one()
        if "employee_id" in self._fields and self.employee_id:
            return self.employee_id.name
        return self.user_id.name or self.env.user.name

    @api.model_create_multi
    def create(self, vals_list):
        orders = super().create(vals_list)
        for order in orders.filtered(lambda o: not o.opened_by_name):
            order.sudo().with_context(tob_set=True).opened_by_name = order._tob_cashier_name()
        return orders

    def write(self, vals):
        # الاسم يُحدَّد مرة واحدة عند الإنشاء، ولا تستبدله الصناديق الأخرى عند المزامنة
        if "opened_by_name" in vals and not self.env.context.get("tob_set"):
            vals = dict(vals)
            vals.pop("opened_by_name")
        return super().write(vals)
