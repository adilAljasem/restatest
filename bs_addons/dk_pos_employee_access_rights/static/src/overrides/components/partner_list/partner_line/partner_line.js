/** @odoo-module */

import { PartnerLine } from "@point_of_sale/app/screens/partner_list/partner_line/partner_line";
import { patch } from "@web/core/utils/patch";
import { usePos } from "@point_of_sale/app/hooks/pos_hook";

patch(PartnerLine.prototype, {
    setup() {
        super.setup();
        this.pos = usePos();
    },
});
