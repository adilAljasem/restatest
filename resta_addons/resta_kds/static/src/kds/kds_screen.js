/** Resta kitchen screen: simple like Foodics / Toast.
 *  Tap a dish = prepared, «تم» = the order is ready. Live updates through the bus. */
import { Component, onMounted, onWillStart, onWillUnmount, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { browser } from "@web/core/browser/browser";
import { localization } from "@web/core/l10n/localization";

const STORAGE_KEY = "resta_kds_display_id";

function parseUTC(s) {
    return s ? new Date(s.replace(" ", "T") + "Z").getTime() : 0;
}

function fmtElapsed(ms) {
    const total = Math.max(0, Math.floor(ms / 1000));
    const m = Math.floor(total / 60);
    const sec = total % 60;
    return `${m}:${String(sec).padStart(2, "0")}`;
}

export class KdsScreen extends Component {
    static template = "resta_kds.Screen";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.bus = useService("bus_service");
        this.notification = useService("notification");
        this.action = useService("action");
        this.state = useState({
            displays: null, data: null, now: Date.now(), offset: 0, view: "tickets",
            stageFilter: false, catFilter: false, showRecent: false, soundOn: true, audioLocked: true,
            busy: {},
        });
        this.dir = localization.direction === "rtl" || document.body.classList.contains("o_rtl") ? "rtl" : "ltr";
        this.displayId = this.props.action?.params?.display_id || this.readStoredDisplay();
        this.channel = null;
        this.reloadTimer = null;

        onWillStart(async () => {
            if (!this.displayId) {
                this.state.displays = await this.orm.call("resta.kds.display", "kds_list_displays", []);
                if (this.state.displays.length === 1) {
                    this.displayId = this.state.displays[0].id;
                }
            }
            if (this.displayId) {
                await this.load();
            }
        });
        onMounted(() => {
            this.clock = setInterval(() => (this.state.now = Date.now()), 1000);
            this.poll = setInterval(() => this.displayId && this.load(), 30000);
            this.keepAwake();
        });
        onWillUnmount(() => {
            clearInterval(this.clock);
            clearInterval(this.poll);
            clearTimeout(this.reloadTimer);
            if (this.channel) {
                this.bus.deleteChannel(this.channel);
            }
            this.wakeLock?.release?.();
        });
        this.bus.subscribe("RESTA_KDS", (payload) => {
            if (!this.displayId || payload.display_id !== this.displayId) {
                return;
            }
            if (payload.kind === "new") {
                this.beep();
            }
            clearTimeout(this.reloadTimer);
            this.reloadTimer = setTimeout(() => this.load(), 250);
        });
    }

    readStoredDisplay() {
        try {
            return parseInt(browser.localStorage.getItem(STORAGE_KEY)) || false;
        } catch {
            return false;
        }
    }

    async keepAwake() {
        try {
            this.wakeLock = await navigator.wakeLock?.request("screen");
        } catch {
            // not supported: the tablet's own settings apply
        }
    }

    async choose(displayId) {
        this.displayId = displayId;
        try {
            browser.localStorage.setItem(STORAGE_KEY, String(displayId));
        } catch {
            // private mode
        }
        await this.load();
    }

    async load() {
        const data = await this.orm.call("resta.kds.display", "kds_load", [[this.displayId]]);
        this.state.offset = parseUTC(data.server_time) - Date.now();
        if (data.display.channel !== this.channel) {
            if (this.channel) {
                this.bus.deleteChannel(this.channel);
            }
            this.channel = data.display.channel;
            this.bus.addChannel(this.channel);
        }
        if (this.state.data === null) {
            this.state.soundOn = data.display.sound;
        }
        this.state.data = data;
    }

    // ------------------------------------------------------------------ derived
    get display() {
        return this.state.data.display;
    }
    get stages() {
        return this.display.stages;
    }
    get multiStage() {
        return this.stages.length > 1;
    }
    get stageById() {
        return Object.fromEntries(this.stages.map((s) => [s.id, s]));
    }
    lineVisible(line) {
        const cat = this.state.catFilter;
        return !cat || line.category_ids.includes(cat);
    }
    get tickets() {
        let list = this.state.data.tickets;
        if (this.state.stageFilter) {
            list = list.filter((t) => t.stage_id === this.state.stageFilter);
        }
        if (this.state.catFilter) {
            list = list.filter((t) => t.lines.some((l) => this.lineVisible(l)));
        }
        return list;
    }
    countForStage(stageId) {
        return this.state.data.tickets.filter((t) => t.stage_id === stageId).length;
    }
    groupedLines(ticket) {
        // combo items under their combo header
        const lines = ticket.lines.filter((l) => this.lineVisible(l) || ticket.lines.some(
            (c) => c.parent_uuid === l.uuid && this.lineVisible(c)));
        const children = {};
        for (const l of lines) {
            if (l.parent_uuid) {
                (children[l.parent_uuid] ||= []).push(l);
            }
        }
        return lines.filter((l) => !l.parent_uuid || !lines.some((p) => p.uuid === l.parent_uuid))
            .map((l) => ({ line: l, children: children[l.uuid] || [] }));
    }
    elapsed(ticket) {
        return this.state.now + this.state.offset - parseUTC(ticket.created);
    }
    elapsedText(ticket) {
        return fmtElapsed(this.elapsed(ticket));
    }
    timerClass(ticket) {
        if (ticket.cancelled) {
            return "is-cancelled";
        }
        const min = this.elapsed(ticket) / 60000;
        if (min >= this.display.late) {
            return "is-late";
        }
        if (min >= this.display.warn) {
            return "is-warn";
        }
        return "is-ok";
    }
    nextLabel(ticket) {
        const ordered = this.stages;
        const idx = ordered.findIndex((s) => s.id === ticket.stage_id);
        const next = ordered[idx + 1];
        return next ? next.name : _t("تم");
    }
    qty(n) {
        return Number.isInteger(n) ? String(n) : n.toFixed(2).replace(/\.?0+$/, "");
    }
    get allDay() {
        const totals = {};
        for (const t of this.state.data.tickets) {
            if (t.cancelled) {
                continue;
            }
            for (const l of t.lines) {
                const left = l.qty - l.cancelled;
                if (l.done || left <= 0 || !this.lineVisible(l)) {
                    continue;
                }
                const key = `${l.name}|${l.attributes}`;
                totals[key] ||= { name: l.name, name2: l.name2, attributes: l.attributes, qty: 0 };
                totals[key].qty += left;
            }
        }
        return Object.values(totals).sort((a, b) => b.qty - a.qty);
    }
    get avgMinutes() {
        const done = this.state.data.recent.filter((t) => t.done_date);
        if (!done.length) {
            return false;
        }
        const avg = done.reduce((s, t) => s + (parseUTC(t.done_date) - parseUTC(t.created)), 0) / done.length;
        return fmtElapsed(avg);
    }
    typeClass(ticket) {
        const s = ticket.source;
        if (s === "kiosk" || s === "mobile") {
            return "is-self";
        }
        return ticket.table ? "is-dinein" : "is-takeaway";
    }

    // ------------------------------------------------------------------ actions
    async run(key, fn) {
        if (this.state.busy[key]) {
            return;
        }
        this.state.busy[key] = true;
        try {
            await fn();
            await this.load();
        } finally {
            delete this.state.busy[key];
        }
    }
    bump(ticket) {
        this.unlockAudio();
        return this.run("t" + ticket.id, () => this.orm.call("resta.kds.ticket", "kds_next", [[ticket.id]]));
    }
    toggleLine(line) {
        this.unlockAudio();
        return this.run("l" + line.id, () => this.orm.call("resta.kds.ticket.line", "kds_toggle", [[line.id]]));
    }
    recall(ticket) {
        return this.run("r" + ticket.id, () => this.orm.call("resta.kds.ticket", "kds_recall", [[ticket.id]]));
    }
    setView(view) {
        this.state.view = view;
    }
    setStage(id) {
        this.state.stageFilter = this.state.stageFilter === id ? false : id;
    }
    setCat(id) {
        this.state.catFilter = this.state.catFilter === id ? false : id;
    }
    toggleRecent() {
        this.state.showRecent = !this.state.showRecent;
    }
    toggleSound() {
        this.state.soundOn = !this.state.soundOn;
        this.unlockAudio();
    }
    fullscreen() {
        const el = document.documentElement;
        if (document.fullscreenElement) {
            document.exitFullscreen?.();
        } else {
            el.requestFullscreen?.();
        }
    }
    changeDisplay() {
        try {
            browser.localStorage.removeItem(STORAGE_KEY);
        } catch {
            // ignore
        }
        this.displayId = false;
        this.state.data = null;
        this.orm.call("resta.kds.display", "kds_list_displays", []).then((d) => (this.state.displays = d));
    }
    exit() {
        this.action.doAction("point_of_sale.action_pos_config_kanban");
    }

    // ------------------------------------------------------------------ sound
    unlockAudio() {
        if (!this.audio) {
            try {
                this.audio = new (window.AudioContext || window.webkitAudioContext)();
            } catch {
                return;
            }
        }
        this.audio.resume?.();
        this.state.audioLocked = false;
    }
    beep() {
        if (!this.state.soundOn || !this.audio) {
            return;
        }
        const ctx = this.audio;
        [988, 1319].forEach((f, i) => {
            const o = ctx.createOscillator();
            const g = ctx.createGain();
            o.frequency.value = f;
            o.connect(g);
            g.connect(ctx.destination);
            const t = ctx.currentTime + i * 0.18;
            g.gain.setValueAtTime(0.0001, t);
            g.gain.exponentialRampToValueAtTime(0.35, t + 0.02);
            g.gain.exponentialRampToValueAtTime(0.0001, t + 0.3);
            o.start(t);
            o.stop(t + 0.32);
        });
    }
}

registry.category("actions").add("resta_kds.screen", KdsScreen);
