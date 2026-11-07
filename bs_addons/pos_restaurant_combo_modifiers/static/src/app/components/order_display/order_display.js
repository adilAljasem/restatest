/** @odoo-module **/

import { OrderDisplay } from "@point_of_sale/app/components/order_display/order_display";
import { arrangeModifierLines } from "@pos_restaurant_combo_modifiers/app/utils/modifier_display";
import { patch } from "@web/core/utils/patch";

patch(OrderDisplay.prototype, {
    get comboSortedLines() {
        return arrangeModifierLines(super.comboSortedLines, this.props.mode);
    },
});
