/** @odoo-module **/

import { ProductTemplate } from "@point_of_sale/app/models/product_template";
import { patch } from "@web/core/utils/patch";

patch(ProductTemplate.prototype, {
    get canBeDisplayed() {
        return super.canBeDisplayed && !this.hide_from_pos_screen;
    },
});
