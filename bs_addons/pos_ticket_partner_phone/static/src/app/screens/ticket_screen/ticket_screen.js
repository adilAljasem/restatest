import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { TicketScreen } from "@point_of_sale/app/screens/ticket_screen/ticket_screen";

patch(TicketScreen.prototype, {
    _getSearchFields() {
        const fields = super._getSearchFields(...arguments);
        fields.PARTNER_PHONE = {
            repr: (order) => order.partner_id?.phone || "",
            displayName: _t("Customer Phone"),
            modelFields: ["partner_id.phone"],
        };
        return fields;
    },
});
