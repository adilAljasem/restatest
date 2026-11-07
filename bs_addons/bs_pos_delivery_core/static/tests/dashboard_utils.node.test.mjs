import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

import { dashboardFilters, formatMetric, growthLabel } from "../src/dashboard/dashboard_utils.js";

test("filters are copied, typed and custom dates cannot leak into presets", () => {
    const draft = {
        period: "last7",
        company_id: "2",
        provider_id: "3",
        pos_config_id: "4",
        date_from: "2020-01-01",
        date_to: "2020-01-02",
        channel: "HUNGERSTATION",
        environment: "test",
    };
    // The environment filter was removed from the reports: it is never sent.
    assert.deepEqual(dashboardFilters(draft), {
        period: "last7",
        company_ids: [2],
        provider_id: 3,
        pos_config_id: 4,
        channel: "HUNGERSTATION",
    });
    assert.equal(draft.company_id, "2");
    assert.deepEqual(dashboardFilters({ period: "today" }), { period: "today" });
    assert.deepEqual(
        dashboardFilters({ period: "custom", date_from: "2026-01-01", date_to: "2026-02-01" }),
        { period: "custom", date_from: "2026-01-01", date_to: "2026-02-01" },
    );
});

test("unknown metrics differ from genuinely measured zero", () => {
    assert.equal(formatMetric(null), "—");
    assert.equal(formatMetric(undefined), "—");
    assert.equal(formatMetric(NaN), "—");
    assert.equal(formatMetric(0, "%"), "0.0%");
    assert.equal(formatMetric(12.345), "12.3");
});

test("growth has no fabricated zero-baseline percentage", () => {
    const labels = ["vs entire previous month", "No previous-month baseline"];
    assert.equal(
        growthLabel({ mom_comparable: false, mom_growth_pct: 0 }, ...labels),
        "No previous-month baseline",
    );
    assert.equal(
        growthLabel({ mom_comparable: true, mom_growth_pct: -25 }, ...labels),
        "-25.0% vs entire previous month",
    );
});

// Exercise the production component's request lifecycle with stubbed Odoo
// services. These are service/unit tests, not a substitute for browser UI QA.
const source = await readFile(
    new URL("../src/dashboard/aggregator_dashboard.js", import.meta.url),
    "utf8",
);
const componentCode =
    source.replace(/^import[\s\S]*?;\r?\n/gm, "").replace("export class", "class") +
    "\nBsAggregatorDashboard;";
const ComponentClass = vm.runInNewContext(componentCode, {
    Component: class {},
    registry: { category: () => ({ add() {} }) },
    useService() {},
    useState() {},
    onMounted() {},
    onWillStart() {},
    onWillUnmount() {},
    _t: (message) => message,
    dashboardFilters,
    formatMetric,
    growthLabel,
    Date,
    Number,
    Promise,
    clearInterval,
});
const options = { company_id: 1, companies: [], providers: [], branches: [], today: "2026-09-09" };
const snapshot = (value) => ({ value, permissions: {}, metrics: {}, live: {}, kpis: {} });
function deferred() {
    let resolve, reject;
    const promise = new Promise((yes, no) => {
        resolve = yes;
        reject = no;
    });
    return { promise, resolve, reject };
}
function component(responses) {
    const instance = Object.create(ComponentClass.prototype);
    Object.assign(instance, {
        _requestSequence: 0,
        _destroyed: false,
        _inFlight: null,
        filters: { period: "today" },
        snapshotFilters: { period: "today" },
        state: { loading: false, data: null, options, filters: {}, mode: "overview" },
        notification: { add() {} },
        orm: { call: () => responses.shift().promise },
    });
    return instance;
}

test("older response cannot overwrite a newer filtered snapshot", async () => {
    const first = deferred(),
        second = deferred();
    const instance = component([first, second]);
    const loadingFirst = instance.load();
    instance.filters = { period: "last7" };
    const loadingSecond = instance.load();
    second.resolve(snapshot("new"));
    await loadingSecond;
    first.resolve(snapshot("old"));
    await loadingFirst;
    assert.equal(instance.state.data.value, "new");
    assert.equal(instance.snapshotFilters.period, "last7");
    assert.equal(instance.state.loading, false);
});

test("refresh failure preserves both old data and old drilldown filters", async () => {
    const response = deferred();
    const instance = component([response]);
    instance.state.data = snapshot("old");
    instance.filters = { period: "month" };
    const task = instance.load();
    response.reject(new Error("offline"));
    await task;
    assert.equal(instance.state.data.value, "old");
    assert.equal(instance.snapshotFilters.period, "today");
    assert.equal(instance.state.stale, true);
    assert.equal(instance.state.loading, false);
});

test("unmounted component ignores pending response", async () => {
    const response = deferred();
    const instance = component([response]);
    const task = instance.load();
    instance._destroyed = true;
    response.resolve(snapshot("late"));
    await task;
    assert.equal(instance.state.data, null);
});

test("drilldowns freeze rendered dates across midnight", async () => {
    const response = deferred();
    const instance = component([response]);
    instance.filters = { period: "yesterday" };
    const task = instance.load();
    response.resolve({
        ...snapshot("dated"),
        period: { date_from: "2026-09-08", date_to: "2026-09-08" },
    });
    await task;
    assert.equal(instance.snapshotFilters.period, "custom");
    assert.equal(instance.snapshotFilters.date_from, "2026-09-08");
    assert.equal(instance.snapshotFilters.date_to, "2026-09-08");
    assert.equal(instance.filters.period, "yesterday");
});

test("role modes restrict integration to authorized users", () => {
    const instance = component([]);
    instance.state.data = snapshot("viewer");
    assert.equal(instance.modes.map((mode) => mode.key).join(","), "overview,operations");
    instance.state.data.permissions = { integration: true };
    assert.equal(
        instance.modes.map((mode) => mode.key).join(","),
        "overview,operations,integration",
    );
});
