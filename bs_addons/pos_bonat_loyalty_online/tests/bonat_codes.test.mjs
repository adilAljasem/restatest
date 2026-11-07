// node --test tests/
import test from "node:test";
import assert from "node:assert/strict";

import {
    applyBonatRedeemResult,
    bonatCodesNotRedeemed,
    bonatCodesRefused,
    bonatCodesToRedeem,
    bonatVoucherTotal,
    findBonatCode,
    makeBonatCodeEntry,
    migrateLegacyBonatCodes,
    restoreBonatCodes,
    splitBonatOrderDiscount,
} from "../static/src/app/bonat_codes.js";

const pct = (amount, cap = 0) => ({ is_percentage: true, amount, cap });
const fixed = (amount) => ({ is_percentage: false, amount, cap: 0 });

test("one percentage code", () => {
    assert.deepEqual(splitBonatOrderDiscount(100, [pct(10)]).amounts, [10]);
});

test("codes stack on what is left, in entry order", () => {
    const split = splitBonatOrderDiscount(100, [pct(10), fixed(5)]);
    assert.deepEqual(split.amounts, [10, 5]);
    assert.equal(split.total, 15);
    assert.equal(split.isFull, false);
});

test("two 50% codes are 50 + 25, never 100", () => {
    assert.deepEqual(splitBonatOrderDiscount(100, [pct(50), pct(50)]).amounts, [50, 25]);
});

test("fixed codes never take the order below zero", () => {
    const split = splitBonatOrderDiscount(12, [fixed(10), fixed(10)]);
    assert.deepEqual(split.amounts, [10, 2]);
    assert.equal(split.isFull, true);
});

test("a code after a full discount takes nothing", () => {
    const split = splitBonatOrderDiscount(19, [pct(100), fixed(5)]);
    assert.deepEqual(split.amounts, [19, 0]);
    assert.equal(split.isFull, true);
});

test("cap applies per code", () => {
    assert.deepEqual(splitBonatOrderDiscount(49, [pct(10, 3), pct(10, 3)]).amounts, [3, 3]);
});

test("amounts are rounded to the currency", () => {
    const split = splitBonatOrderDiscount(10, [pct(33.333)]);
    assert.equal(split.amounts[0], 3.33);
});

test("several codes on one order, each found once", () => {
    const codes = [
        makeBonatCodeEntry({ code: "645934", kind: "order", rule: pct(10) }),
        makeBonatCodeEntry({ code: " abc ", kind: "product" }),
    ];
    assert.equal(findBonatCode(codes, "645934").kind, "order");
    assert.equal(findBonatCode(codes, "ABC").kind, "product");
    assert.equal(findBonatCode(codes, "999"), undefined);
});

test("vouchers add up, refused ones do not count", () => {
    const a = makeBonatCodeEntry({ code: "V1", kind: "voucher", amount: 50 });
    const b = makeBonatCodeEntry({ code: "V2", kind: "voucher", amount: 20 });
    assert.equal(bonatVoucherTotal([a, b]), 70);
    applyBonatRedeemResult(b, { success: false, error: "used" });
    assert.equal(bonatVoucherTotal([a, b]), 50);
});

test("what still has to be used with Bonat", () => {
    const pending = makeBonatCodeEntry({ code: "1", kind: "product" });
    const used = applyBonatRedeemResult(makeBonatCodeEntry({ code: "2", kind: "product" }), {
        success: true,
    });
    const offline = applyBonatRedeemResult(makeBonatCodeEntry({ code: "3", kind: "product" }), {
        success: false,
        retriable: true,
        error: "Request error: timeout",
    });
    const refused = applyBonatRedeemResult(makeBonatCodeEntry({ code: "4", kind: "product" }), {
        success: false,
        error: "This code has already been used",
    });
    const all = [pending, used, offline, refused];
    assert.deepEqual(bonatCodesToRedeem(all).map((e) => e.code), ["1", "3"]);
    assert.deepEqual(bonatCodesRefused(all).map((e) => e.code), ["4"]);
    assert.deepEqual(bonatCodesNotRedeemed(all).map((e) => e.code), ["1", "3", "4"]);
    assert.equal(used.state, "redeemed");
    assert.equal(refused.error, "This code has already been used");
});

test("a redeem cut by a reload is retried, not assumed done", () => {
    const restored = restoreBonatCodes([
        { code: "1", kind: "order", rule: pct(10), state: "redeeming" },
        { code: "2", kind: "voucher", amount: 5, state: "redeemed" },
        { code: "", kind: "order" },
        { code: "3", kind: "unknown" },
    ]);
    assert.deepEqual(
        restored.map((e) => [e.code, e.state]),
        [
            ["1", "pending"],
            ["2", "redeemed"],
        ]
    );
});

test("orders saved by the single-code build are converted, as already used", () => {
    assert.deepEqual(migrateLegacyBonatCodes({}), []);
    const [order] = migrateLegacyBonatCodes({ appliedCode: "11", bonatDiscountRule: pct(10) });
    assert.equal(order.kind, "order");
    assert.equal(order.state, "redeemed");
    assert.equal(order.legacy, true);
    const [voucher] = migrateLegacyBonatCodes({
        appliedCode: "22",
        bonatVoucherMethod: "Voucher",
        bonatVoucherAmount: 30,
    });
    assert.equal(voucher.kind, "voucher");
    assert.equal(voucher.amount, 30);
    const [product] = migrateLegacyBonatCodes({ appliedCode: "33" });
    assert.equal(product.kind, "product");
});
