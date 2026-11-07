import { Interaction } from "@web/public/interaction";
import { registry } from "@web/core/registry";
import { rpc } from "@web/core/network/rpc";
import { _t } from "@web/core/l10n/translation";
import { loadJS } from "@web/core/assets";

/** Branch screen: refreshes the rotating QR code every period (default 10 s). */
export class EssKiosk extends Interaction {
    static selector = ".o_ess_kiosk";

    setup() {
        this.token = this.el.dataset.token;
        this.period = parseInt(this.el.dataset.period || "10", 10);
        this.img = this.el.querySelector("[data-ess-qr]");
        this.bar = this.el.querySelector("[data-ess-bar]");
        this.clock = this.el.querySelector("[data-ess-clock]");
        this.present = this.el.querySelector("[data-ess-present]");
        this.online = this.el.querySelector("[data-ess-online]");
    }

    async willStart() {
        // QR generated in the browser: no server-side image rendering every 10 seconds.
        await loadJS("/bs_hr_ess_attendance/static/lib/qrcode-generator/qrcode.js");
    }

    renderQr(value) {
        const qr = window.qrcode(0, "M");
        qr.addData(value);
        qr.make();
        return qr.createDataURL(10, 2);
    }

    start() {
        this.refresh();
        this.clockTimer = setInterval(() => {
            this.clock.textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
        }, 1000);
        // keep the tablet awake when supported
        navigator.wakeLock?.request("screen").catch(() => {});
    }

    destroy() {
        clearTimeout(this.timer);
        clearInterval(this.clockTimer);
    }

    async refresh() {
        let delay = 3000;
        try {
            const res = await rpc(`/hr/kiosk/${this.token}/code`, {});
            if (res.ok) {
                this.img.src = this.renderQr(res.value);
                this.img.dataset.value = res.value;
                this.online.classList.add("is-online");
                this.present.textContent = _t("%s employee(s) present", res.present);
                delay = Math.max(500, res.remaining * 1000 + 150);
                this.animateBar(res.remaining, res.period);
            } else {
                window.location.reload();
                return;
            }
        } catch {
            this.online.classList.remove("is-online");
        }
        this.timer = setTimeout(() => this.refresh(), delay);
    }

    animateBar(remaining, period) {
        this.bar.style.transition = "none";
        this.bar.style.width = `${(remaining / period) * 100}%`;
        void this.bar.offsetWidth;
        this.bar.style.transition = `width ${remaining}s linear`;
        this.bar.style.width = "0%";
    }
}

registry.category("public.interactions").add("bs_hr_ess_attendance.kiosk", EssKiosk);
