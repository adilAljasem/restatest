/** @odoo-module */
import { patch } from "@web/core/utils/patch";
import { ProductScreen } from "@point_of_sale/app/screens/product_screen/product_screen";

patch(ProductScreen.prototype, {
    getNumpadButtons() {
        const buttons = super.getNumpadButtons();
        const decimalPoint = this.env.services.localization.decimalPoint;
        return buttons.map((button) =>
            button.value === decimalPoint && this.pos.numpadMode === "quantity"
                ? { ...button, disabled: true }
                : button
        );
    },

    onNumpadClick(buttonValue) {
        const decimalPoint = this.env.services.localization.decimalPoint;
        if (
            this.pos.numpadMode === "quantity" &&
            (buttonValue === decimalPoint || buttonValue === "." || buttonValue === ",")
        ) {
            return;
        }
        return super.onNumpadClick(buttonValue);
    },
});
