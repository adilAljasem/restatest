/** POS: the categories handled by Resta kitchen screens count as «preparation» categories,
 *  so the «Send» button appears even without a kitchen printer. */
import { PosConfig } from "@point_of_sale/app/models/pos_config";
import { patch } from "@web/core/utils/patch";

patch(PosConfig.prototype, {
    get restaKdsCategories() {
        const displays = this.models["resta.kds.display"]?.getAll() || [];
        const set = new Set();
        for (const display of displays) {
            const ids = display.raw?.category_ids || [];
            if (!ids.length) {
                // a screen without categories receives everything
                for (const cat of this.models["pos.category"].getAll()) {
                    set.add(cat.id);
                }
            } else {
                ids.forEach((id) => set.add(id));
            }
        }
        return set;
    },
    get preparationCategories() {
        const base = super.preparationCategories;
        const kds = this.restaKdsCategories;
        return kds.size ? new Set([...base, ...kds]) : base;
    },
});
