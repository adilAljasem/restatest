import { Interaction } from "@web/public/interaction";
import { registry } from "@web/core/registry";
import { rpc } from "@web/core/network/rpc";
import { _t } from "@web/core/l10n/translation";
import { detectFace, faceMetrics, loadFaceApi, openCamera, stopCamera } from "@bs_hr_ess_attendance/js/camera";

const CAPTURES = 5;

export class EssFaceEnroll extends Interaction {
    static selector = ".o_ess_face_enroll";
    dynamicContent = {
        "[data-ess-start]": { "t-on-click": this.onStart },
    };

    setup() {
        this.video = this.el.querySelector("video");
        this.hint = this.el.querySelector("[data-ess-hint]");
        this.bar = this.el.querySelector("[data-ess-progress]");
        this.button = this.el.querySelector("[data-ess-start]");
        this.stream = null;
    }

    destroy() {
        stopCamera(this.stream, this.video);
    }

    async onStart() {
        if (!this.el.querySelector("[data-ess-consent]").checked) {
            this.hint.textContent = _t("Please tick the consent box first.");
            return;
        }
        this.button.disabled = true;
        try {
            this.hint.textContent = _t("Loading face recognition...");
            const fa = await this.waitFor(loadFaceApi(this.el.dataset.modelsUrl));
            try {
                this.stream = await this.waitFor(openCamera(this.video, "user"));
            } catch {
                throw new Error(
                    _t("The live camera is not available in this app. Do the enrollment once in Safari or Chrome.")
                );
            }
            const descriptors = [];
            let tries = 0;
            while (descriptors.length < CAPTURES && tries < 200) {
                tries++;
                const det = await this.waitFor(detectFace(fa, this.video));
                if (!det) {
                    this.hint.textContent = _t("No face detected - center your face");
                } else if (faceMetrics(det).ear < 0.24) {
                    this.hint.textContent = _t("Keep your eyes open");
                } else {
                    descriptors.push(Array.from(det.descriptor));
                    this.bar.style.width = `${(descriptors.length / CAPTURES) * 100}%`;
                    this.hint.textContent = _t("Hold still... (%s/%s)", descriptors.length, CAPTURES);
                    await new Promise((r) => setTimeout(r, 500));
                }
                await new Promise((r) => setTimeout(r, 80));
            }
            stopCamera(this.stream, this.video);
            if (descriptors.length < CAPTURES) {
                throw new Error(_t("Could not capture your face. Retry in good light."));
            }
            const result = await this.waitFor(
                rpc("/my/hr/attendance/face/enroll", { descriptors, consent: true })
            );
            this.hint.textContent = result.message;
            if (result.ok) {
                this.hint.classList.add("text-success");
                setTimeout(() => window.location.assign("/my/hr/attendance"), 1500);
            } else {
                this.button.disabled = false;
            }
        } catch (error) {
            stopCamera(this.stream, this.video);
            this.hint.textContent = error?.data?.message || error?.message || _t("Something went wrong.");
            this.button.disabled = false;
        }
    }
}

registry.category("public.interactions").add("bs_hr_ess_attendance.face_enroll", EssFaceEnroll);
