/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { PosStore } from "@point_of_sale/app/services/pos_store";

patch(PosStore.prototype, {
    async _processData(loadedData) {
        await super._processData(...arguments);
        // This is kept for backward compatibility if needed, 
        // but the data is already available via this.config.analytic_account_id 
        // because we added it to _load_pos_data_fields in pos_config.py
        this.analytic_account_id = this.config.analytic_account_id;
    }
});