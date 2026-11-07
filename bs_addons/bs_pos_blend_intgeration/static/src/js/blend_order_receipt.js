import { OrderReceipt } from "@point_of_sale/app/screens/receipt_screen/receipt/order_receipt";
import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";

// Keep the original template, including installed receipt designs, inside one
// printable root. Ordinary orders still render the original template directly.
patch(OrderReceipt, {
    template: "bs_pos_blend_intgeration.OrderReceipt",
});

patch(OrderReceipt.prototype, {
    get blendReceiptData() {
        const text = (value) => (value ? String(value).trim() : "");
        const reference = text(this.order.blend_partner_reference);
        if (!reference) {
            return null;
        }

        const platformOrderId = text(this.order.blend_aggregator_order_id);
        const orderType = text(this.order.blend_order_type);
        const orderTypes = new Map([
            ["delivery", _t("Delivery")],
            ["pickup", _t("Pickup")],
            ["dinein", _t("Dine-in")],
        ]);
        const rows = [
            { key: "platform", label: _t("Platform"), value: text(this.order.blend_aggregator) },
            { key: "order_id", label: _t("Platform Order ID"), value: platformOrderId },
            {
                key: "reference",
                label: _t("Blend Reference"),
                value: reference === platformOrderId ? "" : reference,
            },
            {
                key: "type",
                label: _t("Order Type"),
                value: orderTypes.get(orderType.toLowerCase().replace(/[\s_-]/g, "")) || orderType,
            },
        ].filter((row) => row.value);

        return { title: _t("Blend Order"), rows };
    },
});
