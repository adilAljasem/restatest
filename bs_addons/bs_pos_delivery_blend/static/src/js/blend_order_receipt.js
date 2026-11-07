import { OrderReceipt } from "@point_of_sale/app/screens/receipt_screen/receipt/order_receipt";
import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";

patch(OrderReceipt, {
    template: "bs_pos_delivery_blend.OrderReceipt",
});

patch(OrderReceipt.prototype, {
    get blendReceiptData() {
        const text = (value) => (value ? String(value).trim() : "");
        const reference = text(this.order.blend_partner_reference || this.order.blend_order_id);
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
            {
                key: "car_plate",
                label: _t("Car Plate"),
                value: [text(this.order.blend_car_plate_letters), text(this.order.blend_car_plate_number)].filter(Boolean).join(" "),
            },
            {
                key: "car_details",
                label: _t("Car Details"),
                value: [text(this.order.blend_car_brand), text(this.order.blend_car_color)].filter(Boolean).join(" - "),
            },
        ].filter((row) => row.value);

        return { title: _t("Blend Order"), rows };
    },
});

