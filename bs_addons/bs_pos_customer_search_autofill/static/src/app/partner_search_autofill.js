/** @odoo-module */

import { PartnerList } from "@point_of_sale/app/screens/partner_list/partner_list";
import { PosStore } from "@point_of_sale/app/services/pos_store";
import { patch } from "@web/core/utils/patch";

const partnerCreationQueries = new WeakMap();
const PHONE_SEPARATOR_REGEX = /[\s()+\-./]/g;
const DECIMAL_DIGITS_REGEX = /^[0-9\u0660-\u0669\u06f0-\u06f9]+$/;

/**
 * Return the partner form defaults for a POS customer search term.
 *
 * A value made exclusively of decimal digits and common phone separators is
 * treated as a phone number. The original, trimmed value is preserved because
 * it may contain a country prefix or user-friendly formatting.
 *
 * @param {unknown} query
 * @returns {Record<string, string>}
 */
export function getPartnerCreationDefaults(query) {
    const value = String(query ?? "").trim();
    if (!value) {
        return {};
    }

    const defaults = {
        default_name: value,
    };
    const compactValue = value.replace(PHONE_SEPARATOR_REGEX, "");
    if (compactValue && DECIMAL_DIGITS_REGEX.test(compactValue)) {
        defaults.default_phone = value;
    }
    return defaults;
}

patch(PartnerList.prototype, {
    async editPartner(partner = false) {
        if (partner) {
            return await super.editPartner(partner);
        }

        partnerCreationQueries.set(this.pos, this.state.query);
        try {
            return await super.editPartner(partner);
        } finally {
            partnerCreationQueries.delete(this.pos);
        }
    },
});

patch(PosStore.prototype, {
    editPartnerContext() {
        return {
            ...(super.editPartnerContext(...arguments) || {}),
            ...getPartnerCreationDefaults(partnerCreationQueries.get(this)),
        };
    },
});
