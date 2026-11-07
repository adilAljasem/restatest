import { Interaction } from "@web/public/interaction";
import { registry } from "@web/core/registry";
import { rpc } from "@web/core/network/rpc";
import { _t } from "@web/core/l10n/translation";
import {
    capturePhoto,
    decodeQrImage,
    faceFromPhoto,
    captureLiveFace,
    getDeviceToken,
    getPosition,
    loadFaceApi,
    openCamera,
    scanQr,
    stopCamera,
} from "@bs_hr_ess_attendance/js/camera";

export class EssAttendance extends Interaction {
    static selector = ".o_ess_attendance";
    dynamicContent = {
        "[data-ess-punch]": { "t-on-click": this.onPunch },
        "[data-ess-camera-close]": { "t-on-click": this.closeCamera },
    };

    setup() {
        this.config = JSON.parse(this.el.dataset.config || "{}");
        this.statusEl = this.el.querySelector("[data-ess-status]");
        this.cameraEl = this.el.querySelector("[data-ess-camera]");
        this.video = this.cameraEl.querySelector("video");
        this.canvas = this.cameraEl.querySelector("canvas");
        this.busy = false;
        this.cancelled = false;
        this.stream = null;
    }

    start() {
        const clock = this.el.querySelector("[data-ess-clock]");
        const tick = () => {
            clock.textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
        };
        tick();
        this.clockTimer = setInterval(tick, 10000);
        // Scanned the branch QR with the phone camera app: punch right away.
        if (this.config.qr_code && this.config.logic === "all") {
            this.punch("all");
        } else if (this.config.qr_code && this.config.logic === "any") {
            this.punch("qr");
        }
    }

    destroy() {
        clearInterval(this.clockTimer);
        this.closeCamera();
    }

    setStatus(message, tone = "info") {
        this.statusEl.innerHTML = "";
        if (!message) {
            return;
        }
        const div = document.createElement("div");
        div.className = `alert alert-${tone} mb-0`;
        div.textContent = message;
        this.statusEl.appendChild(div);
    }

    async openCameraOverlay(title, facingMode) {
        this.cancelled = false;
        this.cameraEl.classList.remove("d-none");
        this.cameraEl.querySelector("[data-ess-camera-title]").textContent = title;
        this.cameraEl.classList.toggle("is-mirrored", facingMode === "user");
        this.stream = await openCamera(this.video, facingMode);
    }

    closeCamera() {
        this.cancelled = true;
        stopCamera(this.stream, this.video);
        this.stream = null;
        this.cameraEl?.classList.add("d-none");
    }

    setHint(text) {
        this.cameraEl.querySelector("[data-ess-camera-hint]").textContent = text;
    }

    /** Live scan when the camera stream is available, else a native photo (Odoo app WebViews). */
    async readQr() {
        try {
            await this.openCameraOverlay(_t("Scan the QR code on the branch screen"), "environment");
        } catch {
            this.closeCamera();
            this.setStatus(_t("Take a photo of the QR code on the branch screen"));
            const img = await this.waitFor(capturePhoto("environment"));
            const code = await this.waitFor(decodeQrImage(img, this.canvas));
            if (!code) {
                throw new Error(_t("No QR code found on the photo. Retry closer to the screen."));
            }
            return code;
        }
        this.setHint(_t("Point the camera at the QR code"));
        const code = await this.waitFor(scanQr(this.video, this.canvas, () => this.cancelled));
        this.closeCamera();
        if (!code) {
            throw new Error(_t("Cancelled."));
        }
        return code;
    }

    async readFace() {
        this.setStatus(_t("Loading face recognition..."));
        const fa = await this.waitFor(loadFaceApi(this.config.models_url));
        try {
            await this.openCameraOverlay(_t("Face check"), "user");
        } catch {
            this.closeCamera();
            if (!this.config.face_photo_fallback) {
                throw new Error(_t("The camera cannot be used here. Open the portal in Safari / Chrome."));
            }
            this.setStatus(_t("Take a selfie to check in"));
            const img = await this.waitFor(capturePhoto("user"));
            return this.waitFor(faceFromPhoto(fa, img, this.canvas));
        }
        const face = await this.waitFor(
            captureLiveFace(fa, this.video, this.canvas, {
                setHint: (t) => this.setHint(t),
                isCancelled: () => this.cancelled,
            })
        );
        this.closeCamera();
        if (!face) {
            throw new Error(_t("Cancelled."));
        }
        return face;
    }

    onPunch(ev) {
        this.punch(ev.currentTarget.dataset.essPunch);
    }

    async punch(method) {
        if (this.busy) {
            return;
        }
        this.busy = true;
        const cfg = this.config;
        const need = (m) => (method === "all" ? cfg[m] : method === m);
        const payload = { device_token: getDeviceToken(), method: method === "all" ? null : method };
        try {
            if (need("geo")) {
                this.setStatus(_t("Getting your location..."));
                Object.assign(payload, await this.waitFor(getPosition()));
            }
            if (need("qr")) {
                if (cfg.qr_code) {
                    payload.qr_code = cfg.qr_code;
                    cfg.qr_code = ""; // a code is valid once
                } else {
                    payload.qr_code = await this.readQr();
                }
            }
            if (need("face")) {
                const face = await this.readFace();
                payload.face_descriptor = face.descriptor;
                payload.photo = face.photo;
                payload.liveness = face.liveness;
                payload.photo_mode = Boolean(face.photo_mode);
            }
            this.setStatus(_t("Sending..."));
            const result = await this.waitFor(rpc("/my/hr/attendance/punch", payload));
            if (result.ok) {
                this.setStatus(result.message, result.suspicious ? "warning" : "success");
                if (navigator.vibrate) {
                    navigator.vibrate(80);
                }
                setTimeout(() => window.location.assign("/my/hr/attendance"), 1800);
            } else {
                this.setStatus(result.message, "danger");
            }
        } catch (error) {
            this.closeCamera();
            const msg = error?.data?.message || error?.message || _t("Something went wrong. Retry.");
            this.setStatus(msg, "danger");
        } finally {
            this.busy = false;
        }
    }
}

registry.category("public.interactions").add("bs_hr_ess_attendance.attendance", EssAttendance);
