import assert from "node:assert/strict";
import test from "node:test";

import { parseModifierQuantity } from "../../src/app/utils/modifier_quantity.js";

const limits = { current: 0, optionMax: 2, groupTotal: 0, groupMax: 3, rounding: 0.001 };

test("accepts direct decimal quantities up to three places, including Arabic decimal input", () => {
    assert.deepEqual(parseModifierQuantity("0.125", limits), { value: 0.125 });
    assert.deepEqual(parseModifierQuantity("0,5", limits), { value: 0.5 });
    assert.deepEqual(parseModifierQuantity("٠٫١٢٥", limits), { value: 0.125 });
    assert.deepEqual(parseModifierQuantity("0", limits), { value: 0 });
});

test("rejects excessive precision and values beyond option or group limits", () => {
    assert.deepEqual(parseModifierQuantity("0.1255", limits), { error: "invalid" });
    assert.deepEqual(parseModifierQuantity("2.5", limits), { error: "maximum" });
    assert.deepEqual(
        parseModifierQuantity("1", { ...limits, current: 0.5, groupTotal: 2.75 }),
        { error: "maximum" }
    );
});

test("rejects quantities the product unit would round away", () => {
    assert.deepEqual(parseModifierQuantity("0.125", { ...limits, rounding: 0.01 }), {
        error: "unit",
    });
});
