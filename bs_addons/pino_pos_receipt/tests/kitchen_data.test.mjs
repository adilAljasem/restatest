import assert from 'node:assert/strict';
import { test } from 'node:test';
import { prepareBlendKitchenReceipt, buildPinoKitchenData } from '../static/src/kitchen_data.js';
import { drainBlendKitchenPrint } from '../../bs_pos_blend_intgeration/static/src/js/blend_kitchen_worker.js';

const config = { pino_kitchen_design: true, pino_show_latin_name: true };
const now = new Date(2026, 8, 25, 14, 5, 6);
const receipt = () => ({
    blend_kitchen: { version: 2, reference: 'hunger: 123', order_type: 'delivery', order_time: '11:02' },
    tracking_number: '41003', employee_name: 'Blend', preset_name: 'delivery',
    changes: { title: 'Blend', data: [
        { uuid: 'parent', quantity: 1, basic_name: 'Rice', product_name_ar: 'أرز', pino_latin: 'Rice' },
        { uuid: 'other', quantity: 2, basic_name: 'Pasta' },
        { uuid: 'child', quantity: 1, basic_name: 'Chicken', isModifierChild: true, modifier_parent_uuid: 'parent' },
    ] },
});

test('Blend uses the cashier design settings, short number and immutable snapshot', () => {
    const source = receipt();
    const before = structuredClone(source);
    const result = prepareBlendKitchenReceipt(source, config, now);
    assert.equal(result.pino.enabled, true);
    assert.equal(result.pino.trackingNumber, '003');
    assert.equal(result.pino.orderTime, '11:02');
    assert.equal(result.changes.data[0].basic_name, 'أرز');
    assert.equal(result.changes.data[0].pino_latin, 'Rice');
    assert.deepEqual(result.changes.data.map(line => line.uuid), ['parent', 'child', 'other']);
    assert.equal(result.pino.blendReference, 'hunger: 123');
    assert.deepEqual(source, before);
    assert.equal(result.pino.printTime, buildPinoKitchenData(config, {}, now).printTime);
});

test('disabled design preserves native receipt names and unrelated receipts are untouched', () => {
    const source = receipt();
    assert.equal(prepareBlendKitchenReceipt(source, { ...config, pino_kitchen_design: false }, now), source);
    const ordinary = { changes: { data: [] } };
    assert.equal(prepareBlendKitchenReceipt(ordinary, config, now), ordinary);
});

test('missing parent and cyclic relationships cannot drop or duplicate items', () => {
    const source = receipt();
    source.changes.data = [
        { uuid: 'a', modifier_parent_uuid: 'b', quantity: 1 },
        { uuid: 'b', modifier_parent_uuid: 'a', quantity: 2 },
        { uuid: 'c', combo_parent_uuid: 'missing', quantity: 3 },
    ];
    const result = prepareBlendKitchenReceipt(source, config, now);
    assert.equal(result.changes.data.length, 3);
    assert.equal(new Set(result.changes.data.map(line => line.uuid)).size, 3);
    assert.equal(result.changes.data.reduce((sum, line) => sum + line.quantity, 0), 6);
});

test('general notes appear once and item notes stay attached to the item', () => {
    const source = receipt();
    source.blend_kitchen.general_note = 'No cutlery';
    source.changes.data[0].customer_note = 'No salt\nNo cutlery';
    source.changes.data[0].blend_line_customer_note = 'No salt';
    const result = prepareBlendKitchenReceipt(source, config, now);
    assert.equal(result.general_customer_note, 'No cutlery');
    assert.equal(result.changes.data[0].customer_note, 'No salt');
    assert.equal(source.changes.data[0].customer_note, 'No salt\nNo cutlery');
});

test('a lost acknowledgement never renders or prints the enriched Blend ticket twice', async () => {
    let job = { id: 42, token: 'token', printer_id: 7, receipt: receipt() };
    let failAck = true;
    let prints = 0;
    const store = {
        config: { ...config, id: 1 }, session: { id: 5 },
        unwatched: { printers: [{ config: { id: 7 } }] },
        data: { network: { offline: false }, async call(model, method) {
            if (method === 'claim_next') {
                const next = job;
                job = null;
                return next;
            }
            if (failAck) {
                failAck = false;
                throw new Error('Connection lost after printing');
            }
            return true;
        } },
        async printOrderChanges(data) {
            const prepared = prepareBlendKitchenReceipt(data, this.config, now);
            assert.equal(prepared.pino.trackingNumber, '003');
            prints++;
            return { successful: true };
        },
        _blendKitchenNotify() {},
    };
    await drainBlendKitchenPrint(store);
    assert.ok(store._blendKitchenAck);
    await Promise.all([drainBlendKitchenPrint(store), drainBlendKitchenPrint(store)]);
    assert.equal(prints, 1);
    assert.equal(store._blendKitchenAck, null);
});
