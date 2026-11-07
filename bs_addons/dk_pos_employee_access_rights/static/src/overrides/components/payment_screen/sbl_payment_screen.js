/** @odoo-module */

import { PaymentScreen } from "@point_of_sale/app/screens/payment_screen/payment_screen";
import { patch } from "@web/core/utils/patch";

function recordId(recordOrId) {
    return recordOrId?.id ?? recordOrId;
}

patch(PaymentScreen.prototype, {
    setup() {
        super.setup();
        this._filterDisabledPaymentMethods();
    },
    _filterDisabledPaymentMethods() {
        const cashier = this.pos.getCashier();
        const disabledMethods = cashier?.sbl_disabled_payment_method_ids || [];
        if (disabledMethods.length > 0) {
            const disabledMethodIds = disabledMethods.map(recordId);
            this.payment_methods_from_config = this.payment_methods_from_config
                .filter(pm => !disabledMethodIds.includes(pm.id))
                .slice()
                .sort((a, b) => a.sequence - b.sequence);
        }
    },
    getNumpadButtons() {
        const buttons = super.getNumpadButtons();
        const employee = this.pos.getCashier();
        for (const button of buttons) {
            if (button.value === "quantity") {
                button.disabled = employee.sbl_disable_pos_qty;
            }
            if (button.value === "price") {
                button.disabled = !this.pos.cashierHasPriceControlRights() || employee.sbl_disable_pos_change_price;
            }
            if (button.value === "discount") {
                button.disabled = !this.pos.config.manual_discount || employee.sbl_disable_pos_discount_button;
            }
            if (button.value === "-") {
                button.disabled = employee.sbl_disable_pos_numpad_plus_minus;
            }
        }
        const clickButton = buttons.find(button => button.value === this.pos.numpadMode);
        if (clickButton) {
            for (const button of buttons) {
                if (!["quantity", "discount", "price", "-"].includes(button.value)) {
                    button.disabled = clickButton.disabled;
                }
            }
        }
        return buttons;
    },
});
