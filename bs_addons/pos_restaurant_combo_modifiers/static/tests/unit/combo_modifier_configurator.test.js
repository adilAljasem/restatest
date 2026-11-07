import { expect, test } from "@odoo/hoot";
import { ComboModifierConfiguratorPopup } from "@pos_restaurant_combo_modifiers/app/components/popups/combo_modifier_configurator_popup/combo_modifier_configurator_popup";
import {
    deleteModifierChildren,
    syncModifierDiscounts,
    syncModifierQuantities,
} from "@pos_restaurant_combo_modifiers/app/models/pos_order_line";
import "@pos_restaurant_combo_modifiers/app/services/pos_store";
import { PosStore } from "@point_of_sale/app/services/pos_store";

function option(id, values = {}) {
    return {
        id,
        name: `Option ${id}`,
        sequence: id,
        option_type: "product",
        operation: "add",
        price_mode: "included",
        extra_price: 0,
        default_qty: 0,
        max_qty: 3,
        product_id: {
            id: 100 + id,
            taxes_id: [{ id: 1 }],
            getPrice: () => 12,
        },
        ...values,
    };
}

function group(id, options, values = {}) {
    return {
        id,
        name: `Group ${id}`,
        sequence: id,
        selection_type: "multiple",
        min_qty: 0,
        max_qty: 3,
        included_qty: 0,
        option_ids: options,
        ...values,
    };
}

function product(lines = [], values = {}) {
    return {
        id: 1,
        display_name: "UAT Burger",
        allow_modifiers: true,
        is_pos_modifier_product: false,
        modifier_group_line_ids: lines,
        combo_ids: [],
        ...values,
    };
}

function popup(productTemplate, quantities = {}) {
    const instance = Object.create(ComboModifierConfiguratorPopup.prototype);
    instance.pos = {
        config: {
            enable_product_modifiers: true,
            modifier_display_type: "grid",
        },
        models: { "product.combo": { get: () => null } },
    };
    instance.props = {
        productTemplate,
        order: { pricelist_id: { id: 1 } },
        line: null,
    };
    instance.state = {
        qty: {},
        modifierQuantities: { root: { ...quantities } },
        expandedGroups: {},
        expandedCombos: {},
        limitMessages: {},
    };
    Object.defineProperty(instance, "sortedCombos", {
        configurable: true,
        value: [],
    });
    return instance;
}

test("loads required groups first for products also used as modifier options", () => {
    const optional = group(1, [option(1)]);
    const required = group(2, [option(2)], { min_qty: 1 });
    const productTemplate = product([
        { id: 1, sequence: 1, modifier_group_id: optional },
        { id: 2, sequence: 50, modifier_group_id: required },
    ]);
    const instance = popup(productTemplate);

    expect(instance.modifierGroupLines(productTemplate).map((line) => line.id)).toEqual([2, 1]);
    const dualUseProduct = { ...productTemplate, is_pos_modifier_product: true };
    expect(instance.modifierGroupLines(dualUseProduct).map((line) => line.id)).toEqual([2, 1]);
    expect(
        PosStore.prototype._shouldConfigureModifiers.call(
            { config: { enable_product_modifiers: true } },
            dualUseProduct,
            dualUseProduct.modifier_group_line_ids,
            true
        )
    ).toBe(true);

    instance.pos.config.enable_product_modifiers = false;
    expect(instance.modifierGroupLines(productTemplate)).toHaveLength(0);
});

test("presents clean names, progress, and collapsible required groups", () => {
    const requiredOption = option(1);
    const requiredGroup = group(1, [requiredOption], { min_qty: 1, max_qty: 1 });
    const requiredLine = { id: 10, sequence: 1, modifier_group_id: requiredGroup };
    const productTemplate = product([requiredLine], {
        name: "برجر لحم كلاسيك",
        display_name: "[TEST-BURG-BEEF] برجر لحم كلاسيك",
    });
    const instance = popup(productTemplate, { [requiredOption.id]: 0 });
    instance.state.expandedGroups["root-10"] = true;

    expect(instance.productName(productTemplate)).toBe("برجر لحم كلاسيك");
    expect(instance.comboName({ name: "TEST - وجبة / الوجبة الرئيسية" })).toBe(
        "الوجبة الرئيسية"
    );
    expect(instance.selectionProgress).toEqual({ completed: 0, total: 1 });

    instance.toggleGroup("root", requiredLine);
    expect(instance.isGroupExpanded("root", requiredLine)).toBe(false);
    instance.increaseModifier("root", requiredGroup, requiredOption);
    expect(instance.selectionProgress).toEqual({ completed: 1, total: 1 });
});

test("initializes default modifier selections and restores an edited line", () => {
    const defaultOption = option(1, { default_qty: 2 });
    const modifierGroup = group(1, [defaultOption]);
    const productTemplate = product([
        { id: 10, sequence: 1, modifier_group_id: modifierGroup },
    ]);
    const instance = popup(productTemplate);

    instance._initializeTarget(productTemplate);
    expect(instance.state.modifierQuantities.root[defaultOption.id]).toBe(2);

    instance.props.line = { modifier_selection_data: { [defaultOption.id]: 1 } };
    instance._initializeTarget(productTemplate);
    expect(instance.state.modifierQuantities.root[defaultOption.id]).toBe(1);
});

test("enforces single, multiple, option, and group maximum quantities locally", () => {
    const first = option(1);
    const second = option(2);
    const single = group(1, [first, second], {
        selection_type: "single",
        max_qty: 1,
    });
    const instance = popup(product(), { [first.id]: 1, [second.id]: 0 });

    instance.increaseModifier("root", single, second);
    expect(instance.modifierQuantity("root", first)).toBe(0);
    expect(instance.modifierQuantity("root", second)).toBe(1);
    expect(instance.state.limitMessages["modifier-root-1"]).toBeFalsy();

    instance.increaseModifier("root", single, first);
    expect(instance.modifierQuantity("root", first)).toBe(1);
    expect(instance.modifierQuantity("root", second)).toBe(0);
    expect(instance.state.limitMessages["modifier-root-1"]).toBeFalsy();

    const multipleOption = option(3, { max_qty: 2 });
    const multiple = group(2, [multipleOption], { max_qty: 2 });
    instance.state.modifierQuantities.root[multipleOption.id] = 0;
    instance.increaseModifier("root", multiple, multipleOption);
    instance.increaseModifier("root", multiple, multipleOption);
    instance.increaseModifier("root", multiple, multipleOption);
    expect(instance.modifierQuantity("root", multipleOption)).toBe(2);
});

test("confirmation stays disabled until required minimum is satisfied", () => {
    const requiredOption = option(1);
    const requiredGroup = group(1, [requiredOption], { min_qty: 1, max_qty: 2 });
    const productTemplate = product([
        { id: 1, sequence: 1, modifier_group_id: requiredGroup },
    ]);
    const instance = popup(productTemplate, { [requiredOption.id]: 0 });
    instance.isConfirmButtonEnabled = () => true;

    expect(instance.canConfirmUnified).toBe(false);
    instance.increaseModifier("root", requiredGroup, requiredOption);
    expect(instance.canConfirmUnified).toBe(true);
});

test("calculates included, fixed-extra, and pricelist modifier prices", () => {
    const included = option(1, { price_mode: "fixed_extra", extra_price: 5 });
    const pricelist = option(2, { price_mode: "product_pricelist" });
    const modifierGroup = group(1, [included, pricelist], {
        included_qty: 1,
        max_qty: 4,
    });
    const productTemplate = product([
        { id: 1, sequence: 1, modifier_group_id: modifierGroup },
    ]);
    const instance = popup(productTemplate, {
        [included.id]: 2,
        [pricelist.id]: 1,
    });

    expect(instance._modifierUnitPrice(included)).toBe(5);
    expect(instance._modifierUnitPrice(pricelist)).toBe(12);
    expect(instance.modifierExtraTotal).toBe(22);
});

test("creates real modifier lines, structured instructions, and stable configuration keys", () => {
    const fixed = option(1, { price_mode: "fixed_extra", extra_price: 5 });
    const instruction = option(2, {
        name: "No Onion",
        kitchen_name: "No Onion",
        option_type: "instruction",
        operation: "remove",
        product_id: false,
    });
    const modifierGroup = group(1, [fixed, instruction], { included_qty: 1 });
    const values = { qty: 2 };
    const order = { pricelist_id: { id: 1 } };
    const store = {
        _modifierPrice: PosStore.prototype._modifierPrice,
        _modifierConfigurationKey: PosStore.prototype._modifierConfigurationKey,
    };

    PosStore.prototype._applyProductModifiers.call(
        store,
        values,
        [
            { group: modifierGroup, option: fixed, qty: 2 },
            { group: modifierGroup, option: instruction, qty: 1 },
        ],
        order,
        10
    );

    expect(values.modifier_child_line_ids).toHaveLength(2);
    expect(values.modifier_child_line_ids[0][1].modifier_is_included).toBe(true);
    expect(values.modifier_child_line_ids[0][1].price_unit).toBe(0);
    expect(values.modifier_child_line_ids[1][1].price_unit).toBe(5);
    expect(values.modifier_child_line_ids[1][1].qty).toBe(2);
    expect(values.modifier_child_line_ids[1][1].discount).toBe(10);
    expect(values.modifier_instruction_data.instructions[0].name).toBe("No Onion");
    expect(values.modifier_configuration_key).toBeTruthy();

    const differentKey = store._modifierConfigurationKey([
        { group: modifierGroup, option: fixed, qty: 1 },
    ]);
    expect(differentKey).not.toBe(values.modifier_configuration_key);
});

test("does not create an empty modifier configuration for a combo root", () => {
    const values = { qty: 1 };
    const store = {
        _modifierPrice: PosStore.prototype._modifierPrice,
        _modifierConfigurationKey: PosStore.prototype._modifierConfigurationKey,
    };

    PosStore.prototype._applyProductModifiers.call(store, values, [], {
        pricelist_id: { id: 1 },
    });

    expect(values.modifier_child_line_ids).toEqual([]);
    expect(values.modifier_selection_data).toEqual({});
    expect(values.modifier_instruction_data).toEqual({});
    expect(values.modifier_configuration_key).toBe("");
});

test("reports a missing modifier product without raising a JavaScript error", () => {
    const missingProductOption = option(1, { product_id: false });
    const modifierGroup = group(1, [missingProductOption]);
    const values = { qty: 1 };
    let alertProps;
    const store = {
        dialog: {
            add(component, props) {
                alertProps = props;
            },
        },
        _modifierPrice: PosStore.prototype._modifierPrice,
        _modifierConfigurationKey: PosStore.prototype._modifierConfigurationKey,
    };

    const result = PosStore.prototype._applyProductModifiers.call(
        store,
        values,
        [{ group: modifierGroup, option: missingProductOption, qty: 1 }],
        { pricelist_id: { id: 1 } }
    );

    expect(result).toBe(false);
    expect(alertProps.title).toBe("Modifier product unavailable");
    expect(alertProps.body).toContain(missingProductOption.name);
    expect(values.modifier_child_line_ids).toBe(undefined);
});

test("deletion, discount, and quantity helpers keep modifier children synchronized", () => {
    const calls = [];
    const first = {
        qty: 2,
        modifier_qty_per_parent: 1,
        delete: (options) => calls.push(["delete-first", options.reason]),
        setDiscount: (discount) => calls.push(["discount-first", discount]),
        setQuantity: (quantity, keepPrice) =>
            calls.push(["quantity-first", quantity, keepPrice]),
    };
    const second = {
        qty: 4,
        modifier_qty_per_parent: 0,
        delete: (options) => calls.push(["delete-second", options.reason]),
        setDiscount: (discount) => calls.push(["discount-second", discount]),
        setQuantity: (quantity, keepPrice) =>
            calls.push(["quantity-second", quantity, keepPrice]),
    };
    const parent = {
        qty: 3,
        discount: 15,
        modifier_child_line_ids: [first, second],
    };

    syncModifierDiscounts(parent);
    syncModifierQuantities(parent, 2);
    deleteModifierChildren(parent, { reason: "parent" });

    expect(first.modifier_qty_per_parent).toBe(1);
    expect(second.modifier_qty_per_parent).toBe(2);
    expect(calls).toEqual([
        ["discount-first", 15],
        ["discount-second", 15],
        ["quantity-first", 3, true],
        ["quantity-second", 6, true],
        ["delete-first", "parent"],
        ["delete-second", "parent"],
    ]);
});

test("editing replaces old modifier children instead of duplicating them", () => {
    const deleted = [];
    const oldChildren = [
        { id: 1 },
        { id: 2 },
    ];
    const line = {
        modifier_child_line_ids: [...oldChildren],
    };
    const store = {
        data: {
            models: {
                "pos.order.line": {
                    delete(child) {
                        deleted.push(child.id);
                        line.modifier_child_line_ids.shift();
                    },
                },
            },
        },
    };
    const replacement = [["create", { modifier_option_id: 3 }]];

    PosStore.prototype._applyEditedRootModifiers.call(store, line, {
        modifier_child_line_ids: replacement,
        modifier_selection_data: { 3: 1 },
        modifier_instruction_data: {},
        modifier_configuration_key: "replacement",
    });

    expect(deleted).toEqual([1, 2]);
    expect(line.modifier_child_line_ids).toBe(replacement);
    expect(line.modifier_configuration_key).toBe("replacement");
});

test("combo defaults and single choices are selected automatically", () => {
    const comboItem = { id: 20, default_qty: 2, qty_max: 3 };
    const combo = { id: 10, qty_max: 3, combo_item_ids: [comboItem] };
    comboItem.combo_id = combo;
    const productTemplate = product([], { combo_ids: [combo] });
    const instance = popup(productTemplate);
    instance.state.qty = { [combo.id]: { [comboItem.id]: 0 } };
    Object.defineProperty(instance, "sortedCombos", {
        configurable: true,
        value: [combo],
    });

    instance._initializeComboDefaults();
    expect(instance.state.qty[combo.id][comboItem.id]).toBe(2);
    instance.state.qty[combo.id][comboItem.id] = 0;
    instance.autoSelectSingleChoices();
    expect(instance.state.qty[combo.id][comboItem.id]).toBe(1);
});

test("keeps modifiers attached to the selected main and drink combo children", () => {
    const mainOption = option(11, { default_qty: 1, price_mode: "fixed_extra", extra_price: 5 });
    const drinkOption = option(12, { default_qty: 1, price_mode: "fixed_extra", extra_price: 2 });
    const mainGroup = group(11, [mainOption], { min_qty: 1, max_qty: 1 });
    const drinkGroup = group(12, [drinkOption], { min_qty: 1, max_qty: 1 });
    const mainTemplate = product([
        { id: 111, sequence: 1, modifier_group_id: mainGroup },
    ], { id: 101, display_name: "Main" });
    const sideTemplate = product([], { id: 102, display_name: "Side", allow_modifiers: false });
    const drinkTemplate = product([
        { id: 112, sequence: 1, modifier_group_id: drinkGroup },
    ], { id: 103, display_name: "Drink" });
    const makeItem = (id, productTemplate) => ({
        id,
        default_qty: 1,
        qty_max: 1,
        product_id: { id: 200 + id, product_tmpl_id: productTemplate },
    });
    const mainItem = makeItem(1, mainTemplate);
    const sideItem = makeItem(2, sideTemplate);
    const drinkItem = makeItem(3, drinkTemplate);
    const combos = [
        { id: 1, qty_min: 1, qty_max: 1, combo_item_ids: [mainItem] },
        { id: 2, qty_min: 1, qty_max: 1, combo_item_ids: [sideItem] },
        { id: 3, qty_min: 1, qty_max: 1, combo_item_ids: [drinkItem] },
    ];
    for (const combo of combos) {
        combo.combo_item_ids[0].combo_id = combo;
    }
    const root = product([], { combo_ids: combos });
    const instance = popup(root);
    Object.defineProperty(instance, "sortedCombos", {
        configurable: true,
        value: combos,
    });
    instance.state.qty = {
        1: { 1: 1 },
        2: { 2: 1 },
        3: { 3: 1 },
    };

    instance._initializeModifierState();

    expect(instance._targetSelections(mainTemplate, mainItem)[0].option.id).toBe(mainOption.id);
    expect(instance._targetSelections(drinkTemplate, drinkItem)[0].option.id).toBe(
        drinkOption.id
    );
    expect(instance._targetSelections(sideTemplate, sideItem)).toHaveLength(0);
    expect(instance.modifierExtraTotal).toBe(7);
});
