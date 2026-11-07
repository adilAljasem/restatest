import assert from "node:assert/strict";
import test from "node:test";

import {
    changedKitchenInstructions,
    kitchenInstructionSnapshot,
    kitchenProductSnapshot,
    nestKitchenModifierChanges,
} from "../../src/app/utils/kitchen_print.js";

test("kitchen print groups included and paid slices below one parent", () => {
    const parent = { uuid: "burger", basic_name: "Burger", quantity: 1 };
    const cheese = {
        modifier_parent_uuid: "burger", modifier_option_id: 9,
        modifier_name: "Cheese", modifier_operation: "add", quantity: 0.5,
    };
    const result = nestKitchenModifierChanges(
        [parent, cheese, { ...cheese, quantity: 0.25 }],
        { burger: parent },
        { burger: [{ kind: "instruction", name: "No Onion", quantity: 1 }] }
    );

    assert.equal(result.length, 1);
    assert.deepEqual(result[0].modifier_print_details, [
        { key: "option:9", kind: "product", name: "Cheese", operation: "add", quantity: 0.75 },
        { kind: "instruction", name: "No Onion", quantity: 1 },
    ]);
});

test("kitchen print restores parent context when only a modifier changes", () => {
    const result = nestKitchenModifierChanges(
        [{ modifier_parent_uuid: "burger", modifier_option_id: 9,
            modifier_name: "Cheese", modifier_operation: "add", quantity: 1 }],
        { burger: { uuid: "burger", basic_name: "Burger", quantity: 1 } },
        {}
    );

    assert.equal(result.length, 1);
    assert.equal(result[0].basic_name, "Burger");
    assert.equal(result[0].modifier_print_details[0].name, "Cheese");
});

test("instruction-only edits produce a printable delta", () => {
    const previous = kitchenInstructionSnapshot({ instructions: [
        { option_id: 5, kitchen_name: "No Onion", operation: "remove", quantity: 1 },
    ] });
    const current = kitchenInstructionSnapshot({ instructions: [
        { option_id: 6, kitchen_name: "Light Sauce", operation: "note", quantity: 0.5 },
    ] });

    assert.deepEqual(changedKitchenInstructions(previous, current), [
        { key: "instruction:6", kind: "instruction", name: "Light Sauce",
            operation: "note", quantity: 0.5, change_status: "added" },
        { key: "instruction:5", kind: "instruction", name: "No Onion",
            operation: "remove", quantity: 1, change_status: "removed" },
    ]);
});

test("product modifier snapshots combine fractional slices and report later edits", () => {
    const option = { id: 9, operation: "add" };
    const previous = kitchenProductSnapshot([
        { modifier_option_id: option, modifier_option_name: "Cheese", qty: 1 },
        { modifier_option_id: option, modifier_option_name: "Cheese", qty: 0.25 },
    ]);
    const current = kitchenProductSnapshot([
        { modifier_option_id: option, modifier_option_name: "Cheese", qty: 1.5 },
    ]);
    assert.deepEqual(previous[0].quantity, 1.25);
    assert.deepEqual(changedKitchenInstructions(previous, current), [{
        key: "option:9", kind: "product", name: "Cheese", operation: "add",
        quantity: 1.5, change_status: "updated",
    }]);
});
