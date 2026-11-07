/** @odoo-module **/

import { onPatched } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { Many2XAutocomplete } from "@web/views/fields/relational_utils";
import { X2ManyField, x2ManyField } from "@web/views/fields/x2many/x2many_field";
import { ListRenderer } from "@web/views/list/list_renderer";

class ModifierGroupOptionsRenderer extends ListRenderer {
    static rowsTemplate = "pos_restaurant_combo_modifiers.ModifierGroupOptionsRows";
    static recordRowTemplate = "pos_restaurant_combo_modifiers.ModifierGroupOptionsRecordRow";
    static components = { ...ListRenderer.components, Many2XAutocomplete };
    static props = [...ListRenderer.props, "parentRecord"];

    setup() {
        super.setup();
        this.state.expandedOptions = {};
        this.state.optionsByGroup = {};
        this.state.optionsLoading = {};
        this.state.optionsError = {};
        this.draftBuffer = null;
        this.pendingDraftWrites = 0;
        this.lastDraft = JSON.stringify(this.recordDrafts);
        onPatched(() => {
            if (this.pendingDraftWrites) {
                return;
            }
            const current = JSON.stringify(this.recordDrafts);
            if (current !== this.lastDraft) {
                this.lastDraft = current;
                for (const record of this.props.list.records) {
                    if (this.state.expandedOptions[record.id]) {
                        this.loadOptions(record);
                    }
                }
            }
        });
    }

    get drafts() {
        return this.draftBuffer || this.recordDrafts;
    }

    get recordDrafts() {
        return this.props.parentRecord.data.modifier_option_editor_changes || [];
    }

    groupId(record) {
        const group = record.data.modifier_group_id;
        return group?.id || group?.[0] || false;
    }

    async loadOptions(record) {
        const key = record.id;
        const groupId = this.groupId(record);
        if (!groupId) {
            this.state.optionsByGroup[key] = [];
            return;
        }
        this.state.optionsLoading[key] = true;
        this.state.optionsError[key] = "";
        try {
            const rows = await this.orm.call(
                "pos.modifier.group", "get_options_for_product_editor",
                [[groupId], this.props.parentRecord.resId || false]
            );
            this.state.optionsByGroup[key] = rows.map((option) => {
                const draft = this.drafts.find((item) =>
                    item.group_id === groupId && item.option_id === option.id
                );
                return {
                    ...option,
                    ...(!draft?.reset ? draft?.values || {} : option.shared),
                };
            });
        } catch (error) {
            this.state.optionsError[key] = error.message || _t("Unable to load modifier options.");
        } finally {
            this.state.optionsLoading[key] = false;
        }
    }

    async toggleOptions(record) {
        const key = record.id;
        this.state.expandedOptions[key] = !this.state.expandedOptions[key];
        if (this.state.expandedOptions[key]) {
            await this.loadOptions(record);
        }
    }

    setDrafts(drafts) {
        this.draftBuffer = drafts;
        this.pendingDraftWrites++;
        return this.props.parentRecord.update({
            modifier_option_editor_changes: drafts,
        }).finally(() => {
            this.pendingDraftWrites--;
            if (!this.pendingDraftWrites) {
                this.lastDraft = JSON.stringify(this.recordDrafts);
                this.draftBuffer = null;
            }
        });
    }

    async changeOption(record, option, fieldName, value) {
        option[fieldName] = value;
        const groupId = this.groupId(record);
        const drafts = [...this.drafts];
        const index = drafts.findIndex((item) =>
            item.group_id === groupId && item.option_id === option.id
        );
        const values = { ...(index >= 0 ? drafts[index].values : {}), [fieldName]: value };
        if (fieldName === "default_qty" && value > option.max_qty) {
            option.max_qty = Math.ceil(value);
            values.max_qty = option.max_qty;
        }
        const draft = { group_id: groupId, option_id: option.id, values };
        if (index >= 0) {
            drafts[index] = draft;
        } else {
            drafts.push(draft);
        }
        await this.setDrafts(drafts);
    }

    async updateProduct(record, option, products) {
        const product = products?.[0];
        option.product_name = product?.display_name || "";
        await this.changeOption(record, option, "product_id", product?.id || false);
    }

    parseNumber(value) {
        return Number(value);
    }
}

class ModifierGroupOptionsField extends X2ManyField {
    static components = {
        ...X2ManyField.components,
        ListRenderer: ModifierGroupOptionsRenderer,
    };

    get rendererProps() {
        return { ...super.rendererProps, parentRecord: this.props.record };
    }
}

registry.category("fields").add("modifier_group_options", {
    ...x2ManyField,
    component: ModifierGroupOptionsField,
});
