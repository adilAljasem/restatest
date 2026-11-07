/** @odoo-module */
/*
 * BS POS Refund Guard — فحص في الواجهة قبل الدفع
 * يمنع الكاشير من إكمال الاسترجاع (وتسليم الكاش) إذا كان المبلغ المسترد
 * أعلى من الصافي بعد الخصم. السيرفر يعيد الفحص نفسه تراكمياً.
 */
import { patch } from "@web/core/utils/patch";
import { PaymentScreen } from "@point_of_sale/app/screens/payment_screen/payment_screen";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { _t } from "@web/core/l10n/translation";

// قراءة سعر السطر شامل الضريبة بطريقة تتحمل اختلاف أسماء الدوال بين الإصدارات
function lineTotalIncl(line) {
    if (typeof line.priceIncl === "number") return line.priceIncl;
    if (typeof line.get_price_with_tax === "function") return line.get_price_with_tax();
    if (typeof line.getPriceWithTax === "function") return line.getPriceWithTax();
    return line.price_subtotal_incl || 0;
}

function lineQty(line) {
    return typeof line.getQuantity === "function" ? line.getQuantity() : line.qty || 0;
}

function computeRefundExcess(order) {
    const refundLines = order.lines.filter((l) => l.refunded_orderline_id);
    if (!refundLines.length) return null;

    // تجميع سطور الاسترجاع حسب الطلب الأصلي
    const byOriginal = new Map();
    for (const rl of refundLines) {
        const original = rl.refunded_orderline_id.order_id;
        if (!original) continue;
        if (!byOriginal.has(original)) byOriginal.set(original, []);
        byOriginal.get(original).push(rl);
    }

    for (const [original, rls] of byOriginal) {
        const origLines = original.lines || [];
        const positive = origLines.filter((l) => (l.price_subtotal_incl || 0) > 0);
        const negativeTotal = origLines
            .filter((l) => (l.price_subtotal_incl || 0) < 0)
            .reduce((s, l) => s + l.price_subtotal_incl, 0);
        const positiveTotal = positive.reduce((s, l) => s + l.price_subtotal_incl, 0);
        if (!positiveTotal || !negativeTotal) continue; // لا يوجد خصم عام → لا مشكلة

        const factor = (positiveTotal + negativeTotal) / positiveTotal;
        let refunded = 0;
        let allowed = 0;
        for (const rl of rls) {
            refunded += -lineTotalIncl(rl);
            const ol = rl.refunded_orderline_id;
            if ((ol.price_subtotal_incl || 0) > 0 && ol.qty) {
                allowed += ol.price_subtotal_incl * factor * (-lineQty(rl) / ol.qty);
            }
        }
        if (refunded - allowed > 0.01 * rls.length) {
            return { refunded, allowed, ref: original.pos_reference || original.name };
        }
    }
    return null;
}

patch(PaymentScreen.prototype, {
    async validateOrder(isForceValidate) {
        const excess = computeRefundExcess(this.currentOrder);
        if (excess) {
            this.env.services.dialog.add(AlertDialog, {
                title: _t("استرجاع غير مسموح"),
                body: _t(
                    "الطلب الأصلي %(ref)s عليه خصم.\nالمبلغ المسترد %(refunded)s أعلى من الصافي المسموح %(allowed)s.\n\nارجع لشاشة الاسترجاع واختر سطر الخصم مع الصنف.",
                    {
                        ref: excess.ref,
                        refunded: excess.refunded.toFixed(2),
                        allowed: excess.allowed.toFixed(2),
                    }
                ),
            });
            return;
        }
        return super.validateOrder(...arguments);
    },
});
