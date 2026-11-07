/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { FormController } from "@web/views/form/form_controller";

const BACKDATED_INVENTORY_BUTTON = "action_count_with_back_date";

function isBackdatedInventoryButton(controller, clickParams) {
    return (
        controller.model.root.resModel === "stock.inventory" &&
        clickParams.name === BACKDATED_INVENTORY_BUTTON
    );
}

patch(FormController.prototype, {
    async beforeExecuteActionButton(clickParams) {
        if (!isBackdatedInventoryButton(this, clickParams)) {
            return super.beforeExecuteActionButton(...arguments);
        }

        const record = this.model.root;
        const saved = await record.save({
            reload: false,
            onError: (error, options) => this.onSaveError(error, options, true),
        });
        if (saved !== false && this.props.onSave) {
            this.props.onSave(record, clickParams);
        }
        return saved;
    },

    async afterExecuteActionButton(clickParams) {
        if (!isBackdatedInventoryButton(this, clickParams)) {
            return super.afterExecuteActionButton(...arguments);
        }

        const lineList = this.model.root.data.line_ids;
        if (lineList) {
            await lineList.load();
        }
    },
});
