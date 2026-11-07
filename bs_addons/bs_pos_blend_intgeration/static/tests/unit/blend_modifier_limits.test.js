import { expect, test } from "@odoo/hoot";
import { ModifierProductPopup } from "@product_modifiers/js/Popups/ModifierProductPopup";
import { patchTranslations } from "@web/../tests/web_test_helpers";
import "@bs_pos_blend_intgeration/js/blend_modifier_limits";

function modifierPopup(overrides = {}) {
    patchTranslations();
    const popup = Object.create(ModifierProductPopup.prototype);
    const dialogs = [];
    popup.props = {
        product: {},
        products: [{
            modifier_line_id: 156,
            product: { id: 1312, display_name: "Add Grilled Chicken" },
            apply_selector: true,
            min_selector: 0,
            max_selector: 3,
            blend_group_external_id: false,
            blend_max_per_quantity: 1,
            ...overrides,
        }],
    };
    popup.root = { el: null };
    popup.dialog = { add: (_component, props) => dialogs.push(props) };
    popup.modifiers_list = [{
        product: "1312",
        modifier_line_id: "156",
        selection_key: "156:1312:::",
        selection_quantity: 1,
        quantity: 1,
        portion_no: false,
        total_portion: false,
    }];
    const attributes = {
        "data-modifier-key": "156:1312:::",
        "data-modifier-cart-action": "increase",
        "data-modifier-line-id": "156",
        "data-product-id": "1312",
    };
    const event = {
        currentTarget: {
            getAttribute: (name) => attributes[name] ?? null,
            closest: () => null,
        },
    };
    return { popup, event, dialogs };
}

for (const method of ["updateModifierQuantity", "SelectExtraModifier"]) {
    test(`${method}: ordinary modifiers reach their selector maximum despite Blend defaults`, () => {
        const { popup, event, dialogs } = modifierPopup();
        popup[method](event);
        expect(popup.modifiers_list[0].selection_quantity).toBe(2);
        popup[method](event);
        expect(popup.modifiers_list[0].selection_quantity).toBe(3);
        expect(popup.modifiers_list[0].quantity).toBe(3);
        expect(dialogs).toHaveLength(0);

        popup[method](event);
        expect(popup.modifiers_list[0].selection_quantity).toBe(3);
        expect(dialogs).toHaveLength(1);
    });

    test(`${method}: ordinary modifiers without a selector limit can exceed the default maximum`, () => {
        const { popup, event, dialogs } = modifierPopup({ apply_selector: false });
        for (let i = 0; i < 3; i++) {
            popup[method](event);
        }
        expect(popup.modifiers_list[0].selection_quantity).toBe(4);
        expect(popup.modifiers_list[0].quantity).toBe(4);
        expect(dialogs).toHaveLength(0);
    });

    test(`${method}: Blend modifiers still enforce their per-option maximum`, () => {
        const { popup, event, dialogs } = modifierPopup({
            blend_group_external_id: "blend-toppings",
        });
        popup[method](event);
        expect(popup.modifiers_list[0].selection_quantity).toBe(1);
        expect(popup.modifiers_list[0].quantity).toBe(1);
        expect(dialogs).toHaveLength(1);
    });
}
