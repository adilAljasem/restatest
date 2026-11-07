/* Customer order tracking page (no framework, works on any phone browser). */
(function () {
    "use strict";

    const TOKEN = window.TRK_TOKEN;
    const VAPID = window.TRK_VAPID;
    const BASE = "/track/" + TOKEN;
    const POLL_MS = 6000;
    const RING_MAX_MS = 90000;

    const T = {
        ar: {
            your_order: "رقم طلبك",
            step_received: "استلمنا طلبك",
            step_preparing: "قيد التحضير",
            step_ready: "جاهز",
            order_details: "تفاصيل الطلب",
            alerts_off: "فعّل التنبيه ونبلغك بصوت واهتزاز أول ما يجهز طلبك.",
            alerts_on: "التنبيه مفعّل. بنبلغك أول ما يجهز طلبك.",
            alerts_keep_open: "خلّ هذي الصفحة مفتوحة عشان يوصلك الصوت.",
            alerts_blocked: "المتصفح منع الإشعارات، بس الصوت بيشتغل طالما الصفحة مفتوحة.",
            enable_alerts: "نبّهني لما يجهز",
            nps_q: "كم احتمال تنصح أصحابك يزورون {shop}؟",
            nps_low: "غير محتمل",
            nps_high: "محتمل جداً",
            nps_comment: "وش أكثر شي عجبك، أو وش نقدر نحسّنه؟ (اختياري)",
            nps_send: "أرسل التقييم",
            nps_sending: "جاري الإرسال…",
            nps_error: "ما قدرنا نرسل التقييم. تأكد من الاتصال وجرّب مرة ثانية.",
            thanks_title: "شكراً لك!",
            thanks_text: "وصلنا تقييمك، ويساعدنا نخدمك أفضل.",
            google_review: "قيّمنا على خرائط قوقل",
            stop_alert: "إيقاف التنبيه",
            received_h: "استلمنا طلبك",
            received_s: "بنبدأ نحضّره خلال لحظات.",
            preparing_h: "طلبك قيد التحضير",
            preparing_s: "خلّك قريب، بنبلغك أول ما يجهز.",
            ready_h: "طلبك جاهز!",
            ready_s: "تفضّل استلمه من الكاونتر. بالعافية!",
            completed_h: "استلمت طلبك",
            completed_s: "بالعافية! نتمنى إنك استمتعت.",
            cancelled_h: "تم إلغاء الطلب",
            cancelled_s: "للاستفسار تواصل مع الكاشير.",
            visit_h: "شكراً لزيارتك",
            visit_s: "بنسألك عن رأيك بعد ما تستمتع بطلبك.",
            visit_nps_s: "رأيك يهمنا ويساعدنا نتحسّن.",
            notif_title: "طلبك رقم {n} جاهز!",
            media_waiting: "طلب رقم {n} قيد التحضير",
            sheet_title: "فعّل التنبيه عشان نبلغك أول ما يجهز طلبك",
            sheet_p1: "يرن ويهتز حتى لو قفلت الشاشة",
            sheet_p2: "يوصلك إشعار لو طلعت من الصفحة",
            sheet_allow: "فعّل التنبيهات وتابع طلبك",
            sheet_note: "بعد الضغط، اختر «سماح» في رسالة المتصفح.",
            check_sound: "الصوت شغّال حتى مع قفل الشاشة",
            check_notif_on: "الإشعارات مفعّلة",
            check_notif_off: "الإشعارات غير مفعّلة",
            check_notif_denied: "الإشعارات مرفوضة. فعّلها من إعدادات المتصفح للموقع",
            check_notif_ios: "إشعارات الآيفون تحتاج إضافة الصفحة للشاشة الرئيسية",
            hint_ios: "خلّ الصفحة مفتوحة ولا تقفل المتصفح، وتأكد إن الجوال مو على الصامت.",
            hint_android: "تقدر تقفل الشاشة، بنرن لك أول ما يجهز.",
            lang_btn: "EN",
        },
        en: {
            your_order: "Your order number",
            step_received: "Received",
            step_preparing: "Preparing",
            step_ready: "Ready",
            order_details: "Order details",
            alerts_off: "Turn on alerts and we'll ring and vibrate your phone the moment your order is ready.",
            alerts_on: "Alerts are on. We'll let you know as soon as it's ready.",
            alerts_keep_open: "Keep this page open so the sound can reach you.",
            alerts_blocked: "Notifications are blocked, but the sound still plays while this page is open.",
            enable_alerts: "Alert me when it's ready",
            nps_q: "How likely are you to recommend {shop} to a friend?",
            nps_low: "Not likely",
            nps_high: "Very likely",
            nps_comment: "What did you like most, or what can we improve? (optional)",
            nps_send: "Send rating",
            nps_sending: "Sending…",
            nps_error: "Your rating wasn't sent. Check your connection and try again.",
            thanks_title: "Thank you!",
            thanks_text: "We got your rating. It helps us serve you better.",
            google_review: "Review us on Google Maps",
            stop_alert: "Stop the alert",
            received_h: "We got your order",
            received_s: "We'll start preparing it in a moment.",
            preparing_h: "Your order is being prepared",
            preparing_s: "Stay close, we'll alert you when it's ready.",
            ready_h: "Your order is ready!",
            ready_s: "Please pick it up at the counter. Enjoy!",
            completed_h: "Order picked up",
            completed_s: "Enjoy your meal!",
            cancelled_h: "This order was cancelled",
            cancelled_s: "Please ask the cashier for details.",
            visit_h: "Thanks for your visit",
            visit_s: "We'll ask for your opinion once you've enjoyed your order.",
            visit_nps_s: "Your opinion helps us get better.",
            notif_title: "Order #{n} is ready!",
            media_waiting: "Order #{n} is being prepared",
            sheet_title: "Turn on alerts so we can tell you when your order is ready",
            sheet_p1: "Rings and vibrates even with the screen locked",
            sheet_p2: "Sends a notification if you leave the page",
            sheet_allow: "Turn on alerts and track your order",
            sheet_note: "After tapping, choose \"Allow\" in the browser's message.",
            check_sound: "Sound stays on with the screen locked",
            check_notif_on: "Notifications are on",
            check_notif_off: "Notifications are off",
            check_notif_denied: "Notifications are blocked. Allow them in the browser's site settings",
            check_notif_ios: "On iPhone, notifications need the page added to the Home Screen",
            hint_ios: "Keep this page open in the browser and make sure the phone isn't on silent.",
            hint_android: "You can lock the screen. We'll ring when it's ready.",
            lang_btn: "ع",
        },
    };

    const $ = (id) => document.getElementById(id);
    const store = {
        get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
        set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } },
    };

    let lang = store.get("trk_lang") || ((navigator.language || "ar").toLowerCase().startsWith("ar") ? "ar" : "en");
    let data = JSON.parse($("trk-data").textContent);

    /* ---------------- one order per phone ----------------
     * The newest order opened on this phone is the current one. An older tracking link
     * (Safari history / address-bar autocomplete, an old tab) jumps to the newest order,
     * and opening a newer link wipes everything saved for the previous orders. */
    const CURRENT_KEY = "trk_current";
    let current = null;
    try { current = JSON.parse(store.get(CURRENT_KEY) || "null"); } catch (e) { current = null; }
    const thisOrder = { token: TOKEN, date: data.date_order || "" };
    if (current && current.token && current.token !== TOKEN && current.date && thisOrder.date
            && current.date > thisOrder.date) {
        location.replace("/track/" + encodeURIComponent(current.token));
        return;
    }
    if (!current || current.token !== TOKEN) {
        try {
            for (let i = localStorage.length - 1; i >= 0; i--) {
                const key = localStorage.key(i);
                if (key && key.startsWith("trk_") && key !== "trk_lang") {
                    localStorage.removeItem(key);
                }
            }
            sessionStorage.clear();
        } catch (e) { /* storage blocked */ }
        if ("caches" in window) {
            caches.keys().then((keys) => keys.forEach((k) => caches.delete(k))).catch(() => {});
        }
        store.set(CURRENT_KEY, JSON.stringify(thisOrder));
    }
    // Page restored from the back/forward cache (old snapshot): load it again from the server.
    window.addEventListener("pageshow", (event) => {
        if (event.persisted) {
            location.reload();
        }
    });
    let lastState = data.state;
    let pollTimer = null;
    let npsTimer = null;
    let selectedScore = null;
    // Audio can only be unlocked by a tap, so alerts are re-enabled on each page load.
    let alertsOn = false;
    let audioCtx = null;
    let ringTimer = null;
    let ringStarted = 0;
    let wakeLock = null;
    let titleTimer = null;
    const baseTitle = document.title;

    const t = (key, vars) => {
        let s = (T[lang] && T[lang][key]) || T.ar[key] || key;
        if (vars) {
            Object.keys(vars).forEach((k) => { s = s.replace("{" + k + "}", vars[k]); });
        }
        return s;
    };

    /* ---------------- rendering ---------------- */
    function applyLang() {
        document.documentElement.lang = lang;
        document.documentElement.dir = lang === "ar" ? "rtl" : "ltr";
        document.querySelectorAll("[data-i18n]").forEach((el) => { el.textContent = t(el.dataset.i18n); });
        $("trk-lang").textContent = t("lang_btn");
        render();
    }

    const pagerOn = () => !data.features || data.features.pager;

    function render() {
        const pager = pagerOn();
        const state = pager ? data.state : "visit";
        const root = $("trk");
        root.dataset.state = state;
        $("trk-number").textContent = data.number;
        $("trk-steps").hidden = !pager;
        $("trk-headline").textContent = t(state + "_h");
        if (pager) {
            $("trk-sub").textContent = state === "ready" && data.shop.ready_message ? data.shop.ready_message : t(state + "_s");
        } else {
            $("trk-sub").textContent = data.nps && (data.nps.available || data.nps.done) ? t("visit_nps_s") : t("visit_s");
        }

        const order = ["received", "preparing", "ready"];
        const idx = state === "completed" ? 2 : Math.max(0, order.indexOf(state));
        document.querySelectorAll("#trk-steps li").forEach((li, i) => {
            li.classList.toggle("is-done", i < idx || (i === idx && (state === "ready" || state === "completed")));
            li.classList.toggle("is-current", i === idx && state !== "ready" && state !== "completed" && state !== "cancelled");
        });
        $("trk-steps").style.setProperty("--progress", (idx / 2) * 66.66 + "%");

        const ul = $("trk-lines");
        ul.textContent = "";
        (data.lines || []).forEach((l) => {
            const li = document.createElement("li");
            const q = document.createElement("span");
            q.className = "qty";
            q.textContent = "×" + (Number.isInteger(l.qty) ? l.qty : Number(l.qty).toFixed(2));
            const n = document.createElement("span");
            n.textContent = l.name;
            if (l.items && l.items.length) {
                const sub = document.createElement("span");
                sub.className = "sub";
                sub.textContent = l.items.join("، ");
                n.appendChild(sub);
            }
            li.append(q, n);
            ul.appendChild(li);
        });

        // Alerts panel only matters while waiting.
        const waiting = pager && (state === "received" || state === "preparing");
        $("trk-alerts").hidden = !waiting;
        renderAlertsPanel();

        // NPS
        const nps = data.nps || {};
        $("trk-nps").hidden = !(nps.available && !nps.done);
        $("trk-thanks").hidden = !nps.done;
        $("trk-nps-q").textContent = t("nps_q", { shop: data.shop.name });
        const g = $("trk-google");
        g.hidden = !(nps.done && nps.score >= 9 && data.shop.google_review_url);
        if (data.shop.google_review_url) {
            g.href = data.shop.google_review_url;
        }
        scheduleNps();
    }

    function buildScale() {
        const scale = $("trk-scale");
        for (let i = 0; i <= 10; i++) {
            const b = document.createElement("button");
            b.type = "button";
            b.textContent = i;
            b.setAttribute("role", "radio");
            b.setAttribute("aria-checked", "false");
            b.dataset.cat = i >= 9 ? "promoter" : i >= 7 ? "passive" : "detractor";
            b.addEventListener("click", () => {
                selectedScore = i;
                scale.querySelectorAll("button").forEach((x) => x.setAttribute("aria-checked", String(x === b)));
                $("trk-nps-send").disabled = false;
            });
            scale.appendChild(b);
        }
    }

    /* ---------------- data ---------------- */
    async function poll() {
        try {
            const res = await fetch(BASE + "/status", { cache: "no-store", credentials: "same-origin" });
            if (!res.ok) {
                return;
            }
            const next = await res.json();
            const wasWaiting = lastState === "received" || lastState === "preparing";
            data = next;
            render();
            if (wasWaiting && (next.state === "ready" || next.state === "completed")) {
                onReady();
            }
            if (!(next.state === "received" || next.state === "preparing")) {
                $("trk-sheet").hidden = true;
            }
            if (next.state === "received" || next.state === "preparing") {
                if (ringTimer) {
                    stopRinging(false);
                }
            }
            lastState = next.state;
        } catch (e) {
            /* offline: try again on next tick */
        }
        maybeStopPolling();
    }

    function finished() {
        if (!pagerOn()) {
            return true; // no live status: the rating timer refreshes the page data when needed
        }
        return data.state === "cancelled" || (data.state === "completed" && data.nps && data.nps.done);
    }

    function startPolling() {
        if (!pollTimer && !finished()) {
            pollTimer = setInterval(poll, POLL_MS);
        }
    }

    function maybeStopPolling() {
        if (finished() && pollTimer) {
            clearInterval(pollTimer);
            pollTimer = null;
        }
    }

    function scheduleNps() {
        const nps = data.nps || {};
        if (npsTimer || nps.done || nps.available || nps.seconds_left == null) {
            return;
        }
        npsTimer = setTimeout(() => { npsTimer = null; poll(); }, (nps.seconds_left + 2) * 1000);
    }

    async function sendNps() {
        if (selectedScore === null) {
            return;
        }
        const btn = $("trk-nps-send");
        btn.disabled = true;
        btn.textContent = t("nps_sending");
        try {
            const res = await fetch(BASE + "/nps", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                credentials: "same-origin",
                body: JSON.stringify({
                    jsonrpc: "2.0", method: "call", id: Date.now(),
                    params: { score: selectedScore, comment: $("trk-comment").value },
                }),
            });
            const json = await res.json();
            if (!json.result || !json.result.data) {
                throw new Error("bad response");
            }
            data = json.result.data;
            render();
            maybeStopPolling();
        } catch (e) {
            btn.disabled = false;
            alert(t("nps_error"));
        } finally {
            btn.textContent = t("nps_send");
        }
    }

    /* ---------------- sound engine ----------------
     * An <audio> element started by a tap keeps playing when the screen is locked
     * (iOS and Android treat it as media). While waiting it loops near-silence,
     * which keeps the page alive in the background; when the order is ready the
     * same element switches to the chime, so the phone rings with the screen locked. */
    function makeWav(seconds, sampleAt) {
        const rate = 22050;
        const n = Math.floor(rate * seconds);
        const buf = new ArrayBuffer(44 + n * 2);
        const v = new DataView(buf);
        const str = (o, x) => { for (let i = 0; i < x.length; i++) { v.setUint8(o + i, x.charCodeAt(i)); } };
        str(0, "RIFF"); v.setUint32(4, 36 + n * 2, true); str(8, "WAVE"); str(12, "fmt ");
        v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
        v.setUint32(24, rate, true); v.setUint32(28, rate * 2, true); v.setUint16(32, 2, true);
        v.setUint16(34, 16, true); str(36, "data"); v.setUint32(40, n * 2, true);
        for (let i = 0; i < n; i++) {
            const x = Math.max(-1, Math.min(1, sampleAt(i / rate)));
            v.setInt16(44 + i * 2, x * 32767, true);
        }
        return URL.createObjectURL(new Blob([buf], { type: "audio/wav" }));
    }
    let silentUrl = null;
    let chimeUrl = null;
    function soundUrls() {
        if (!silentUrl) {
            // Not perfectly silent: some systems drop an all-zero stream.
            silentUrl = makeWav(1.5, (tt) => 0.0008 * Math.sin(2 * Math.PI * 40 * tt));
            const notes = [[988, 0], [1319, 0.18], [1568, 0.36], [988, 1.2], [1319, 1.38], [1568, 1.56]];
            chimeUrl = makeWav(2.6, (tt) => {
                let x = 0;
                for (const [f, at] of notes) {
                    if (tt >= at) {
                        const d = tt - at;
                        x += Math.sin(2 * Math.PI * f * d) * Math.exp(-d * 4) * Math.min(1, d * 60);
                    }
                }
                return x * 0.45;
            });
        }
        return { silentUrl, chimeUrl };
    }

    let player = null;
    let keepAlive = false;

    function playerPlay(url, volume) {
        if (!player) {
            return Promise.reject(new Error("no player"));
        }
        if (player.dataset.src !== url) {
            player.src = url;
            player.dataset.src = url;
        }
        player.loop = true;
        player.volume = volume;
        return player.play();
    }

    function startKeepAlive() {
        // Must run inside a tap handler the first time.
        try {
            if (!player) {
                player = new Audio();
                player.setAttribute("playsinline", "");
                player.preload = "auto";
            }
            keepAlive = true;
            playerPlay(soundUrls().silentUrl, 1).catch(() => {});
            setMediaSession(false);
        } catch (e) { /* no media */ }
    }

    function stopAudio() {
        keepAlive = false;
        if (player) {
            player.pause();
        }
        if ("mediaSession" in navigator) {
            navigator.mediaSession.playbackState = "none";
        }
    }

    function setMediaSession(ready) {
        if (!("mediaSession" in navigator) || typeof MediaMetadata === "undefined") {
            return;
        }
        try {
            navigator.mediaSession.metadata = new MediaMetadata({
                title: ready ? t("notif_title", { n: data.number }) : t("media_waiting", { n: data.number }),
                artist: data.shop.name,
                artwork: [{ src: "/logo.png?company=" + (data.shop.company_id || ""), sizes: "192x192", type: "image/png" }],
            });
            navigator.mediaSession.playbackState = "playing";
            navigator.mediaSession.setActionHandler("pause", () => { stopRinging(true); });
            navigator.mediaSession.setActionHandler("stop", () => { stopRinging(true); });
        } catch (e) { /* ignore */ }
    }

    // WebAudio fallback for browsers that refuse the <audio> element.
    function unlockAudioCtx() {
        try {
            const Ctx = window.AudioContext || window.webkitAudioContext;
            if (!Ctx) {
                return;
            }
            audioCtx = audioCtx || new Ctx();
            if (audioCtx.state === "suspended") {
                audioCtx.resume();
            }
            const o = audioCtx.createOscillator();
            const g = audioCtx.createGain();
            g.gain.value = 0.0001;
            o.connect(g).connect(audioCtx.destination);
            o.start();
            o.stop(audioCtx.currentTime + 0.05);
        } catch (e) { /* no audio */ }
    }

    function ctxChime() {
        if (!audioCtx) {
            return;
        }
        const now = audioCtx.currentTime;
        [[988, 0], [1319, 0.18], [1568, 0.36]].forEach(([freq, at]) => {
            const o = audioCtx.createOscillator();
            const g = audioCtx.createGain();
            o.type = "sine";
            o.frequency.value = freq;
            g.gain.setValueAtTime(0.0001, now + at);
            g.gain.exponentialRampToValueAtTime(0.5, now + at + 0.02);
            g.gain.exponentialRampToValueAtTime(0.0001, now + at + 0.9);
            o.connect(g).connect(audioCtx.destination);
            o.start(now + at);
            o.stop(now + at + 1);
        });
    }

    /* ---------------- ringing ---------------- */
    let usingCtxFallback = false;

    function vibrateOnce() {
        if (navigator.vibrate) {
            navigator.vibrate([500, 200, 500, 200, 900]);
        }
    }

    function startRinging() {
        stopRinging(false);
        ringStarted = Date.now();
        $("trk").classList.add("is-ringing");
        $("trk-ready-bar").hidden = false;
        setMediaSession(true);
        usingCtxFallback = false;
        playerPlay(soundUrls().chimeUrl, 1).catch(() => {
            usingCtxFallback = true;
            ctxChime();
        });
        vibrateOnce();
        ringTimer = setInterval(() => {
            if (usingCtxFallback) {
                ctxChime();
            }
            vibrateOnce();
            if (Date.now() - ringStarted > RING_MAX_MS) {
                stopRinging(false);
            }
        }, 2600);
        let flip = false;
        titleTimer = setInterval(() => {
            flip = !flip;
            document.title = flip ? "🔔 " + t("notif_title", { n: data.number }) : baseTitle;
        }, 1000);
    }

    function stopRinging(userAction) {
        if (ringTimer) {
            clearInterval(ringTimer);
            ringTimer = null;
        }
        if (titleTimer) {
            clearInterval(titleTimer);
            titleTimer = null;
        }
        document.title = baseTitle;
        $("trk").classList.remove("is-ringing");
        $("trk-ready-bar").hidden = true;
        if (navigator.vibrate) {
            navigator.vibrate(0);
        }
        const waiting = data.state === "received" || data.state === "preparing";
        if (keepAlive && waiting) {
            playerPlay(soundUrls().silentUrl, 1).catch(() => {});
        } else {
            stopAudio();
        }
        if (userAction) {
            ackReady();
        }
    }

    let acked = false;
    function ackReady() {
        if (acked || !(data.state === "ready" || data.state === "completed")) {
            return;
        }
        acked = true;
        fetch(BASE + "/ack", { method: "POST", credentials: "same-origin" }).catch(() => { acked = false; });
    }

    async function onReady() {
        startRinging();
        if (!document.hidden) {
            // The customer is looking at the page: no need to repeat the push.
            setTimeout(() => { if (!document.hidden) { ackReady(); } }, 15000);
        }
        // Local notification as a backup of the server push.
        try {
            if ("Notification" in window && Notification.permission === "granted" && document.hidden) {
                const reg = await navigator.serviceWorker.getRegistration("/track/");
                const title = t("notif_title", { n: data.number });
                const body = data.shop.ready_message || t("ready_s");
                if (reg) {
                    reg.showNotification(title, {
                        body, tag: "order-" + TOKEN + "-ready", renotify: true, requireInteraction: true,
                        silent: false, vibrate: [500, 200, 500, 200, 900],
                        data: { url: "/track/" + TOKEN, ack: BASE + "/ack", kind: "ready" },
                    });
                } else {
                    new Notification(title, { body });
                }
            }
        } catch (e) { /* ignore */ }
    }

    /* ---------------- permission & push ---------------- */
    const isIOS = /iP(hone|ad|od)/.test(navigator.userAgent) ||
        (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
    const isStandalone = window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
    const pushSupported = "serviceWorker" in navigator && "PushManager" in window && "Notification" in window && !!VAPID;
    let pushOk = false;

    function urlB64ToUint8Array(base64String) {
        const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
        const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
        const raw = atob(base64);
        return Uint8Array.from([...raw].map((c) => c.charCodeAt(0)));
    }

    async function subscribePush() {
        if (!pushSupported) {
            return false;
        }
        const reg = await navigator.serviceWorker.register("/track/sw.js", { scope: "/track/" });
        await navigator.serviceWorker.ready;
        let sub = await reg.pushManager.getSubscription();
        if (!sub) {
            sub = await reg.pushManager.subscribe({
                userVisibleOnly: true,
                applicationServerKey: urlB64ToUint8Array(VAPID),
            });
        }
        const res = await fetch(BASE + "/subscribe", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            credentials: "same-origin",
            body: JSON.stringify({ jsonrpc: "2.0", method: "call", id: Date.now(), params: { subscription: sub.toJSON(), lang } }),
        });
        const json = await res.json();
        return Boolean(json.result && json.result.ok);
    }

    async function keepAwake() {
        try {
            if ("wakeLock" in navigator && !wakeLock) {
                wakeLock = await navigator.wakeLock.request("screen");
                wakeLock.addEventListener("release", () => { wakeLock = null; });
            }
        } catch (e) { /* not allowed */ }
    }

    async function enableAlerts() {
        // Everything that needs a user gesture happens first, synchronously.
        startKeepAlive();
        unlockAudioCtx();
        if (navigator.vibrate) {
            navigator.vibrate(60);
        }
        const permissionPromise = ("Notification" in window && Notification.permission === "default")
            ? Notification.requestPermission() : Promise.resolve();
        alertsOn = true;
        $("trk-sheet").hidden = true;
        store.set("trk_sheet_" + TOKEN, "1");
        renderAlertsPanel();
        keepAwake();
        try {
            await permissionPromise;
            if ("Notification" in window && Notification.permission === "granted") {
                pushOk = await subscribePush();
            }
        } catch (e) { /* push not available: the in-page sound still works */ }
        renderAlertsPanel();
    }

    function renderAlertsPanel() {
        const txt = $("trk-alerts-text");
        const btn = $("trk-enable");
        const checks = $("trk-checks");
        const hint = $("trk-alerts-hint");
        if (!alertsOn) {
            txt.textContent = t("alerts_off");
            txt.classList.remove("is-on");
            btn.hidden = false;
            checks.hidden = true;
            hint.hidden = true;
            return;
        }
        txt.textContent = t("alerts_on");
        txt.classList.add("is-on");
        btn.hidden = true;
        checks.hidden = false;
        $("trk-check-sound").textContent = t("check_sound");
        const perm = "Notification" in window ? Notification.permission : "unsupported";
        const notif = $("trk-check-notif");
        notif.classList.toggle("is-off", !(perm === "granted" && pushOk));
        if (perm === "granted" && pushOk) {
            notif.textContent = t("check_notif_on");
        } else if (perm === "denied") {
            notif.textContent = t("check_notif_denied");
        } else if (isIOS && !isStandalone) {
            notif.textContent = t("check_notif_ios");
        } else {
            notif.textContent = t("check_notif_off");
        }
        hint.hidden = false;
        hint.textContent = isIOS ? t("hint_ios") : t("hint_android");
    }

    function maybeShowSheet() {
        const waiting = pagerOn() && (data.state === "received" || data.state === "preparing");
        $("trk-sheet").hidden = !(waiting && !alertsOn);
    }

    /* ---------------- wiring ---------------- */
    $("trk-lang").addEventListener("click", () => {
        lang = lang === "ar" ? "en" : "ar";
        store.set("trk_lang", lang);
        applyLang();
    });
    $("trk-enable").addEventListener("click", enableAlerts);
    $("trk-sheet-allow").addEventListener("click", enableAlerts);
    $("trk-stop").addEventListener("click", () => stopRinging(true));
    $("trk-nps-send").addEventListener("click", sendNps);
    // Any tap keeps audio unlocked (needed after the phone was locked on iOS).
    document.addEventListener("pointerdown", () => {
        if (alertsOn) {
            unlockAudioCtx();
            if (keepAlive && player && player.paused) {
                player.play().catch(() => {});
            }
        }
    }, { passive: true });
    document.addEventListener("visibilitychange", () => {
        if (!document.hidden) {
            poll();
            if (alertsOn) {
                keepAwake();
            }
            if (data.state === "ready" || data.state === "completed") {
                ackReady();
            }
        }
    });
    if ("serviceWorker" in navigator) {
        navigator.serviceWorker.addEventListener("message", (event) => {
            if (event.data && event.data.type === "tracking-push") {
                poll();
            }
        });
        if ("Notification" in window && Notification.permission === "granted") {
            navigator.serviceWorker.register("/track/sw.js", { scope: "/track/" }).catch(() => {});
        }
    }

    buildScale();
    applyLang();
    maybeShowSheet();
    startPolling();
})();
