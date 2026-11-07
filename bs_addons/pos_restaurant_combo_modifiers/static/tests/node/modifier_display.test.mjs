import assert from "node:assert/strict";
import test from "node:test";

import { arrangeModifierLines } from "../../src/app/utils/modifier_display.js";

test("POS display nests modifiers after their product, including combo items", () => {
    const cheese = { id: "cheese", is_modifier_line: true };
    const sauce = { id: "sauce", is_modifier_line: true };
    const burger = { id: "burger", modifier_child_line_ids: [cheese] };
    const comboItem = { id: "combo-item", modifier_child_line_ids: [sauce] };
    const cola = { id: "cola" };

    assert.deepEqual(
        arrangeModifierLines([cheese, burger, comboItem, cola, sauce], "display").map(
            (line) => line.id
        ),
        ["burger", "cheese", "combo-item", "sauce", "cola"]
    );
});

test("receipt keeps parent and combo lines while omitting financial modifier rows", () => {
    const modifier = { id: "modifier", is_modifier_line: true };
    const burger = { id: "burger", modifier_child_line_ids: [modifier] };
    const comboItem = { id: "combo-item" };

    assert.deepEqual(
        arrangeModifierLines([burger, modifier, comboItem], "receipt").map((line) => line.id),
        ["burger", "combo-item"]
    );
});
