/** «رحلتي»: the full journey page (roadmap, current level, discipline score, learning, subscription). */
import { Component, markup, onMounted, onWillStart, onWillUnmount, onPatched, useRef, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";
import { loadBundle } from "@web/core/assets";
import { GrowthRing } from "../systray/growth_badge";
import { WelcomeDialog } from "../welcome/welcome_dialog";

const TABS = [
    { key: "level", label: _t("المستوى الحالي"), icon: "fa-flag" },
    { key: "score", label: _t("مؤشر الانضباط"), icon: "fa-tachometer" },
    { key: "learn", label: _t("التعلم"), icon: "fa-graduation-cap" },
    { key: "plan", label: _t("الاشتراك"), icon: "fa-calendar-check-o" },
    { key: "profile", label: _t("ملف مطعمك"), icon: "fa-cog" },
];

export class GrowthJourney extends Component {
    static template = "resta_growth.Journey";
    static components = { GrowthRing };
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.growth = useService("resta_growth");
        this.notification = useService("notification");
        this.action = useService("action");
        this.tabs = TABS;
        const ctx = this.props.action?.context || {};
        this.state = useState({
            data: null, tab: ctx.growth_tab || "level", focusLevel: null, openLesson: null,
            holiday: { name: "", from: "", to: "" }, scenario: "disciplined", busy: false,
        });
        this.chartRef = useRef("chart");
        this.growthState = useState(this.growth.state);
        onWillStart(async () => {
            await loadBundle("web.chartjs_lib");
            await this.load();
        });
        onMounted(() => this.renderChart());
        onPatched(() => this.renderChart());
        onWillUnmount(() => this.chart?.destroy());
        this.lastVersion = this.growth.state.version;
    }

    async load() {
        const data = await this.orm.call("resta.growth.engine", "get_journey_data", []);
        // lesson content is an Html field, sanitized on the server
        data.lessons = data.lessons.map((l) => ({ ...l, content: markup(l.content || "") }));
        this.state.data = data;
        if (this.state.focusLevel === null && this.state.data.level) {
            this.state.focusLevel = this.state.data.level.seq;
        }
    }

    get d() { return this.state.data; }
    get focused() {
        return this.d.levels.find((l) => l.seq === this.state.focusLevel) || this.d.levels[0];
    }
    get isCurrentFocused() { return this.d.level && this.focused.seq === this.d.level.seq; }

    // ------------------------------------------------------------------ score gauge
    get gaugeDash() {
        const pct = Math.max(0, Math.min(1000, this.d.score.value)) / 1000;
        const len = Math.PI * 90;
        return `${len * pct} ${len}`;
    }
    get bandIndex() {
        const s = this.d.score.value;
        const [b1, b2, b3] = this.d.bands;
        return s >= b3 ? 3 : s >= b2 ? 2 : s >= b1 ? 1 : 0;
    }

    renderChart() {
        const el = this.chartRef.el;
        if (!el || !this.d || this.state.tab !== "score") {
            if (this.chart && (!el || this.state.tab !== "score")) {
                this.chart.destroy();
                this.chart = null;
            }
            return;
        }
        if (this.chart && this.chart.canvas === el) {
            return;
        }
        this.chart?.destroy();
        const css = getComputedStyle(document.documentElement);
        const primary = css.getPropertyValue("--rs-primary").trim() || "#FF5A1F";
        const muted = css.getPropertyValue("--rs-text-muted").trim() || "#888";
        const grid = css.getPropertyValue("--rs-border-soft").trim() || "rgba(0,0,0,.08)";
        const points = this.d.history;
        this.chart = new window.Chart(el, {
            type: "line",
            data: {
                labels: points.map((p) => p.date.slice(5)),
                datasets: [{
                    data: points.map((p) => p.score), borderColor: primary, backgroundColor: primary + "22",
                    fill: true, tension: 0.3, pointRadius: 0, borderWidth: 2,
                }],
            },
            options: {
                responsive: true, maintainAspectRatio: false, animation: false,
                plugins: { legend: { display: false }, tooltip: { rtl: true } },
                scales: {
                    x: { reverse: true, ticks: { color: muted, maxTicksLimit: 6 }, grid: { display: false } },
                    y: { min: 0, max: 1000, position: "right", ticks: { color: muted, stepSize: 200 }, grid: { color: grid } },
                },
            },
        });
    }

    // ------------------------------------------------------------------ actions
    setTab(key) { this.state.tab = key; }
    focus(seq) { this.state.focusLevel = seq; this.state.tab = "level"; }
    toggleLesson(key) { this.state.openLesson = this.state.openLesson === key ? null : key; }

    whatToDo(lesson) {
        if (lesson && lesson.tour) {
            this.growth.startTour(lesson.tour);
        } else if (lesson) {
            this.state.tab = "learn";
            this.state.openLesson = lesson.key;
        }
    }

    async claim() {
        await this.growth.claim();
        await this.load();
    }

    async skip() {
        await this.growth.skipLevel();
        await this.load();
    }

    async lessonDone(key) {
        await this.growth.lessonDone(key);
        await this.load();
    }

    async addHoliday() {
        const h = this.state.holiday;
        if (!h.name || !h.from || !h.to || h.to < h.from) {
            this.notification.add(_t("اكتب السبب وتاريخي البداية والنهاية."), { type: "warning" });
            return;
        }
        await this.orm.call("resta.growth.profile", "add_holiday", [h.name, h.from, h.to]);
        this.state.holiday = { name: "", from: "", to: "" };
        this.notification.add(_t("تم إعلان الإجازة. هذه الأيام لن تُحسب عليك."), { type: "success" });
        await this.load();
    }

    openProfileForm() {
        this.action.doAction("resta_growth.action_growth_profile");
    }

    restartWelcome() {
        this.action.doAction({ type: "ir.actions.client", tag: "resta_growth.welcome_launcher", target: "new" });
    }

    // ------------------------------------------------------------------ support tools
    async sim(method, args = []) {
        this.state.busy = true;
        try {
            const res = await this.orm.call("resta.growth.simulator", method, args);
            if (method === "celebration") {
                this.growth.showCelebration(res, { preview: true });
            } else {
                this.notification.add(_t("تم."), { type: "success" });
            }
            await this.growth.load();
            await this.load();
        } finally {
            this.state.busy = false;
        }
    }

    async recompute() {
        this.state.busy = true;
        try {
            await this.orm.call("resta.growth.profile", "action_recompute_now", [[this.d.profile_id]]);
            await this.growth.load();
            await this.load();
            this.notification.add(_t("تم تحديث الحسابات."), { type: "success" });
        } finally {
            this.state.busy = false;
        }
    }
}

registry.category("actions").add("resta_growth.journey", GrowthJourney);

/** Re-open the welcome wizard from the journey page. */
function welcomeLauncher(env) {
    env.services.dialog.add(WelcomeDialog, {});
}
registry.category("actions").add("resta_growth.welcome_launcher", welcomeLauncher);
