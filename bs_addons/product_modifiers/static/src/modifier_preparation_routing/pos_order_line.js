/** @odoo-module **/

import { PosOrderline } from "@point_of_sale/app/models/pos_order_line";
import { patch } from "@web/core/utils/patch";

patch(PosOrderline.prototype, {
    setup() {
        super.setup(...arguments);
        this.modifier_parent_uuid = this.modifier_parent_uuid || "";
    },

    setOptions(options) {
        const result = super.setOptions(...arguments);
        const parentReference = options.extras?.parent_line_id;
        if (!parentReference) {
            return result;
        }

        const parentLine = this.order_id?.lines?.find(
            (line) =>
                String(line.id) === String(parentReference) ||
                line.uuid === parentReference
        );
        if (parentLine) {
            this.modifier_parent_uuid = parentLine.uuid;
        }
        return result;
    },
});
