// Real Odoo OWL template/component mounted in a DOM emulator. This verifies
// rendering and events, not browser layout, contrast, or screenshots.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import test from "node:test";
import { dashboardFilters, formatMetric, growthLabel } from "../src/dashboard/dashboard_utils.js";

const require = createRequire(
    new URL("../../../scripts/dashboard_ui_tests/package.json", import.meta.url),
);
const { JSDOM } = require("jsdom");
const owlSource = await readFile(
    new URL("../../../../odoo/addons/web/static/lib/owl/owl.js", import.meta.url),
    "utf8",
);
const componentSource = await readFile(
    new URL("../src/dashboard/aggregator_dashboard.js", import.meta.url),
    "utf8",
);
const templates = await readFile(
    new URL("../src/dashboard/aggregator_dashboard.xml", import.meta.url),
    "utf8",
);
const states = [
    "received",
    "accepted",
    "preparing",
    "ready",
    "picked_up",
    "delivered",
    "rejected",
    "cancelled",
];
const baseData = () => ({
    permissions: { integration: false, configure: false },
    period: { date_from: "2026-09-09", date_to: "2026-09-09" },
    company_scope: [{ id: 1, name: "Demo company" }],
    currency: { symbol: "SAR", position: "before", decimal_places: 2 },
    metrics: {
        orders: 0,
        gmv: 0,
        net: 0,
        commission: 0,
        acceptance_minutes: null,
        preparation_minutes: null,
        acceptance_samples: 0,
        preparation_samples: 0,
        rejection_pct: null,
        cancellation_pct: null,
        average_order_value: null,
    },
    live: {
        connections: 0,
        configured: 0,
        paused: 0,
        connection_errors: 0,
        awaiting_acceptance: 0,
        active_orders: 0,
        platforms: [],
    },
    kpis: {
        late_orders: 0,
        mtd_orders: 0,
        mtd_gmv: 0,
        ytd_gmv: 0,
        mom_comparable: false,
    },
    queue_health: {},
    trend: [],
    platforms: [],
    branches: [],
    business_day: { start_hour: 6, label: "06:00", timezone: "Asia/Riyadh", today: "2026-09-09" },
    recent_orders: [],
    state_breakdown: states.map((state) => ({ state, count: 0 })),
});
const options = {
    company_id: 1,
    today: "2026-09-09",
    companies: [{ id: 1, name: "Demo company" }],
    providers: [{ id: 3, name: "Example provider" }],
    channels: [
        { key: "HUNGERSTATION", name: "HungerStation", count: 2 },
        { key: "JAHEZ", name: "Jahez", count: 1 },
    ],
    branches: [{ id: 4, company_id: 1, name: "Demo branch" }],
};
function populatedData() {
    const data = baseData();
    data.permissions = { integration: true, configure: true };
    Object.assign(data.metrics, {
        orders: 12,
        gmv: 1000,
        net: 800,
        commission: 200,
        average_order_value: 100,
        acceptance_minutes: 3.5,
        preparation_minutes: 12.2,
        acceptance_samples: 8,
        preparation_samples: 6,
        rejection_pct: 8.3,
        cancellation_pct: 8.3,
    });
    Object.assign(data.live, {
        connections: 3,
        configured: 2,
        connection_errors: 1,
        paused: 1,
        active_orders: 8,
        awaiting_acceptance: 2,
        platforms: [
            { id: "HUNGERSTATION", key: "HUNGERSTATION", name: "HungerStation", color: "#f2c200", provider_id: 0, count: 3 },
            { id: "JAHEZ", key: "JAHEZ", name: "جاهز", color: "#d62a2a", provider_id: 0, count: 3 },
            { id: "TRYORDER", key: "TRYORDER", name: "TryOrder", color: "", provider_id: 7, count: 2 },
        ],
    });
    Object.assign(data.kpis, {
        late_orders: 3,
        mtd_orders: 48,
        mtd_gmv: 5000,
        ytd_gmv: 40000,
        mom_comparable: true,
        mom_growth_pct: -12.5,
    });
    data.trend = Array.from({ length: 7 }, (_, i) => ({
        date: `2026-09-0${i + 3}`,
        label: `Day ${i + 1}`,
        amount: i * 100,
    }));
    data.platforms = data.live.platforms.map((platform) => ({
        key: platform.key,
        provider_id: platform.provider_id,
        color: platform.color,
        name: platform.name,
        via: platform.provider_id ? [] : ["Blend"],
        orders: 1,
        gmv: 100,
        share_pct: 33.3,
        average_order_value: 100,
        commission: 20,
        net: 80,
    }));
    data.branches = [
        { pos_config_id: 4, name: "Demo branch", orders: 2, gmv: 200, share_pct: 66.7, average_order_value: 100, commission: 40, net: 160 },
        { pos_config_id: 0, name: "Unassigned branch", orders: 1, gmv: 100, share_pct: 33.3, average_order_value: 100, commission: 20, net: 80 },
    ];
    data.recent_orders = Array.from({ length: 12 }, (_, i) => ({
        id: i + 1,
        name: `ORDER-${i + 1}`,
        provider_id: [3, 3, 7][i % 3],
        provider: ["Blend", "Blend", "TryOrder"][i % 3],
        channel: ["HUNGERSTATION", "JAHEZ", "TRYORDER"][i % 3],
        channel_name: ["HungerStation", "جاهز", "TryOrder"][i % 3],
        channel_color: ["#f2c200", "#d62a2a", ""][i % 3],
        channel_is_provider: i % 3 === 2,
        customer: `عميل ${i + 1}`,
        state: states[i % states.length],
        is_late: i < 3,
        amount: 100,
        received_at: "2026-09-09 10:00:00",
    }));
    data.state_breakdown = states.map((state, i) => ({
        state,
        count: i < 4 ? 2 : 1,
    }));
    data.queue_health = { webhook_retry: 2, outbox_retry: 1 };
    return data;
}
const settle = () => new Promise((resolve) => setTimeout(resolve, 45));
async function mounted(data) {
    const dom = new JSDOM("<!doctype html><div id='dashboard'></div>", {
        runScripts: "outside-only",
        pretendToBeVisual: true,
    });
    const win = dom.window;
    win.eval(owlSource);
    const calls = [],
        actions = [];
    const services = {
        orm: {
            call: async (model, method, args, kwargs) => {
                calls.push({ model, method, args, kwargs });
                if (method === "get_dashboard_options") {
                    return options;
                }
                if (method === "get_dashboard_action") {
                    return {
                        type: "ir.actions.act_window",
                        res_model: "bs.delivery.order",
                    };
                }
                return structuredClone(data);
            },
        },
        action: { doAction: async (action) => actions.push(action) },
        notification: { add: (message) => assert.fail(message) },
    };
    Object.assign(win, {
        ...win.owl,
        useService: (name) => services[name],
        registry: { category: () => ({ add() {} }) },
        _t: (text) => text,
        dashboardFilters,
        formatMetric,
        growthLabel,
        deserializeDateTime: (value) => value,
        formatDateTime: (value) => value,
    });
    win.eval(
        componentSource.replace(/^import[\s\S]*?;\r?\n/gm, "").replace("export class", "class") +
            "\nwindow.DashboardComponent = BsAggregatorDashboard;",
    );
    const app = new win.owl.App(win.DashboardComponent, {
        templates,
        dev: true,
    });
    const component = await app.mount(win.document.querySelector("#dashboard"));
    return {
        dom,
        component,
        calls,
        actions,
        close: () => {
            app.destroy();
            win.close();
        },
    };
}

test("empty viewer dashboard compiles and renders neutral setup state", async () => {
    const page = await mounted(baseData());
    try {
        const text = page.dom.window.document.body.textContent;
        assert.match(text, /No active platform connections/);
        assert.doesNotMatch(text, /No previous-month baseline/);
        assert.doesNotMatch(text, /No orders match these filters/);
        assert.equal(page.dom.window.document.querySelectorAll("table").length, 0);
        assert.equal(page.dom.window.document.querySelectorAll(".o_bs_chart_empty").length, 0);
        assert.equal(page.dom.window.document.querySelectorAll(".o_bs_empty_setup").length, 1);
        assert.equal(page.dom.window.document.querySelectorAll(".o_bs_attention").length, 0);
        assert.doesNotMatch(text, /API connectivity not verified/); // admin-only queue section
        assert.equal(
            page.dom.window.document.querySelectorAll(".o_bs_dashboard_modes button").length,
            2,
        );
    } finally {
        page.close();
    }
});

test("populated dashboard renders platform logos and integration", async () => {
    const data = baseData();
    data.permissions = { integration: true, configure: true };
    data.metrics.orders = 1;
    data.live.connections = 1;
    data.live.platforms = [
        { id: "EXAMPLE", key: "EXAMPLE", name: "Example provider", provider_id: 3, count: 1 },
    ];
    data.platforms = [
        {
            key: "EXAMPLE",
            provider_id: 3,
            name: "Example provider",
            via: [],
            orders: 1,
            gmv: 100,
            share_pct: 100,
            average_order_value: 100,
            commission: 20,
            net: 80,
        },
    ];
    data.recent_orders = [
        {
            id: 8,
            name: "ORDER-8",
            provider: "Example provider",
            provider_id: 3,
            channel: "EXAMPLE",
            channel_name: "Example provider",
            channel_is_provider: true,
            customer: "عميل تجريبي",
            state: "delivered",
            amount: 100,
            received_at: "2026-09-09 10:00:00",
        },
    ];
    const page = await mounted(data);
    try {
        const document = page.dom.window.document;
        assert.doesNotMatch(document.body.textContent, /API connectivity not verified/);
        assert.doesNotMatch(document.body.textContent, /Service performance/);
        assert.match(document.body.textContent, /Commercial overview/);
        assert.match(document.body.textContent, /عميل تجريبي/);
        // Overview, Operations, Integration.
        assert.equal(document.querySelectorAll(".o_bs_dashboard_modes button").length, 3);
        assert.equal(
            document.querySelectorAll("img[src='/web/image/bs.delivery.provider/3/image_128']")
                .length,
            2,
        );
        document.querySelector(".o_bs_kpi_grid button").click();
        await settle();
        assert.equal(page.actions.length, 1);
        assert.equal(page.calls.at(-1).args[0], "orders");

        document.querySelectorAll(".o_bs_dashboard_modes button")[2].click();
        await settle();
        assert.match(document.body.textContent, /API connectivity not verified/);
        assert.doesNotMatch(document.body.textContent, /Commercial overview/);
        assert.doesNotMatch(document.body.textContent, /Recent orders/);
        assert.equal(document.querySelectorAll(".o_bs_kpi_grid").length, 0);
    } finally {
        page.close();
    }
});

test("filter form sends selected branch and period and section controls render", async () => {
    const page = await mounted(baseData());
    try {
        const win = page.dom.window;
        const selects = win.document.querySelectorAll(".o_bs_filters select");
        for (const [index, value] of [
            [0, "last7"],
            [2, "JAHEZ"],
            [3, "4"],
        ]) {
            selects[index].value = value;
            selects[index].dispatchEvent(new win.Event("change", { bubbles: true }));
        }
        win.document
            .querySelector(".o_bs_filters")
            .dispatchEvent(new win.Event("submit", { bubbles: true, cancelable: true }));
        await settle();
        const request = page.calls.filter((call) => call.method === "get_dashboard_data").at(-1);
        assert.equal(request.args[0].period, "last7");
        assert.equal(request.args[0].channel, "JAHEZ");
        assert.equal(request.args[0].pos_config_id, 4);
        assert.equal(request.args[0].environment, undefined);
        assert.equal(win.document.querySelectorAll(".o_bs_filters select").length, 4);
        assert.doesNotMatch(win.document.querySelector(".o_bs_filters").textContent, /Environment/);
        win.document.querySelectorAll(".o_bs_dashboard_modes button")[1].click();
        await settle();
        assert.equal(page.component.state.mode, "operations");
        assert.doesNotMatch(win.document.body.textContent, /Commercial overview/);
    } finally {
        page.close();
    }
});

test("overview is a five-order summary; operations owns states and timing details", async () => {
    const page = await mounted(populatedData());
    try {
        const doc = page.dom.window.document;
        const rows = () => doc.querySelectorAll('[data-section="recent-orders"] tbody tr');
        assert.equal(rows().length, 5);
        assert.equal(doc.querySelectorAll(".o_bs_order_late").length, 3);
        assert.equal(doc.querySelectorAll(".o_bs_late_badge").length, 3);
        assert.equal(doc.querySelectorAll(".o_bs_state_badge[data-state]").length, 5);
        assert.equal(
            doc.querySelectorAll(
                '[data-section="service"], [data-section="states"], [data-section="integration"]',
            ).length,
            0,
        );
        // Direct platforms show their logo, middleware-forwarded ones a platform mark.
        assert.equal(doc.querySelectorAll('[data-section="recent-orders"] img').length, 1);
        assert.equal(
            doc.querySelectorAll('[data-section="recent-orders"] .o_bs_channel_mark').length,
            4,
        );
        assert.match(doc.querySelector('[data-section="recent-orders"]').textContent, /via Blend/);
        assert.equal(doc.querySelectorAll('[data-section="trend"] .o_bs_trend_day').length, 7);

        doc.querySelectorAll(".o_bs_dashboard_modes button")[1].click();
        await settle();
        assert.equal(rows().length, 12);
        assert.equal(
            doc.querySelectorAll('[data-section="service"], [data-section="states"]').length,
            2,
        );
        assert.equal(doc.querySelectorAll(".o_bs_state_row[data-state]").length, 8);
        assert.equal(
            doc.querySelectorAll(
                '[data-section="trend"], [data-section="commercial"], [data-section="platform-performance"]',
            ).length,
            0,
        );

        doc.querySelector('.o_bs_state_row[data-state="ready"]').click();
        await settle();
        assert.equal(page.calls.at(-1).args[0], "state");
        assert.equal(page.calls.at(-1).kwargs.state, "ready");
        rows()[0].querySelector("button").click();
        await settle();
        assert.equal(page.actions.at(-1).res_id, 1);
    } finally {
        page.close();
    }
});

test("configured empty period is distinct from absent connections and keeps older live work", async () => {
    const data = baseData();
    data.live.connections = 1;
    data.live.active_orders = 1;
    data.live.platforms = [{ id: "JAHEZ", key: "JAHEZ", name: "Jahez", provider_id: 0, count: 1 }];
    data.kpis.late_orders = 1;
    const page = await mounted(data);
    try {
        const doc = page.dom.window.document;
        assert.equal(doc.querySelectorAll(".o_bs_empty_setup").length, 0);
        assert.equal(doc.querySelectorAll(".o_bs_period_empty").length, 1);
        assert.equal(doc.querySelectorAll(".o_bs_attention").length, 1);
        assert.equal(doc.querySelectorAll("table, .o_bs_chart_empty").length, 0);
        doc.querySelectorAll(".o_bs_dashboard_modes button")[1].click();
        await settle();
        assert.equal(doc.querySelectorAll('[data-section="active-platforms"] button').length, 1);
        assert.equal(doc.querySelectorAll('[data-section="states"]').length, 0);
    } finally {
        page.close();
    }
});

test("historical sales stay visible with no currently active connection", async () => {
    const data = populatedData();
    data.live.connections = 0;
    const page = await mounted(data);
    try {
        const doc = page.dom.window.document;
        assert.equal(doc.querySelectorAll(".o_bs_empty_setup").length, 1);
        assert.equal(doc.querySelectorAll('[data-section="overview-summary"]').length, 1);
        assert.equal(doc.querySelectorAll('[data-section="recent-orders"] tbody tr').length, 5);
    } finally {
        page.close();
    }
});

test("commercial history remains visible when the selected period is empty", async () => {
    const data = baseData();
    data.kpis.ytd_gmv = 900;
    const page = await mounted(data);
    try {
        const doc = page.dom.window.document;
        assert.match(doc.body.textContent, /SAR 900.00/);
        assert.equal(doc.querySelectorAll('[data-section="commercial"]').length, 1);
        assert.equal(doc.querySelectorAll("table").length, 0);
    } finally {
        page.close();
    }
});

test("queue alert opens integration and read-only help expands on click", async () => {
    const page = await mounted(populatedData());
    try {
        const doc = page.dom.window.document;
        const help = doc.querySelector(".o_bs_help");
        assert.equal(help.open, false);
        assert.ok(help.querySelector("summary").getAttribute("aria-label"));
        help.querySelector("summary").click();
        assert.equal(help.open, true);
        help.querySelector("summary").click();
        assert.equal(help.open, false);
        [...doc.querySelectorAll(".o_bs_attention_actions button")]
            .find((button) => button.textContent.includes("Queue issues"))
            .click();
        await settle();
        assert.equal(page.component.state.mode, "integration");
        assert.equal(doc.querySelectorAll('[data-section="integration"]').length, 1);
        assert.equal(
            doc.querySelectorAll(
                '[data-section="recent-orders"], [data-section="service"], [data-section="commercial"]',
            ).length,
            0,
        );
    } finally {
        page.close();
    }
});

test("zero current sales keep the real previous-month comparison visible", async () => {
    const data = baseData();
    data.kpis.mom_comparable = true;
    data.kpis.mom_growth_pct = -100;
    const page = await mounted(data);
    try {
        assert.match(page.dom.window.document.body.textContent, /-100.0% vs entire previous month/);
        assert.equal(page.dom.window.document.querySelectorAll("table").length, 0);
    } finally {
        page.close();
    }
});

test("provider and customer labels are escaped, not interpreted as markup", async () => {
    const data = populatedData();
    data.recent_orders[0].customer = '<img src=x onerror="alert(1)">';
    data.recent_orders[0].provider = "<script>alert(1)</script>";
    data.recent_orders[0].channel_name = "<b>channel</b>";
    const page = await mounted(data);
    try {
        const doc = page.dom.window.document;
        assert.equal(doc.querySelectorAll("script, img[onerror]").length, 0);
        assert.match(doc.querySelector('[data-section="recent-orders"]').textContent, /<script>/);
    } finally {
        page.close();
    }
});

test("platform sales show ordering platforms, branch sales sit at the bottom", async () => {
    const page = await mounted(populatedData());
    try {
        const doc = page.dom.window.document;
        const platformText = doc.querySelector('[data-section="platform-performance"]').textContent;
        assert.match(platformText, /HungerStation/);
        assert.match(platformText, /via Blend/);
        assert.match(platformText, /TryOrder/);
        assert.equal(doc.querySelectorAll('[data-section="platform-performance"] tbody tr').length, 3);
        assert.equal(doc.querySelectorAll('[data-section="platform-performance"] .o_bs_channel_mark').length, 2);
        const sections = [...doc.querySelectorAll("[data-section]")].map((node) => node.dataset.section);
        assert.equal(sections.at(-1), "branch-sales");
        assert.equal(doc.querySelectorAll('[data-section="branch-sales"] tbody tr').length, 2);
        assert.match(doc.querySelector('[data-section="branch-sales"] tfoot').textContent, /300/);
        assert.match(doc.querySelector(".o_bs_scope_line").textContent, /Business day starts 06:00/);
        assert.doesNotMatch(doc.querySelector(".o_bs_scope_line").textContent, /environments/);

        doc.querySelector('[data-section="platform-performance"] tbody button').click();
        await settle();
        assert.equal(page.calls.at(-1).args[0], "gmv");
        assert.equal(page.calls.at(-1).kwargs.channel, "HUNGERSTATION");

        doc.querySelector('[data-section="branch-sales"] tbody button').click();
        await settle();
        assert.equal(page.calls.at(-1).kwargs.pos_config_id, 4);
        // The unassigned bucket is informational, not a drilldown.
        assert.equal(doc.querySelectorAll('[data-section="branch-sales"] tbody button').length, 1);
    } finally {
        page.close();
    }
});
