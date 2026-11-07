/** @odoo-module */

// Reliable logo on printed receipts (Odoo 18).
//
// Odoo 19 already waits for the images before browser printing and before
// converting a receipt to an image.
//
// Receipt printers (ePOS / IoT): the receipt is turned into an image with
//    html-to-image. The logo was sometimes missing from that image (first
//    print, or receipts printed automatically while the iPad app is in the
//    background, e.g. delivery orders). After the conversion the logo is drawn
//    directly on the final image at its place, from the already decoded logo,
//    so it is always printed. Kitchen tickets and other receipts without our
//    logo go through Odoo's printing unchanged.
//
// Sharp black print (Receipt Settings > Sharp Black Print, idea of
//    pos_kitchen_ticket_custom): the printer driver turns the image to black
//    and white with dithering, so thin anti-aliased letters print as grey dots
//    (faded receipt). The receipt image is first made pure black and white
//    with a threshold; the logo is drawn after that, so it keeps its shades.

import { BasePrinter } from "@point_of_sale/app/utils/printer/base_printer";
import { htmlToCanvas } from "@point_of_sale/app/services/render_service";
import { ConnectionLostError } from "@web/core/network/rpc";
import { patch } from "@web/core/utils/patch";

const MAX_WAIT_MS = 3000;

// Pixels darker than this become black (higher = bolder letters).
export const PINO_THRESHOLD = 190;

export function thresholdCanvas(canvas, threshold = PINO_THRESHOLD) {
    const ctx = canvas.getContext("2d");
    const image = ctx.getImageData(0, 0, canvas.width, canvas.height);
    const pixels = image.data;
    for (let i = 0; i < pixels.length; i += 4) {
        const alpha = pixels[i + 3] / 255;
        const gray =
            (pixels[i] * 0.299 + pixels[i + 1] * 0.587 + pixels[i + 2] * 0.114) * alpha +
            255 * (1 - alpha);
        const value = gray < threshold ? 0 : 255;
        pixels[i] = pixels[i + 1] = pixels[i + 2] = value;
        pixels[i + 3] = 255;
    }
    ctx.putImageData(image, 0, 0);
    return canvas;
}

// requestAnimationFrame does not run while the app is in the background:
// always fall back to a timer.
function nextPaint() {
    return new Promise((resolve) => {
        const done = () => resolve();
        const timer = setTimeout(done, 120);
        requestAnimationFrame(() => requestAnimationFrame(() => {
            clearTimeout(timer);
            done();
        }));
    });
}

function imageReady(img) {
    const loaded = img.complete && img.naturalWidth
        ? Promise.resolve()
        : new Promise((resolve) => {
              img.addEventListener("load", resolve, { once: true });
              img.addEventListener("error", resolve, { once: true });
          });
    return loaded.then(() => (img.decode ? img.decode().catch(() => {}) : undefined));
}

export async function waitForImages(el) {
    const images = [...el.querySelectorAll("img")];
    const timeout = new Promise((resolve) => setTimeout(resolve, MAX_WAIT_MS));
    await Promise.race([Promise.all(images.map(imageReady)), timeout]);
    await nextPaint();
}

const decodedImages = new Map(); // src -> decoded HTMLImageElement

async function decodedImage(src) {
    if (!decodedImages.has(src)) {
        const img = new Image();
        img.src = src;
        await imageReady(img);
        if (!img.naturalWidth) {
            return null;
        }
        decodedImages.set(src, img);
    }
    return decodedImages.get(src);
}

/**
 * Draw the logo on the printed image at the place of `.pino-logo`.
 * `mounted` is the receipt copy html-to-image rendered (left mounted in the
 * print container by Odoo), so its layout gives the logo position.
 */
export async function stampLogo(canvas, mounted) {
    const logoEl = mounted?.querySelector?.(".pino-logo");
    if (!logoEl?.getAttribute("src")) {
        return false;
    }
    const logo = await decodedImage(logoEl.getAttribute("src"));
    if (!logo) {
        return false;
    }
    const root = mounted.getBoundingClientRect();
    const box = logoEl.getBoundingClientRect();
    if (!root.width || !box.width || !box.height) {
        return false;
    }
    const scaleX = canvas.width / root.width;
    const scaleY = canvas.height / root.height;
    const x = (box.left - root.left) * scaleX;
    const y = (box.top - root.top) * scaleY;
    const w = box.width * scaleX;
    const h = box.height * scaleY;
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(x, y, w, h);
    ctx.drawImage(logo, x, y, w, h);
    return true;
}

// Receipt designs of this module. Each sets its own print width on its root.
const BRANDED_RECEIPT = [
    ".pos-receipt.pino-receipt",
    ".pos-receipt.bilingual-receipt",
    ".pos-receipt.branding-paper",
].join(", ");

/**
 * The receipt design itself when another module wrapped it in a second
 * `.pos-receipt` (e.g. bs_pos_blend_intgeration < 19.0.1.37.0 on delivery app
 * orders). Odoo adds `pos-receipt-print` (512px) to the element it converts,
 * so with the wrapper the design kept its 76mm screen width and was printed
 * on half of the paper. The wrapper's extra lines (platform, app order number)
 * are already printed by the design's delivery card.
 */
export function brandedRoot(receipt) {
    if (!receipt?.querySelector || receipt.matches?.(BRANDED_RECEIPT)) {
        return receipt;
    }
    return receipt.querySelector(BRANDED_RECEIPT) || receipt;
}

/** The Puls receipt element inside what Odoo sends to the printer, if any. */
export function pinoRoot(receipt) {
    if (receipt?.classList?.contains?.("pino-receipt")) {
        return receipt;
    }
    return receipt?.querySelector?.(".pino-receipt") || null;
}

patch(BasePrinter.prototype, {
    async printReceipt(receipt) {
        if (receipt) {
            receipt = brandedRoot(receipt);
            arguments[0] = receipt;
        }
        if (!pinoRoot(receipt)) {
            // Kitchen / preparation tickets and other designs: Odoo unchanged.
            return super.printReceipt(...arguments);
        }
        // One receipt at a time per printer (as Odoo's own print queue does).
        const previous = this._pinoPrintChain || Promise.resolve();
        const job = previous.catch(() => {}).then(() => this._printPinoReceipt(receipt));
        this._pinoPrintChain = job;
        return job;
    },

    async _printPinoReceipt(receipt) {
        const started = performance.now();
        // No extra image wait here: Odoo 19's htmlToCanvas already waits for
        // the images of the mounted receipt (up to 3 s). Waiting here as well
        // could add another 3 s per receipt when an image is slow to load.
        const canvas = await htmlToCanvas(receipt, { addClass: "pos-receipt-print" });
        const rendered = performance.now();
        if (pinoRoot(receipt)?.classList?.contains("pino-sharp")) {
            try {
                thresholdCanvas(canvas);
            } catch (error) {
                console.warn("Receipt image could not be made black and white", error);
            }
        }
        try {
            const mounted = document.querySelector(
                ".render-container .pos-receipt.pino-receipt.pos-receipt-print"
            );
            await stampLogo(canvas, mounted);
        } catch (error) {
            console.warn("Receipt logo could not be drawn on the printed image", error);
        }
        const prepared = performance.now();
        let printResult;
        try {
            printResult = await this.sendPrintingJob(this.processCanvas(canvas));
            const total = performance.now() - started;
            if (total > 2000) {
                console.warn(
                    `[pino print] slow receipt: ${Math.round(total)} ms ` +
                    `(image ${Math.round(rendered - started)} ms, ` +
                    `processing ${Math.round(prepared - rendered)} ms, ` +
                    `printer ${Math.round(performance.now() - prepared)} ms)`
                );
            }
        } catch (error) {
            // Error in communicating to the IoT box / printer.
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
