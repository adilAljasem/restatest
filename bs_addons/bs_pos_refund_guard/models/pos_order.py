# -*- coding: utf-8 -*-
"""
BS POS Refund Guard
-------------------
الثغرة: الخصم العام (Global Discount) وخصومات برامج الولاء تُسجَّل في Odoo POS
كسطور مستقلة بقيمة سالبة. عند الاسترجاع يستطيع الكاشير اختيار الصنف فقط
دون سطر الخصم، فيُسترد سعر الصنف كاملاً (27.00 بدل 22.95).

الحل (على السيرفر — وهو خط الدفاع الحقيقي):
لكل طلب أصلي، نوزّع السطور السالبة (خصومات) نسبياً على السطور الموجبة،
فيصبح لكل سطر "قيمة قابلة للاسترجاع" = سعره × (صافي الطلب ÷ إجمالي السطور الموجبة).
ثم نتحقق أن مجموع ما استُرد من هذا الطلب (في كل الاسترجاعات، بما فيها الحالي)
لا يتجاوز القيمة القابلة للاسترجاع للكميات المسترجعة، ولا يتجاوز الصافي المدفوع.
"""
from collections import defaultdict

from odoo import _, api, models
from odoo.exceptions import UserError


class PosOrder(models.Model):
    _inherit = "pos.order"

    # ------------------------------------------------------------------
    # Hook: كل طلب يصل من الكاشير يمر من هنا
    # ------------------------------------------------------------------
    @api.model
    def _process_order(self, order, *args, **kwargs):
        order_id = super()._process_order(order, *args, **kwargs)
        pos_order = self.browse(order_id) if isinstance(order_id, int) else order_id
        if pos_order and pos_order.state not in ("draft", "cancel"):
            pos_order._bs_check_refund_limit()
        return order_id

    # ------------------------------------------------------------------
    # الفحص
    # ------------------------------------------------------------------
    def _bs_check_refund_limit(self):
        for refund in self:
            refund_lines = refund.lines.filtered("refunded_orderline_id")
            if not refund_lines:
                continue
            originals = refund_lines.mapped("refunded_orderline_id.order_id")
            for original in originals:
                refund._bs_check_against_original(original)

    def _bs_check_against_original(self, original):
        self.ensure_one()
        currency = original.currency_id or self.currency_id
        rounding = currency.rounding or 0.01

        positive_lines = original.lines.filtered(lambda l: l.price_subtotal_incl > 0)
        negative_lines = original.lines.filtered(lambda l: l.price_subtotal_incl < 0)
        positive_total = sum(positive_lines.mapped("price_subtotal_incl"))
        if not positive_total:
            return
        negative_total = sum(negative_lines.mapped("price_subtotal_incl"))
        # نسبة الصافي بعد الخصومات العامة (مثلاً 0.85 لخصم 15%)
        factor = (positive_total + negative_total) / positive_total

        # كل سطور الاسترجاع المرتبطة بهذا الطلب الأصلي (الحالي + السابقة المعتمدة)
        all_refund_lines = self.env["pos.order.line"].search([
            ("refunded_orderline_id", "in", original.lines.ids),
            "|",
            ("order_id", "=", self.id),
            ("order_id.state", "not in", ("draft", "cancel")),
        ])

        refunded_qty = defaultdict(float)
        refunded_value = 0.0
        for rl in all_refund_lines:
            refunded_qty[rl.refunded_orderline_id.id] += -rl.qty
            # سطر الصنف المسترجع سالب، وسطر الخصم المسترجع موجب → الصافي يُحسب تلقائياً
            refunded_value += -rl.price_subtotal_incl

        allowed = 0.0
        for line in positive_lines:
            qty = refunded_qty.get(line.id, 0.0)
            if qty and line.qty:
                allowed += line.price_subtotal_incl * factor * (qty / line.qty)

        # سقف مطلق: لا يُسترد أكثر من الصافي المدفوع فعلياً
        net_paid = original.amount_paid or original.amount_total
        allowed = min(allowed, net_paid)

        tolerance = rounding * max(len(all_refund_lines), 1)
        if currency.compare_amounts(refunded_value, allowed + tolerance) > 0:
            raise UserError(_(
                "لا يمكن إتمام الاسترجاع.\n\n"
                "الطلب الأصلي %(ref)s عليه خصم، والمبلغ المسترد (%(refunded).2f) "
                "أعلى من الصافي المسموح باسترداده (%(allowed).2f).\n\n"
                "الحل: ارجع لشاشة الاسترجاع واختر سطر الخصم مع الصنف.",
                ref=original.pos_reference or original.name,
                refunded=refunded_value,
                allowed=allowed,
            ))
