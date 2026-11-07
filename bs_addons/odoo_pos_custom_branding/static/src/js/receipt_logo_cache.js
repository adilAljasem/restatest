/** @odoo-module */

// The receipt is turned into an image before printing. A logo given as a URL
// must be downloaded during that conversion and is sometimes missing on the
// printed receipt (blank space). The company logo is downloaded once when the
// POS starts and kept as a data URL, which is embedded instantly when printing.

import { PosStore } from "@point_of_sale/app/services/pos_store";
import { patch } from "@web/core/utils/patch";

const logos = new Map(); // company id -> data URL

export function companyLogoUrl(companyId) {
    // 576px wide is the full print width: lighter and faster to draw than the original.
    return `/web/image?model=res.company&id=${companyId}&field=logo&width=576`;
}

export function cachedCompanyLogo(companyId) {
    return logos.get(companyId) || false;
}

export async function cacheCompanyLogo(companyId) {
    if (!companyId || logos.has(companyId)) {
        return logos.get(companyId);
    }
    const response = await fetch(companyLogoUrl(companyId));
    if (!response.ok) {
        return false;
    }
    const blob = await response.blob();
    const dataUrl = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = reject;
        reader.readAsDataURL(blob);
    });
    logos.set(companyId, dataUrl);
    return dataUrl;
}

patch(PosStore.prototype, {
    async afterProcessServerData() {
        const result = await super.afterProcessServerData(...arguments);
        // Do not delay the POS start; retry once on the next print if it failed.
        cacheCompanyLogo(this.company?.id).catch((error) =>
            console.warn("Receipt logo could not be cached", error)
        );
        return result;
    },
});
