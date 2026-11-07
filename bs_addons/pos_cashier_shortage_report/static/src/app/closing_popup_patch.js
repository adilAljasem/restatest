import { ClosePosPopup } from "@point_of_sale/app/components/popups/closing_popup/closing_popup";
import { parseFloat } from "@web/views/fields/parsers";
import { patch } from "@web/core/utils/patch";


patch(ClosePosPopup.prototype, {
    getInitialState() {
        const state = super.getInitialState(...arguments);
        for (const paymentMethod of this.props.non_cash_payment_methods) {
            if (paymentMethod.type === "cash") {
                state.payments[paymentMethod.id] = { counted: "0" };
            }
        }
        return state;
    },

    async closeSession() {
        const cashCountPairs = [];
        const defaultCash = this.props.default_cash_details;
        if (defaultCash?.id && this.state.payments[defaultCash.id]) {
            cashCountPairs.push([
                defaultCash.id,
                parseFloat(this.state.payments[defaultCash.id].counted),
            ]);
        }
        for (const paymentMethod of this.props.non_cash_payment_methods) {
            if (paymentMethod.type === "cash" && this.state.payments[paymentMethod.id]) {
                cashCountPairs.push([
                    paymentMethod.id,
                    parseFloat(this.state.payments[paymentMethod.id].counted),
                ]);
            }
        }
        await this.pos.data.call(
            "pos.session",
            "store_cashier_cash_counts",
            [this.pos.session.id, cashCountPairs]
        );
        return super.closeSession(...arguments);
    },
});
