import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const exportsObj = {};
const source = readFileSync(new URL("../static/src/js/order_number_range.js", import.meta.url), "utf8")
    .replace(/^import .*;\r?\n/gm, "")
    .replace(/^export function (\w+)/gm, "exports.$1 = $1; function $1")
    .replace(/^export const (\w+)/gm, "const $1");
class PosStore {
    setNextOrderRefs(order) { order.pos_reference = `2610-1-${String(this.next++).padStart(6, "0")}`; order.tracking_number = "1" + order.pos_reference.slice(-3); }
}
const storage = new Map();
const localStorage = { getItem: (k) => storage.get(k) ?? null, setItem: (k, v) => storage.set(k, String(v)) };
vm.runInNewContext(source, {
    PosStore, exports: exportsObj, localStorage,
    patch: (target, methods) => {
        const parent = Object.create(Object.getPrototypeOf(target), Object.getOwnPropertyDescriptors(target));
        Object.setPrototypeOf(methods, parent);
        Object.defineProperties(target, Object.getOwnPropertyDescriptors(methods));
    },
});
const { cycleOrderNumber, rangeOrderNumber } = exportsObj;

test("1 to 100: 001 ... 100 then 001 again", () => {
    assert.equal(cycleOrderNumber(1, 1, 100), "001");
    assert.equal(cycleOrderNumber(57, 1, 100), "057");
    assert.equal(cycleOrderNumber(100, 1, 100), "100");
    assert.equal(cycleOrderNumber(101, 1, 100), "001");
    assert.equal(cycleOrderNumber(250, 1, 100), "050");
});

test("100 to 200: 100 ... 200 then 100 again", () => {
    assert.equal(cycleOrderNumber(1, 100, 200), "100");
    assert.equal(cycleOrderNumber(101, 100, 200), "200");
    assert.equal(cycleOrderNumber(102, 100, 200), "100");
});

test("always 3 digits, also when the range is disabled or invalid", () => {
    assert.equal(rangeOrderNumber({ order_number_range_enabled: false }, "2610-1-000005"), "");
    // Invalid range (end before start, end over 999): plain 001 ... 999.
    assert.equal(rangeOrderNumber({ order_number_range_enabled: true, order_number_start: 50, order_number_end: 10 }, "2610-1-000005"), "005");
    assert.equal(cycleOrderNumber(5, 1, 5000), "005");
    assert.equal(cycleOrderNumber(1000), "001");
    assert.equal(cycleOrderNumber(999), "999");
    assert.equal(cycleOrderNumber(1, 7, 20), "007");
    for (let i = 1; i < 3000; i += 37) {
        assert.match(cycleOrderNumber(i, 1, 100), /^\d{3}$/);
        assert.match(cycleOrderNumber(i), /^\d{3}$/);
    }
});

test("new orders of the POS get the range number, reset at each session", () => {
    const store = new PosStore();
    store.next = 99;
    store.config = { id: 4, order_number_range_enabled: true, order_number_start: 1, order_number_end: 3 };
    store.session = { id: 10 };
    const take = (count) => Array.from({ length: count }, () => {
        const order = {};
        store.setNextOrderRefs(order);
        return order.tracking_number;
    });
    assert.deepEqual(take(4), ["001", "002", "003", "001"]);
    // Page reloaded in the same session: numbering continues.
    assert.deepEqual(take(1), ["002"]);
    // Session closed, new session opened: starts again from the start number.
    store.session = { id: 11 };
    assert.deepEqual(take(3), ["001", "002", "003"]);
    // Range disabled: Odoo's "1107" (device 1 + 107) becomes 3 digits "107".
    store.config = { id: 4, order_number_range_enabled: false };
    const order = {};
    store.setNextOrderRefs(order);
    assert.equal(order.tracking_number, "107");
});

test("session base is kept per POS and session", () => {
    const { sessionBase } = exportsObj;
    const bases = {};
    assert.equal(sessionBase(1, 5, 40, bases), 40);
    assert.equal(sessionBase(1, 5, 45, bases), 40);
    assert.equal(sessionBase(2, 7, 3, bases), 3);
    assert.equal(sessionBase(1, 6, 60, bases), 60);
    assert.equal(bases["1-5"], undefined);
    assert.equal(bases["2-7"], 3);
});
