import { patch } from "@web/core/utils/patch";
import { TicketScreen } from "@point_of_sale/app/screens/ticket_screen/ticket_screen";

patch(TicketScreen.prototype, {
    async onDoRefund() {
        const source = this.getSelectedOrder();
        const hasBlendDiscount = source?.blend_is_inbound &&
            source.lines.some((line) => line.blend_is_discount_line);
        await super.onDoRefund(...arguments);
        const refund = this.pos.getOrder();
        if (!hasBlendDiscount || !refund || refund === source) {
            return;
        }
        const quantities = {};
        for (const line of refund.lines) {
            const original = line.refunded_orderline_id;
            if (original?.order_id === source && !original.blend_is_discount_line) {
                quantities[original.id] = (quantities[original.id] || 0) + Math.abs(line.qty);
            }
        }
        if (!Object.keys(quantities).length) {
            return;
        }
        // Calculate a monetary share on the server, never a rounded fractional
        // quantity of the original coupon. The payment path rechecks the share.
        const values = await this.pos.data.call("pos.order", "blend_refund_discount_data", [[source.id], quantities]);
        for (const line of [...refund.lines]) {
            if (line.blend_is_discount_line && (line.refunded_orderline_id?.order_id === source ||
                line.blend_refund_discount_order_id === source)) {
                line.delete();
            }
        }
        for (const value of values) {
            const product = this.pos.models["product.product"].get(value.product_id);
            await this.pos.addLineToOrder({
                product_tmpl_id: product.product_tmpl_id,
                product_id: product,
                qty: value.qty,
                price_unit: value.price_unit,
                tax_ids: value.tax_ids.map((id) => ["link", this.pos.models["account.tax"].get(id)]),
                blend_refund_discount_order_id: source,
                blend_is_discount_line: true,
                full_product_name: value.full_product_name,
            }, refund, { force: true }, false);
        }
    },
});
