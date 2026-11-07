/** @odoo-module **/

import { ComboConfiguratorPopup } from "@point_of_sale/app/components/popups/combo_configurator_popup/combo_configurator_popup";
import { parseModifierQuantity } from "@pos_restaurant_combo_modifiers/app/utils/modifier_quantity";
import { _t } from "@web/core/l10n/translation";

export class ComboModifierConfiguratorPopup extends ComboConfiguratorPopup {
    static template = "pos_restaurant_combo_modifiers.ComboModifierConfiguratorPopup";
    static props = {
        ...ComboConfiguratorPopup.props,
        order: Object,
    };

    setup() {
        super.setup();
        this._initializeComboDefaults();
        this.state.modifierQuantities = {};
        this.state.expandedGroups = {};
        this.state.expandedCombos = Object.fromEntries(
            this.sortedCombos.map((combo) => [combo.id, combo.qty_min > 0])
        );
        this.state.limitMessages = {};
        this._optionsByTarget = {};
        this._restoreComboQuantities();
        this._initializeModifierState();
    }

    hasMultipleChoices() {
        return true;
    }

    _initializeComboDefaults() {
        if (this.props.line) {
            return;
        }
        for (const combo of this.props.productTemplate.combo_ids) {
            for (const item of combo.combo_item_ids) {
                this.state.qty[combo.id][item.id] = item.default_qty || 0;
            }
        }
    }

    autoSelectSingleChoices() {
        for (const combo of this.props.productTemplate.combo_ids) {
            if (combo.combo_item_ids.length === 1) {
                const [item] = combo.combo_item_ids;
                this.state.qty[combo.id][item.id] = Math.min(
                    Math.max(this.state.qty[combo.id][item.id], 1),
                    this._maximumItemQuantity(item)
                );
            }
        }
    }

    isConfirmButtonEnabled() {
        return Object.keys(this.state.qty).every((comboId) => {
            const combo = this.pos.models["product.combo"].get(comboId);
            const quantity = this.totalQuantityForCombo(comboId);
            return quantity >= combo.qty_min && quantity <= combo.qty_max;
        });
    }

    _maximumItemQuantity(comboItem) {
        return comboItem.qty_max > 0
            ? Math.min(comboItem.qty_max, comboItem.combo_id.qty_max)
            : comboItem.combo_id.qty_max;
    }

    isItemPlusDisabled(comboItem) {
        return (
            this.totalQuantityForCombo(comboItem.combo_id.id) >= comboItem.combo_id.qty_max ||
            this.state.qty[comboItem.combo_id.id][comboItem.id] >=
                this._maximumItemQuantity(comboItem)
        );
    }

    get sortedCombos() {
        return [...this.props.productTemplate.combo_ids].sort((left, right) => {
            const requiredDifference = Number(right.qty_min > 0) - Number(left.qty_min > 0);
            return requiredDifference || left.sequence - right.sequence || left.id - right.id;
        });
    }

    isComboExpanded(combo) {
        return Boolean(this.state.expandedCombos[combo.id]);
    }

    toggleCombo(combo) {
        this.state.expandedCombos[combo.id] = !this.state.expandedCombos[combo.id];
    }

    _restoreComboQuantities() {
        if (!this.props.line) {
            return;
        }
        for (const combo of this.sortedCombos) {
            for (const item of combo.combo_item_ids) {
                this.state.qty[combo.id][item.id] = 0;
            }
        }
        const parentQuantity = Math.abs(this.props.line.qty) || 1;
        for (const line of this.props.line.getAllLinesInCombo()) {
            if (line.combo_item_id) {
                const comboId = line.combo_item_id.combo_id.id;
                const itemId = line.combo_item_id.id;
                this.state.qty[comboId][itemId] += Math.abs(line.qty) / parentQuantity;
            }
        }
    }

    targetKey(comboItem = null) {
        return comboItem ? `combo-${comboItem.id}` : "root";
    }

    modifierGroupLines(productTemplate) {
        if (
            !this.pos.config.enable_product_modifiers ||
            !productTemplate?.allow_modifiers
        ) {
            return [];
        }
        return (productTemplate.modifier_group_line_ids || [])
            .filter((line) => line.modifier_group_id)
            .sort((left, right) => {
                const requiredDifference =
                    Number(right.modifier_group_id.min_qty > 0) -
                    Number(left.modifier_group_id.min_qty > 0);
                return requiredDifference || left.sequence - right.sequence || left.id - right.id;
            });
    }

    sortedModifierOptions(group, targetKey) {
        return [...(this._optionsByTarget[targetKey]?.[group.id] || group.option_ids)].sort(
            (left, right) => left.sequence - right.sequence || left.id - right.id
        );
    }

    _effectiveModifierOptions(groupLine) {
        const settingsByOption = new Map(
            (groupLine.option_line_ids || []).map((line) => [line.option_id?.id, line])
        );
        return groupLine.modifier_group_id.option_ids.flatMap((option) => {
            const settings = settingsByOption.get(option.id);
            if (!settings) {
                return [option];
            }
            if (!settings.available) {
                return [];
            }
            return [{
                id: option.id,
                option_type: option.option_type,
                operation: option.operation,
                name: settings.name,
                description: settings.description,
                product_id: settings.product_id,
                price_mode: settings.price_mode,
                extra_price: settings.extra_price,
                default_qty: settings.default_qty,
                max_qty: settings.max_qty,
                kitchen_name: settings.kitchen_name,
                sequence: settings.sequence,
                originalOption: option,
            }];
        });
    }

    modifierDisplayType(group) {
        return this.pos.config.modifier_display_type || group.display_type || "grid";
    }

    get showImages() {
        return Boolean(this.pos.config.show_modifier_images);
    }

    productName(product) {
        const name = product?.name || product?.display_name || "";
        return String(name).replace(/^\[[^\]]+\]\s*/, "");
    }

    comboName(combo) {
        const name = String(combo?.name || "");
        const separator = name.lastIndexOf("/");
        return separator >= 0 ? name.slice(separator + 1).trim() : name;
    }

    comboSelectionSummary(combo) {
        return this.selectedComboItems(combo)
            .map((item) => this.productName(item.product_id))
            .join("، ");
    }

    _existingLine(comboItem = null) {
        if (!this.props.line) {
            return null;
        }
        if (!comboItem) {
            return this.props.line;
        }
        return this.props.line
            .getAllLinesInCombo()
            .find((line) => line.combo_item_id?.id === comboItem.id);
    }

    _existingModifierQuantities(comboItem = null) {
        const line = this._existingLine(comboItem);
        if (!line) {
            return {};
        }
        if (line.modifier_selection_data) {
            return { ...line.modifier_selection_data };
        }
        const divisor = Math.abs(line.qty) || 1;
        return (line.modifier_child_line_ids || []).reduce((quantities, modifierLine) => {
            const optionId = modifierLine.modifier_option_id?.id;
            if (optionId) {
                quantities[optionId] =
                    (quantities[optionId] || 0) +
                    (modifierLine.modifier_qty_per_parent ||
                        Math.abs(modifierLine.qty) / divisor);
            }
            return quantities;
        }, {});
    }

    _initializeTarget(productTemplate, comboItem = null) {
        const key = this.targetKey(comboItem);
        const existingLine = this._existingLine(comboItem);
        const existing = this._existingModifierQuantities(comboItem);
        this.state.modifierQuantities[key] = {};
        this._optionsByTarget[key] = {};
        for (const line of this.modifierGroupLines(productTemplate)) {
            const group = line.modifier_group_id;
            this._optionsByTarget[key][group.id] = this._effectiveModifierOptions(line);
            for (const option of this.sortedModifierOptions(group, key)) {
                this.state.modifierQuantities[key][option.id] =
                    existing[option.id] ?? (existingLine ? 0 : option.default_qty ?? 0);
            }
            this.state.expandedGroups[`${key}-${line.id}`] =
                group.min_qty > 0 || this.modifierGroupQuantity(key, group) > 0;
        }
    }

    _initializeModifierState() {
        this._initializeTarget(this.props.productTemplate);
        for (const combo of this.sortedCombos) {
            for (const item of combo.combo_item_ids) {
                this._initializeTarget(item.product_id?.product_tmpl_id, item);
            }
        }
    }

    selectedComboItems(combo) {
        return combo.combo_item_ids.filter(
            (item) => (this.state.qty[combo.id][item.id] || 0) > 0
        );
    }

    modifierQuantity(targetKey, option) {
        return this.state.modifierQuantities[targetKey]?.[option.id] || 0;
    }

    modifierGroupQuantity(targetKey, group) {
        return this.sortedModifierOptions(group, targetKey).reduce(
            (total, option) => total + this.modifierQuantity(targetKey, option),
            0
        );
    }

    isModifierGroupValid(targetKey, group) {
        const quantity = this.modifierGroupQuantity(targetKey, group);
        return quantity >= group.min_qty && quantity <= group.max_qty &&
            this.sortedModifierOptions(group, targetKey).every((option) => {
                const current = this.modifierQuantity(targetKey, option);
                return !parseModifierQuantity(current, {
                    current,
                    optionMax: option.max_qty,
                    groupTotal: quantity,
                    groupMax: group.max_qty,
                    rounding: option.product_id?.uom_id?.rounding,
                }).error;
            });
    }

    modifierValidationMessage(group, targetKey) {
        if (this.sortedModifierOptions(group, targetKey).some((option) => {
            const quantity = this.modifierQuantity(targetKey, option);
            return quantity && parseModifierQuantity(quantity, {
                current: quantity,
                optionMax: option.max_qty,
                groupTotal: this.modifierGroupQuantity(targetKey, group),
                groupMax: group.max_qty,
                rounding: option.product_id?.uom_id?.rounding,
            }).error === "unit";
        })) {
            return _t("The modifier product unit of measure cannot represent this quantity.");
        }
        return _t("Select between %(min)s and %(max)s options.", {
            min: group.min_qty,
            max: group.max_qty,
        });
    }

    modifierSectionTitle(product) {
        return _t("Customize %(product)s", { product: this.productName(product) });
    }

    isGroupExpanded(targetKey, line) {
        return Boolean(this.state.expandedGroups[`${targetKey}-${line.id}`]);
    }

    toggleGroup(targetKey, line) {
        const key = `${targetKey}-${line.id}`;
        this.state.expandedGroups[key] = !this.state.expandedGroups[key];
    }

    modifierSelectionSummary(targetKey, group) {
        return this.sortedModifierOptions(group, targetKey)
            .filter((option) => this.modifierQuantity(targetKey, option) > 0)
            .map((option) => option.name)
            .join("، ");
    }

    isComboValid(combo) {
        const quantity = this.totalQuantityForCombo(combo.id);
        return quantity >= combo.qty_min && quantity <= combo.qty_max;
    }

    get selectionProgress() {
        let completed = 0;
        let total = 0;
        const register = (valid) => {
            total += 1;
            completed += Number(valid);
        };
        for (const line of this.modifierGroupLines(this.props.productTemplate)) {
            register(this.isModifierGroupValid(this.targetKey(), line.modifier_group_id));
        }
        for (const combo of this.sortedCombos) {
            register(this.isComboValid(combo));
            for (const item of this.selectedComboItems(combo)) {
                const template = item.product_id.product_tmpl_id;
                for (const line of this.modifierGroupLines(template)) {
                    register(
                        this.isModifierGroupValid(
                            this.targetKey(item),
                            line.modifier_group_id
                        )
                    );
                }
            }
        }
        return { completed, total };
    }

    get progressText() {
        const { completed, total } = this.selectionProgress;
        return _t("%(completed)s of %(total)s complete", { completed, total });
    }

    get confirmationHint() {
        return this.canConfirmUnified
            ? _t("Your choices are ready.")
            : _t("Complete the required choices to continue.");
    }

    get confirmButtonLabel() {
        return this.props.line ? _t("Update order") : _t("Add to order");
    }

    _setLimitMessage(key) {
        this.state.limitMessages[key] = _t("Maximum quantity reached.");
    }

    _clearLimitMessage(key) {
        this.state.limitMessages[key] = "";
    }

    setModifierQuantity(targetKey, group, option, rawValue, input = null) {
        const current = this.modifierQuantity(targetKey, option);
        const result = parseModifierQuantity(rawValue, {
            current,
            optionMax: option.max_qty,
            groupTotal: group.selection_type === "single"
                ? current : this.modifierGroupQuantity(targetKey, group),
            groupMax: group.max_qty,
            rounding: option.product_id?.uom_id?.rounding,
        });
        const messageKey = `modifier-${targetKey}-${group.id}`;
        if (result.error) {
            this.state.limitMessages[messageKey] = {
                invalid: _t("Enter a quantity with up to three decimal places."),
                maximum: _t("Maximum quantity reached."),
                unit: _t("The modifier product unit of measure cannot represent this quantity."),
            }[result.error];
            if (input) {
                input.value = current;
            }
            return;
        }
        if (group.selection_type === "single" && result.value > 0) {
            for (const otherOption of this.sortedModifierOptions(group, targetKey)) {
                this.state.modifierQuantities[targetKey][otherOption.id] = 0;
            }
        }
        this.state.modifierQuantities[targetKey][option.id] = result.value;
        this._clearLimitMessage(messageKey);
    }

    increaseModifier(targetKey, group, option) {
        const messageKey = `modifier-${targetKey}-${group.id}`;
        const current = this.modifierQuantity(targetKey, option);
        if (group.selection_type === "single") {
            this._clearLimitMessage(messageKey);
            for (const otherOption of this.sortedModifierOptions(group, targetKey)) {
                this.state.modifierQuantities[targetKey][otherOption.id] = 0;
            }
            this.state.modifierQuantities[targetKey][option.id] = 1;
            return;
        }
        this.setModifierQuantity(targetKey, group, option, current + 1);
    }

    decreaseModifier(targetKey, group, option) {
        this.setModifierQuantity(
            targetKey, group, option,
            Math.max(0, this.modifierQuantity(targetKey, option) - 1)
        );
    }

    async onClickSimpleProduct(comboItem, combo) {
        const messageKey = `combo-${combo.id}`;
        const itemQuantity = this.state.qty[combo.id][comboItem.id];
        if (
            combo.qty_max > 1 &&
            (this.totalQuantityForCombo(combo.id) >= combo.qty_max ||
                itemQuantity >= this._maximumItemQuantity(comboItem))
        ) {
            this._setLimitMessage(messageKey);
            return;
        }
        this._clearLimitMessage(messageKey);
        return super.onClickSimpleProduct(comboItem, combo);
    }

    async onClickConfigurableProduct(product, comboItem, combo) {
        const messageKey = `combo-${combo.id}`;
        const itemQuantity = this.state.qty[combo.id][comboItem.id];
        if (
            combo.qty_max > 1 &&
            (this.totalQuantityForCombo(combo.id) >= combo.qty_max ||
                itemQuantity >= this._maximumItemQuantity(comboItem))
        ) {
            this._setLimitMessage(messageKey);
            return;
        }
        this._clearLimitMessage(messageKey);
        return super.onClickConfigurableProduct(product, comboItem, combo);
    }

    setQuantity(comboItem, quantity) {
        if (quantity > this._maximumItemQuantity(comboItem)) {
            this._setLimitMessage(`combo-${comboItem.combo_id.id}`);
        } else {
            this._clearLimitMessage(`combo-${comboItem.combo_id.id}`);
        }
        const combo = comboItem.combo_id;
        const currentQuantity = this.state.qty[combo.id][comboItem.id];
        const availableGroupQuantity =
            combo.qty_max - this.totalQuantityForCombo(combo.id) + currentQuantity;
        const maximumQuantity = Math.min(
            availableGroupQuantity,
            this._maximumItemQuantity(comboItem)
        );
        this.state.qty[combo.id][comboItem.id] = Math.max(
            0,
            Math.min(quantity, maximumQuantity)
        );
    }

    _modifierUnitPrice(option, quantity = 1) {
        if (option.price_mode === "fixed_extra") {
            return option.extra_price;
        }
        if (option.price_mode === "product_pricelist" && option.product_id) {
            return option.product_id.getPrice(
                this.props.order.pricelist_id,
                quantity,
                0,
                false,
                option.product_id
            );
        }
        return 0;
    }

    formattedModifierPrice(option) {
        const price = this._modifierUnitPrice(option);
        if (!price) {
            return "";
        }
        const formattedPrice = this.env.utils.formatCurrency(Math.abs(price));
        return price > 0 ? `+ ${formattedPrice}` : `- ${formattedPrice}`;
    }

    _targetModifierExtra(productTemplate, targetKey, multiplier = 1) {
        let total = 0;
        for (const line of this.modifierGroupLines(productTemplate)) {
            const group = line.modifier_group_id;
            let includedRemaining = group.included_qty;
            for (const option of this.sortedModifierOptions(group, targetKey)) {
                const quantity = this.modifierQuantity(targetKey, option);
                if (!quantity || option.option_type === "instruction") {
                    continue;
                }
                const includedQuantity = Math.min(quantity, includedRemaining);
                includedRemaining -= includedQuantity;
                const paidQuantity = quantity - includedQuantity;
                total +=
                    paidQuantity *
                    this._modifierUnitPrice(option, paidQuantity * multiplier) *
                    multiplier;
            }
        }
        return total;
    }

    get modifierExtraTotal() {
        let total = this._targetModifierExtra(
            this.props.productTemplate,
            this.targetKey()
        );
        for (const combo of this.sortedCombos) {
            for (const item of this.selectedComboItems(combo)) {
                total += this._targetModifierExtra(
                    item.product_id.product_tmpl_id,
                    this.targetKey(item),
                    this.state.qty[combo.id][item.id]
                );
            }
        }
        return total;
    }

    get comboExtraTotal() {
        return this.sortedCombos.reduce(
            (total, combo) => total + this.computeComboExtraPrice(combo),
            0
        );
    }

    get extraTotal() {
        return this.comboExtraTotal + this.modifierExtraTotal;
    }

    get formattedExtraTotal() {
        return this.env.utils.formatCurrency(this.extraTotal);
    }

    get formattedGrandTotal() {
        return this.env.utils.formatCurrency(
            this.props.productTemplate.list_price + this.extraTotal
        );
    }

    _isTargetValid(productTemplate, comboItem = null) {
        const key = this.targetKey(comboItem);
        return this.modifierGroupLines(productTemplate).every((line) =>
            this.isModifierGroupValid(key, line.modifier_group_id)
        );
    }

    get canConfirmUnified() {
        if (!this.isConfirmButtonEnabled()) {
            return false;
        }
        if (!this._isTargetValid(this.props.productTemplate)) {
            return false;
        }
        return this.sortedCombos.every((combo) =>
            this.selectedComboItems(combo).every((item) =>
                this._isTargetValid(item.product_id.product_tmpl_id, item)
            )
        );
    }

    _targetSelections(productTemplate, comboItem = null) {
        const key = this.targetKey(comboItem);
        return this.modifierGroupLines(productTemplate).flatMap((line) =>
            this.sortedModifierOptions(line.modifier_group_id, key)
                .filter((option) => this.modifierQuantity(key, option) > 0)
                .map((option) => ({
                    group: line.modifier_group_id,
                    option,
                    qty: this.modifierQuantity(key, option),
                }))
        );
    }

    confirm() {
        if (!this.canConfirmUnified) {
            return;
        }
        const comboModifiers = {};
        for (const combo of this.sortedCombos) {
            for (const item of this.selectedComboItems(combo)) {
                comboModifiers[item.id] = this._targetSelections(
                    item.product_id.product_tmpl_id,
                    item
                );
            }
        }
        this.props.getPayload({
            comboPayload: this.getSelectedComboItems(),
            rootModifiers: this._targetSelections(this.props.productTemplate),
            comboModifiers,
        });
        this.props.close();
    }
}
