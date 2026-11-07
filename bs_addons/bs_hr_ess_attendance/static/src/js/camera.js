import { loadJS } from "@web/core/assets";
import { _t } from "@web/core/l10n/translation";

const LIB = "/bs_hr_ess_attendance/static/lib";
let faceApiReady = null;

/** Random, persistent identifier of this browser (device binding). */
export function getDeviceToken() {
    const KEY = "bs_ess_device";
    try {
        let token = window.localStorage.getItem(KEY);
        if (!token) {
            const bytes = new Uint8Array(24);
            window.crypto.getRandomValues(bytes);
            token = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
            window.localStorage.setItem(KEY, token);
        }
        return token;
    } catch {
        return "";
    }
}

/** True inside the Odoo iOS / Android app (WebView). */
export function isOdooApp() {
    return /OdooMobile/i.test(navigator.userAgent);
}

/**
 * Native camera capture through a file input: works in every WebView (Odoo app included)
 * even when live camera streams (getUserMedia) are not allowed. Resolves with an HTMLImageElement.
 */
export function capturePhoto(facingMode = "environment") {
    return new Promise((resolve, reject) => {
        const input = document.createElement("input");
        input.type = "file";
        input.accept = "image/*";
        input.setAttribute("capture", facingMode === "user" ? "user" : "environment");
        input.style.display = "none";
        document.body.appendChild(input);
        input.addEventListener("change", () => {
            const file = input.files?.[0];
            input.remove();
            if (!file) {
                reject(new Error(_t("Cancelled.")));
                return;
            }
            const img = new Image();
            img.onload = () => resolve(img);
            img.onerror = () => reject(new Error(_t("Could not read the photo.")));
            img.src = URL.createObjectURL(file);
        });
        input.click();
    });
}

/** Decode a QR code from a still image (fallback mode). */
export async function decodeQrImage(img, canvas) {
    await loadJS(`${LIB}/jsqr/jsQR.js`);
    const scale = Math.min(1, 1200 / Math.max(img.naturalWidth, img.naturalHeight));
    canvas.width = Math.round(img.naturalWidth * scale);
    canvas.height = Math.round(img.naturalHeight * scale);
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
    const data = ctx.getImageData(0, 0, canvas.width, canvas.height);
    const code = window.jsQR(data.data, data.width, data.height, { inversionAttempts: "attemptBoth" });
    return code?.data || null;
}

/** Face signature from a still photo (fallback mode, no liveness). */
export async function faceFromPhoto(fa, img, canvas) {
    const det = await detectFace(fa, img);
    if (!det) {
        throw new Error(_t("No face found on the photo. Retry facing the camera in good light."));
    }
    const width = 240;
    canvas.width = width;
    canvas.height = Math.round((width * img.naturalHeight) / img.naturalWidth);
    canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
    return {
        descriptor: Array.from(det.descriptor),
        photo: canvas.toDataURL("image/jpeg", 0.7),
        liveness: false,
        photo_mode: true,
    };
}

export async function openCamera(video, facingMode = "user") {
    if (!navigator.mediaDevices?.getUserMedia) {
        throw new Error(_t("This browser cannot use the camera. Open the page in Safari or Chrome."));
    }
    const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode, width: { ideal: 640 }, height: { ideal: 480 } },
        audio: false,
    });
    video.srcObject = stream;
    await video.play();
    return stream;
}

export function stopCamera(stream, video) {
    stream?.getTracks().forEach((t) => t.stop());
    if (video) {
        video.srcObject = null;
    }
}

export function getPosition() {
    return new Promise((resolve, reject) => {
        if (!navigator.geolocation) {
            reject(new Error(_t("Location is not available on this device.")));
            return;
        }
        navigator.geolocation.getCurrentPosition(
            (pos) =>
                resolve({
                    latitude: pos.coords.latitude,
                    longitude: pos.coords.longitude,
                    accuracy: pos.coords.accuracy,
                }),
            (err) => {
                const msg =
                    err.code === 1
                        ? _t("Location permission denied. Allow location for this site in your browser settings.")
                        : _t("Could not get your location. Move outside or enable GPS and retry.");
                reject(new Error(msg));
            },
            { enableHighAccuracy: true, timeout: 20000, maximumAge: 0 }
        );
    });
}

/** Scan QR codes from the camera until one is found (or `isCancelled()` returns true). */
export async function scanQr(video, canvas, isCancelled) {
    await loadJS(`${LIB}/jsqr/jsQR.js`);
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    while (!isCancelled()) {
        if (video.readyState === video.HAVE_ENOUGH_DATA) {
            canvas.width = video.videoWidth;
            canvas.height = video.videoHeight;
            ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
            const img = ctx.getImageData(0, 0, canvas.width, canvas.height);
            const code = window.jsQR(img.data, img.width, img.height, { inversionAttempts: "dontInvert" });
            if (code?.data) {
                return code.data;
            }
        }
        await new Promise((r) => setTimeout(r, 150));
    }
    return null;
}

export async function loadFaceApi(modelsUrl) {
    if (!faceApiReady) {
        faceApiReady = (async () => {
            await loadJS(`${LIB}/face-api/face-api.js`);
            const fa = window.faceapi;
            await fa.nets.tinyFaceDetector.loadFromUri(modelsUrl);
            await fa.nets.faceLandmark68Net.loadFromUri(modelsUrl);
            await fa.nets.faceRecognitionNet.loadFromUri(modelsUrl);
            return fa;
        })();
        faceApiReady.catch(() => {
            faceApiReady = null;
        });
    }
    return faceApiReady;
}

export async function detectFace(fa, video) {
    const options = new fa.TinyFaceDetectorOptions({ inputSize: 320, scoreThreshold: 0.5 });
    return fa.detectSingleFace(video, options).withFaceLandmarks().withFaceDescriptor();
}

function dist(a, b) {
    return Math.hypot(a.x - b.x, a.y - b.y);
}

/** Eye aspect ratio: drops when the eye closes. */
function eyeRatio(eye) {
    return (dist(eye[1], eye[5]) + dist(eye[2], eye[4])) / (2 * dist(eye[0], eye[3]));
}

export function faceMetrics(detection) {
    const lm = detection.landmarks;
    const left = lm.getLeftEye();
    const right = lm.getRightEye();
    const nose = lm.getNose()[3];
    const ear = (eyeRatio(left) + eyeRatio(right)) / 2;
    const leftCenter = { x: (left[0].x + left[3].x) / 2, y: (left[0].y + left[3].y) / 2 };
    const rightCenter = { x: (right[0].x + right[3].x) / 2, y: (right[0].y + right[3].y) / 2 };
    const eyeDist = dist(leftCenter, rightCenter) || 1;
    const yaw = (nose.x - (leftCenter.x + rightCenter.x) / 2) / eyeDist;
    return { ear, yaw };
}

export function snapshot(video, canvas, width = 240) {
    const ratio = video.videoHeight / (video.videoWidth || 1);
    canvas.width = width;
    canvas.height = Math.round(width * ratio);
    canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
    return canvas.toDataURL("image/jpeg", 0.7);
}

/**
 * Liveness challenge + recognition. Resolves with {descriptor, photo, liveness: true}.
 * Challenge is random: blink, or turn the head and come back.
 */
export async function captureLiveFace(fa, video, canvas, { setHint, isCancelled, timeoutMs = 25000 }) {
    const challenge = Math.random() < 0.5 ? "blink" : "turn";
    setHint(challenge === "blink" ? _t("Look at the camera and blink slowly") : _t("Look at the camera, then turn your head to one side and back"));
    const started = Date.now();
    let baselineYaw = null;
    let sawOpen = false;
    let sawClosed = false;
    let turned = false;
    let best = null;
    while (!isCancelled()) {
        if (Date.now() - started > timeoutMs) {
            throw new Error(_t("Liveness check timed out. Retry in good light."));
        }
        const det = await detectFace(fa, video);
        if (!det) {
            setHint(_t("No face detected - center your face in the frame"));
            await new Promise((r) => setTimeout(r, 120));
            continue;
        }
        const { ear, yaw } = faceMetrics(det);
        if (ear > 0.27 && Math.abs(yaw - (baselineYaw ?? yaw)) < 0.12) {
            best = det; // frontal, eyes open: best frame for recognition
        }
        if (challenge === "blink") {
            if (ear > 0.27) {
                if (sawClosed && sawOpen && best) {
                    break;
                }
                sawOpen = true;
            } else if (ear < 0.21 && sawOpen) {
                sawClosed = true;
            }
        } else {
            if (baselineYaw === null) {
                baselineYaw = yaw;
            } else if (Math.abs(yaw - baselineYaw) > 0.22) {
                turned = true;
                setHint(_t("Good, now look back at the camera"));
            } else if (turned && Math.abs(yaw - baselineYaw) < 0.08 && best) {
                break;
            }
        }
        await new Promise((r) => setTimeout(r, 60));
    }
    if (isCancelled()) {
        return null;
    }
    // final recognition frame
    const finalDet = (await detectFace(fa, video)) || best;
    return {
        descriptor: Array.from(finalDet.descriptor),
        photo: snapshot(video, canvas),
        liveness: true,
    };
}
