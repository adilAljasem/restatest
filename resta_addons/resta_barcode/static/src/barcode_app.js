/** Resta barcode app: receive, transfer and count stock by scanning (scanner gun, phone camera or typing). */
import { Component, onMounted, onWillStart, onWillUnmount, useRef, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { localization } from "@web/core/l10n/localization";

export class RestaBarcodeApp extends Component {
    static template = "resta_barcode.App";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.dir = localization.direction === "rtl" || document.body.classList.contains("o_rtl") ? "rtl" : "ltr";
        this.input = useRef("scan");
        this.video = useRef("video");
        this.state = useState({
            screen: "home", home: null, list: [], listCode: null, picking: null, count: null, locations: [],
            toast: null, lotFor: null, lotValue: "", camera: false, busy: false, editMove: null, editQty: 0,
            confirmValidate: false,
        });
        this.buffer = "";
        this.lastKey = 0;
        this.onKey = this.onKey.bind(this);
        onWillStart(() => this.loadHome());
        onMounted(() => window.addEventListener("keydown", this.onKey, true));
        onWillUnmount(() => {
            window.removeEventListener("keydown", this.onKey, true);
            this.stopCamera();
        });
    }

    // ------------------------------------------------------------------ feedback
    toast(message, kind = "ok") {
        this.state.toast = { message, kind };
        clearTimeout(this.toastTimer);
        this.toastTimer = setTimeout(() => (this.state.toast = null), 2600);
        try {
            navigator.vibrate?.(kind === "ok" ? 40 : [80, 60, 80]);
            const ctx = (this.audio ||= new (window.AudioContext || window.webkitAudioContext)());
            const o = ctx.createOscillator();
            const g = ctx.createGain();
            o.frequency.value = kind === "ok" ? 1320 : 220;
            o.connect(g);
            g.connect(ctx.destination);
            g.gain.setValueAtTime(0.2, ctx.currentTime);
            g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.18);
            o.start();
            o.stop(ctx.currentTime + 0.2);
        } catch {
            // no sound on this device
        }
    }

    // ------------------------------------------------------------------ scanning input
    onKey(ev) {
        // a hardware scanner types fast and ends with Enter; ignore normal typing in inputs
        const target = ev.target;
        if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName) && target !== this.input.el) {
            return;
        }
        if (target === this.input.el) {
            return;
        }
        const now = Date.now();
        if (now - this.lastKey > 80) {
            this.buffer = "";
        }
        this.lastKey = now;
        if (ev.key === "Enter") {
            if (this.buffer.length >= 3) {
                ev.preventDefault();
                this.handleCode(this.buffer);
            }
            this.buffer = "";
        } else if (ev.key.length === 1) {
            this.buffer += ev.key;
        }
    }
    onManual(ev) {
        if (ev.key === "Enter" && ev.target.value.trim()) {
            const code = ev.target.value.trim();
            ev.target.value = "";
            this.handleCode(code);
        }
    }

    async handleCode(code) {
        if (this.state.busy) {
            return;
        }
        this.state.busy = true;
        try {
            if (this.state.screen === "picking") {
                await this.scanPicking(code);
            } else if (this.state.screen === "count") {
                await this.scanCount(code);
            } else {
                const pickingId = await this.orm.call("resta.barcode", "open_by_barcode", [code]);
                if (pickingId) {
                    await this.openPicking(pickingId);
                } else {
                    const locationId = await this.orm.call("resta.barcode", "find_location", [code]);
                    if (locationId) {
                        await this.openCount(locationId);
                    } else {
                        this.toast(`لم يُعرف: ${code}`, "error");
                    }
                }
            }
        } finally {
            this.state.busy = false;
        }
    }

    // ------------------------------------------------------------------ camera (phones / tablets)
    get cameraSupported() {
        return "BarcodeDetector" in window && !!navigator.mediaDevices?.getUserMedia;
    }
    async startCamera() {
        if (!this.cameraSupported) {
            this.toast("الكاميرا غير مدعومة في هذا المتصفح. استخدم Chrome أو قارئ الباركود.", "error");
            return;
        }
        try {
            this.stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } });
        } catch {
            this.toast("لم يُسمح باستخدام الكاميرا.", "error");
            return;
        }
        this.state.camera = true;
        await Promise.resolve();
        const video = this.video.el;
        video.srcObject = this.stream;
        await video.play();
        const detector = new window.BarcodeDetector();
        let last = "";
        let lastAt = 0;
        this.cameraTimer = setInterval(async () => {
            try {
                const codes = await detector.detect(video);
                const value = codes[0]?.rawValue;
                if (value && (value !== last || Date.now() - lastAt > 1500)) {
                    last = value;
                    lastAt = Date.now();
                    this.handleCode(value);
                }
            } catch {
                // frame not ready
            }
        }, 300);
    }
    stopCamera() {
        clearInterval(this.cameraTimer);
        this.stream?.getTracks().forEach((t) => t.stop());
        this.stream = null;
        this.state.camera = false;
    }

    // ------------------------------------------------------------------ navigation
    async loadHome() {
        this.state.home = await this.orm.call("resta.barcode", "get_home", []);
    }
    async goHome() {
        this.state.screen = "home";
        this.state.picking = null;
        this.state.count = null;
        await this.loadHome();
    }
    async openList(code) {
        this.state.listCode = code;
        this.state.list = await this.orm.call("resta.barcode", "get_pickings", [code, ""]);
        this.state.screen = "list";
    }
    async searchList(ev) {
        this.state.list = await this.orm.call("resta.barcode", "get_pickings", [this.state.listCode, ev.target.value]);
    }
    async openPicking(id) {
        this.state.picking = await this.orm.call("resta.barcode", "get_picking", [id]);
        this.state.screen = "picking";
    }
    back() {
        if (this.state.screen === "picking" && this.state.listCode) {
            return this.openList(this.state.listCode);
        }
        return this.goHome();
    }

    // ------------------------------------------------------------------ transfers
    async scanPicking(code, lotName = null) {
        const res = await this.orm.call("resta.barcode", "scan_picking", [this.state.picking.id, code, lotName]);
        this.state.picking = res.picking;
        if (res.need_lot) {
            this.state.lotFor = { code, product: res.product, mode: "picking" };
            this.state.lotValue = "";
            this.toast(res.message, "warn");
            return;
        }
        this.toast(res.message, res.ok ? (res.warning ? "warn" : "ok") : "error");
    }
    async submitLot() {
        const lot = this.state.lotValue.trim();
        const pending = this.state.lotFor;
        if (!lot || !pending) {
            return;
        }
        this.state.lotFor = null;
        if (pending.mode === "picking") {
            await this.scanPicking(pending.code, lot);
        } else {
            await this.scanCount(pending.code, lot);
        }
    }
    editMove(move) {
        this.state.editMove = move.id;
        this.state.editQty = move.done || move.demand;
    }
    async saveQty() {
        this.state.picking = await this.orm.call("resta.barcode", "set_quantity", [this.state.editMove, this.state.editQty]);
        this.state.editMove = null;
    }
    async step(move, delta) {
        const qty = Math.max(0, (move.done || 0) + delta);
        this.state.picking = await this.orm.call("resta.barcode", "set_quantity", [move.id, qty]);
    }
    async fillAll() {
        this.state.picking = await this.orm.call("resta.barcode", "fill_all", [this.state.picking.id]);
        this.toast("تم تعبئة الكميات المتوقعة");
    }
    async validate(backorder) {
        this.state.confirmValidate = false;
        const res = await this.orm.call("resta.barcode", "validate", [this.state.picking.id, backorder]);
        this.toast(res.message + (res.backorder ? ` · المتبقي في ${res.backorder}` : ""), res.ok ? "ok" : "error");
        if (res.ok) {
            await this.back();
        }
    }
    askValidate() {
        if (this.state.picking.all_done) {
            return this.validate(true);
        }
        this.state.confirmValidate = true;
    }

    // ------------------------------------------------------------------ inventory count
    async openCountPicker() {
        this.state.locations = await this.orm.call("resta.barcode", "get_locations", []);
        this.state.screen = "locations";
    }
    async openCount(locationId) {
        this.state.count = await this.orm.call("resta.barcode", "get_count", [locationId]);
        this.state.screen = "count";
    }
    async scanCount(code, lotName = null, qty = null) {
        const res = await this.orm.call("resta.barcode", "scan_count", [this.state.count.location.id, code, lotName, qty]);
        this.state.count = { location: res.location, lines: res.lines };
        if (res.need_lot) {
            this.state.lotFor = { code, product: res.product, mode: "count" };
            this.state.lotValue = "";
            this.toast(res.message, "warn");
            return;
        }
        this.toast(res.message, res.ok ? "ok" : "error");
    }
    async setCount(line, ev) {
        const value = parseFloat(ev.target.value);
        if (!isNaN(value)) {
            this.state.count = await this.orm.call("resta.barcode", "set_count", [line.id, value]);
        }
    }
    async applyCount() {
        const res = await this.orm.call("resta.barcode", "apply_count", [this.state.count.location.id]);
        this.toast(res.message);
        await this.goHome();
    }
    fmt(n) {
        return Number.isInteger(n) ? String(n) : Number(n).toFixed(2).replace(/\.?0+$/, "");
    }
}

registry.category("actions").add("resta_barcode.app", RestaBarcodeApp);
