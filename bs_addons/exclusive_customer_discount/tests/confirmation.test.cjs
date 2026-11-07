// Run with: node --test exclusive_loyalty_limit/tests/exclusive_promotion_confirmation.test.cjs
// Exercise the shipped patch with a POS lifecycle stub, including the reward
// recalculation that runs inside the standard selectPartner implementation.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function fixture(answers = [true], options = {}) {
    const partner = { id: 7, name: 'Employee A', is_exclusive_customer: true };
    const program = {
        id: 10, name: 'Staff Discount', exclusive_customer: true, program_type: 'promotion',
        limit_for_customer: true, exclusive_limit: 2, exclusive_usage_by_partner: { '7': 0 },
        exclusive_eligible_by_partner: { '7': true },
        update(values) { Object.assign(this, values); },
    };
    const prompts = [];
    const automaticApplications = [];
    class PosOrder {
        constructor() { this.uiState = {}; }
        setPartner(value) { this.partner = value; }
        getPartner() { return this.partner; }
        _get_reward_lines() { return this.rewardLines || []; }
        _programIsApplicable(value) { return value.standardApplicable !== false; }
    }
    class PosStore {
        getOrder() { return this.order; }
        async selectPartner() {
            this.order.setPartner(this.selectedPartner);
            await this.updateRewards();
            return this.selectedPartner;
        }
        updateRewards() {
            automaticApplications.push(this.models['loyalty.program'].filter(
                (value) => this.order._programIsApplicable(value)
            ).map((value) => value.id));
        }
    }
    class PaymentScreen {}
    const source = fs.readFileSync(path.join(__dirname,
        '../static/src/js/employee_promotion_limit.js'), 'utf8')
        .replace(/^import .*;\r?\n/gm, '');
    vm.runInNewContext(source, {
        PosOrder, PosStore, PaymentScreen, AlertDialog: class {},
        _t: (text, ...values) => text.replace(/%s/g, () => values.shift()),
        ask: async (_, props) => {
            prompts.push(props);
            assert.equal(store.order._programIsApplicable(program), false,
                'Employee discount must be blocked while confirmation is open');
            const answer = answers.shift();
            return typeof answer === 'function' ? await answer() : answer;
        },
        patch: (target, extension) => {
            const parent = Object.create(Object.getPrototypeOf(target));
            Object.defineProperties(parent, Object.getOwnPropertyDescriptors(target));
            Object.setPrototypeOf(extension, parent);
            Object.defineProperties(target, Object.getOwnPropertyDescriptors(extension));
        },
    });
    const store = new PosStore();
    store.order = new PosOrder();
    store.selectedPartner = partner;
    store.dialog = {};
    store.config = { id: 1 };
    store.models = { 'loyalty.program': [program] };
    store.data = { call: async () => {
        if (options.rpcError) { throw new Error('Offline'); }
        return options.status || { eligible: { '10': true }, usage: { '10': 0 } };
    } };
    return { store, program, partner, prompts, automaticApplications, PosOrder };
}

test('confirmation happens before exclusive discount can be applied', async () => {
    const f = fixture([true]);
    await f.store.selectPartner();
    assert.equal(f.prompts.length, 1);
    assert.match(f.prompts[0].body, /Staff Discount/);
    assert.equal(f.automaticApplications[0].includes(10), false);
    assert.equal(f.store.order._programIsApplicable(f.program), true);
});

for (const answer of [false, undefined]) {
    test(`decline/close (${answer}) remains blocked through reward updates`, async () => {
        const f = fixture([answer]);
        await f.store.selectPartner();
        f.store.updateRewards();
        assert.equal(f.store.order._programIsApplicable(f.program), false);
        assert.ok(f.automaticApplications.every((ids) => !ids.includes(10)));
    });
}

test('regular promotions retain automatic behavior', async () => {
    const f = fixture([false]);
    const regular = { id: 20, exclusive_customer: false, program_type: 'promotion' };
    f.store.models['loyalty.program'].push(regular);
    await f.store.selectPartner();
    assert.ok(f.automaticApplications.every((ids) => ids.includes(20)));
});

test('approval stays with its exclusive/order and is cleared before customer changes', async () => {
    const f = fixture([true]);
    await f.store.selectPartner();
    const other = { id: 8, name: 'Employee B', is_exclusive_customer: true };
    f.program.exclusive_eligible_by_partner['8'] = true;
    f.store.order.setPartner(other);
    assert.equal(f.store.order._programIsApplicable(f.program), false);
    f.store.order.setPartner(f.partner);
    assert.equal(f.store.order._programIsApplicable(f.program), false);
    const next = new f.PosOrder();
    next.setPartner(f.partner);
    assert.equal(next._programIsApplicable(f.program), false);
});

test('approved exclusive is not prompted twice; declined exclusive can be reconsidered', async () => {
    const f = fixture([false, true]);
    await f.store.selectPartner();
    await f.store.selectPartner();
    await f.store.selectPartner();
    assert.equal(f.prompts.length, 2);
    assert.equal(f.store.order._programIsApplicable(f.program), true);
});

for (const [label, options] of [
    ['outside exclusive domain', { status: { eligible: { '10': false }, usage: { '10': 0 } } }],
    ['usage exhausted', { status: { eligible: { '10': true }, usage: { '10': 2 } } }],
    ['offline', { rpcError: true }],
]) {
    test(`${label}: no approval and no discount`, async () => {
        const f = fixture([true], options);
        await f.store.selectPartner();
        assert.equal(f.prompts.length, 0);
        assert.equal(f.store.order._programIsApplicable(f.program), false);
    });
}

test('answer to a stale popup cannot approve another exclusive', async () => {
    const f = fixture([() => {
        f.store.order.setPartner({ id: 8, name: 'Employee B', is_exclusive_customer: true });
        f.program.exclusive_eligible_by_partner['8'] = true;
        return true;
    }]);
    await f.store.selectPartner();
    assert.equal(f.store.order._programIsApplicable(f.program), false);
});

test('each eligible exclusive promotion needs its own confirmation', async () => {
    const f = fixture([false, true], {
        status: { eligible: { '10': true, '11': true }, usage: { '10': 0, '11': 0 } },
    });
    const second = { ...f.program, id: 11, name: 'Second Staff Discount' };
    f.store.models['loyalty.program'].push(second);
    await f.store.selectPartner();
    assert.equal(f.prompts.length, 2);
    assert.equal(f.store.order._programIsApplicable(f.program), false);
    assert.equal(f.store.order._programIsApplicable(second), true);
});
 test('customer removal immediately deletes existing exclusive reward lines', async () => {
    const f = fixture([true]);
    await f.store.selectPartner();
    let removed = false;
    f.store.order.rewardLines = [{reward_id: {program_id: f.program}, delete() { removed = true; }}];
    f.store.order.setPartner(null);
    assert.equal(removed, true);
    assert.equal(f.store.order._programIsApplicable(f.program), false);
 });
 test('disabled customer limit permits usage above maximum', async () => {
    const f = fixture([true], {status: {eligible: {'10': true}, usage: {'10': 99}}});
    f.program.limit_for_customer = false;
    await f.store.selectPartner();
    assert.equal(f.store.order._programIsApplicable(f.program), true);
 });
