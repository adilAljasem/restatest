/** @odoo-module */

// Crisp kitchen tickets.
//
// Odoo turns a ticket into an image and the printer driver converts that image
// to black and white with Floyd-Steinberg dithering. Thin, anti-aliased letters
// then print as grey dots (small, faded text). For our kitchen tickets the
// image is first made pure black and white with a threshold: every letter
// pixel becomes solid black, like the printer's own font.
// Other receipts go through Odoo's printing unchanged.

import { BasePrinter } from "@point_of_sale/app/utils/printer/base_printer";
import { htmlToCanvas } from "@point_of_sale/app/services/render_service";
import { ConnectionLostError } from "@web/core/network/rpc";
import { patch } from "@web/core/utils/patch";

// Pixels darker than this become black (higher = bolder letters).
export const KT_THRESHOLD = 190;

export function thresholdCanvas(canvas, threshold = KT_THRESHOLD) {
    const ctx = canvas.getContext("2d");
    const image = ctx.getImageData(0, 0, canvas.width, canvas.height);
    const pixels = image.data;
    for (let i = 0; i < pixels.length; i += 4) {
        const alpha = pixels[i + 3] / 255;
        const gray = (pixels[i] * 0.299 + pixels[i + 1] * 0.587 + pixels[i + 2] * 0.114) * alpha + 255 * (1 - alpha);
        const value = gray < threshold ? 0 : 255;
        pixels[i] = pixels[i + 1] = pixels[i + 2] = value;
        pixels[i + 3] = 255;
    }
    ctx.putImageData(image, 0, 0);
    return canvas;
}

patch(BasePrinter.prototype, {
    async printReceipt(receipt) {
        if (!receipt?.classList?.contains("kt-ticket")) {
            return super.printReceipt(...arguments);
        }
        // One ticket at a time per printer, like Odoo's own queue.
        const previous = this._ktPrintChain || Promise.resolve();
        const job = previous.catch(() => {}).then(() => this._printKitchenTicket(receipt));
        this._ktPrintChain = job;
        return job;
    },

    async _printKitchenTicket(receipt) {
        const canvas = thresholdCanvas(await htmlToCanvas(receipt, { addClass: "pos-receipt-print" }));
        let printResult;
        try {
            printResult = await this.sendPrintingJob(this.processCanvas(canvas));
        } catch (error) {
            if (error instanceof ConnectionLostError) {
                return this.getOfflineError();
            }
            return this.getActionError();
        }
        if (!printResult || printResult.result === false) {
            return this.getResultsError(printResult);
        }
        return { successful: true, warningCode: this.getResultWarningCode?.(printResult) };
    },
});
