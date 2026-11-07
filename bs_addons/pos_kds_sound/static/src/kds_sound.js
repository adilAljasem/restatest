/** @odoo-module ignore **/
/*
 * POS Kitchen Display Sound — صوت شاشة المطبخ
 *
 * Self-contained script (no Odoo JS imports) so it keeps working even if the
 * internal components of the kitchen display change between versions.
 * It watches the page for order cards; when their count goes up, a new
 * order has arrived and an alert sound plays.
 */
(function () {
    "use strict";
    if (window.__posKdsSoundLoaded) {
        return;
    }
    window.__posKdsSoundLoaded = true;

    const URL_HINT = /(prep|preparation|kitchen|kds|pdis)/i;
    const WARMUP_MS = 4000;          // ignore cards rendered while the screen loads
    const USER_ACTION_GRACE_MS = 1500; // ignore count changes caused by a click (filters, tabs)
    const CONFIG_REFRESH_MS = 60000;

    let cfg = null;
    let audioCtx = null;
    let audioEl = null;
    let unlocked = false;
    let muted = false;
    let lastCount = null;
    let activeSince = 0;
    let lastUserAction = 0;
    let alarmTimer = null;
    let checkTimer = null;
    let ui = null;

    try {
        muted = window.localStorage.getItem("pos_kds_sound_muted") === "1";
    } catch (e) {
        muted = false;
    }

    // ------------------------------------------------------------------ config
    async function loadConfig() {
        try {
            const res = await fetch("/pos_kds_sound/config", {
                credentials: "same-origin",
                headers: { Accept: "application/json" },
            });
            if (!res.ok) {
                return;
            }
            const next = await res.json();
            const urlChanged = !cfg || cfg.sound_url !== next.sound_url;
            cfg = next;
            if (urlChanged) {
                prepareAudioElement();
            }
        } catch (e) {
            // offline or not logged in: keep the previous config
        }
    }

    function prepareAudioElement() {
        audioEl = null;
        if (cfg && cfg.tone === "custom" && cfg.sound_url) {
            audioEl = new Audio(cfg.sound_url);
            audioEl.preload = "auto";
        }
    }

    // ------------------------------------------------------------- detection
    function safeQueryAll(selector) {
        if (!selector) {
            return [];
        }
        try {
            return document.querySelectorAll(selector);
        } catch (e) {
            return [];
        }
    }

    function isKdsPage() {
        if (!cfg) {
            return false;
        }
        if (safeQueryAll(cfg.root_selector).length) {
            return true;
        }
        const where = window.location.pathname + window.location.hash + window.location.search;
        return URL_HINT.test(where) && safeQueryAll(cfg.card_selector).length > 0;
    }

    function countCards() {
        return safeQueryAll(cfg.card_selector).length;
    }

    function check() {
        if (!cfg || !cfg.enabled || !isKdsPage()) {
            lastCount = null;
            activeSince = 0;
            hideUi();
            return;
        }
        showUi();
        const now = Date.now();
        if (!activeSince) {
            activeSince = now;
        }
        const count = countCards();
        const warmingUp = now - activeSince < WARMUP_MS;
        const userCaused = now - lastUserAction < USER_ACTION_GRACE_MS;
        if (lastCount !== null && count > lastCount && !warmingUp && !userCaused) {
            onNewOrder();
        }
        lastCount = count;
    }

    function scheduleCheck() {
        clearTimeout(checkTimer);
        checkTimer = setTimeout(check, 400);
    }

    // ----------------------------------------------------------------- sound
    function getCtx() {
        if (!audioCtx) {
            const Ctx = window.AudioContext || window.webkitAudioContext;
            if (!Ctx) {
                return null;
            }
            audioCtx = new Ctx();
        }
        return audioCtx;
    }

    function unlock() {
        const ctx = getCtx();
        if (ctx && ctx.state === "suspended") {
            ctx.resume().catch(() => {});
        }
        if (audioEl && !unlocked) {
            const v = audioEl.volume;
            audioEl.volume = 0;
            audioEl.play().then(() => {
                audioEl.pause();
                audioEl.currentTime = 0;
                audioEl.volume = v;
            }).catch(() => {
                audioEl.volume = v;
            });
        }
        if (ctx && ctx.state === "running") {
            unlocked = true;
        } else if (ctx) {
            ctx.resume().then(() => {
                unlocked = true;
                renderUi();
            }).catch(() => {});
        }
        renderUi();
    }

    function tone(ctx, freq, start, duration, type, gainValue) {
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = type;
        osc.frequency.setValueAtTime(freq, start);
        gain.gain.setValueAtTime(0.0001, start);
        gain.gain.exponentialRampToValueAtTime(Math.max(gainValue, 0.0002), start + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.0001, start + duration);
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.start(start);
        osc.stop(start + duration + 0.05);
    }

    function playSynth(kind, volume) {
        const ctx = getCtx();
        if (!ctx) {
            return;
        }
        if (ctx.state === "suspended") {
            ctx.resume().catch(() => {});
        }
        const t = ctx.currentTime + 0.05;
        const g = volume * 0.6;
        if (kind === "beep") {
            for (let i = 0; i < 3; i++) {
                tone(ctx, 1000, t + i * 0.22, 0.15, "square", g * 0.5);
            }
        } else if (kind === "bell") {
            tone(ctx, 660, t, 0.9, "sine", g);
            tone(ctx, 1320, t, 0.6, "sine", g * 0.3);
            tone(ctx, 990, t + 0.45, 1.0, "sine", g);
            tone(ctx, 1980, t + 0.45, 0.6, "sine", g * 0.3);
        } else {
            // chime
            [880, 1175, 1568].forEach((f, i) => tone(ctx, f, t + i * 0.16, 0.5, "triangle", g));
        }
    }

    function playOnce() {
        if (!cfg || muted) {
            return;
        }
        const volume = (cfg.volume ?? 80) / 100;
        if (cfg.tone === "custom" && audioEl) {
            try {
                audioEl.pause();
                audioEl.currentTime = 0;
                audioEl.volume = volume;
                audioEl.play().catch(() => playSynth("chime", volume));
            } catch (e) {
                playSynth("chime", volume);
            }
        } else {
            playSynth(cfg.tone, volume);
        }
    }

    function playAlert() {
        const times = Math.max(1, cfg.repeat || 1);
        const gap = cfg.tone === "custom" ? 2200 : 1300;
        for (let i = 0; i < times; i++) {
            setTimeout(playOnce, i * gap);
        }
    }

    function onNewOrder() {
        playAlert();
        flash();
        if (cfg.repeat_interval > 0 && !alarmTimer) {
            alarmTimer = setInterval(() => {
                if (!isKdsPage()) {
                    stopAlarm();
                    return;
                }
                playAlert();
                flash();
            }, cfg.repeat_interval * 1000);
        }
    }

    function stopAlarm() {
        if (alarmTimer) {
            clearInterval(alarmTimer);
            alarmTimer = null;
        }
    }

    // -------------------------------------------------------------------- ui
    function injectStyle() {
        if (document.getElementById("pos-kds-sound-style")) {
            return;
        }
        const style = document.createElement("style");
        style.id = "pos-kds-sound-style";
        style.textContent = `
            #pos-kds-sound-btn {
                position: fixed; left: 16px; bottom: 16px; z-index: 2147483000;
                display: flex; align-items: center; gap: 8px;
                padding: 10px 16px; border-radius: 999px; border: 0;
                font: 600 15px/1.2 system-ui, -apple-system, "Segoe UI", Tahoma, sans-serif;
                color: #fff; background: #1f6f43; cursor: pointer;
                box-shadow: 0 4px 14px rgba(0,0,0,.25);
            }
            #pos-kds-sound-btn.is-locked { background: #c0392b; animation: posKdsPulse 1.2s infinite; }
            #pos-kds-sound-btn.is-muted { background: #6b7280; }
            @keyframes posKdsPulse { 50% { transform: scale(1.06); } }
            #pos-kds-sound-flash {
                position: fixed; inset: 0; pointer-events: none; z-index: 2147482999;
                box-shadow: inset 0 0 0 10px #f39c12; opacity: 0; transition: opacity .25s;
            }
            #pos-kds-sound-flash.on { opacity: 1; }
        `;
        document.head.appendChild(style);
    }

    function showUi() {
        if (ui) {
            if (!ui.btn.isConnected) {
                document.body.appendChild(ui.btn);
                document.body.appendChild(ui.flash);
            }
            return;
        }
        injectStyle();
        const btn = document.createElement("button");
        btn.id = "pos-kds-sound-btn";
        btn.type = "button";
        btn.addEventListener("click", (ev) => {
            ev.stopPropagation();
            if (!unlocked) {
                unlock();
                muted = false;
                saveMuted();
                setTimeout(playOnce, 150);
            } else {
                muted = !muted;
                saveMuted();
                if (!muted) {
                    playOnce();
                }
            }
            renderUi();
        });
        const flashEl = document.createElement("div");
        flashEl.id = "pos-kds-sound-flash";
        document.body.appendChild(btn);
        document.body.appendChild(flashEl);
        ui = { btn, flash: flashEl };
        renderUi();
    }

    function hideUi() {
        if (ui && ui.btn.isConnected) {
            ui.btn.remove();
            ui.flash.remove();
        }
        stopAlarm();
    }

    function renderUi() {
        if (!ui) {
            return;
        }
        const btn = ui.btn;
        btn.classList.toggle("is-locked", !unlocked);
        btn.classList.toggle("is-muted", unlocked && muted);
        if (!unlocked) {
            btn.textContent = "🔇 اضغط لتفعيل صوت الطلبات";
        } else if (muted) {
            btn.textContent = "🔕 الصوت مكتوم";
        } else {
            btn.textContent = "🔔 الصوت مفعّل";
        }
    }

    function flash() {
        if (!ui) {
            return;
        }
        ui.flash.classList.add("on");
        setTimeout(() => ui && ui.flash.classList.remove("on"), 900);
    }

    function saveMuted() {
        try {
            window.localStorage.setItem("pos_kds_sound_muted", muted ? "1" : "0");
        } catch (e) {
            // storage unavailable: keep it in memory only
        }
    }

    // ------------------------------------------------------------------ boot
    function onUserAction(ev) {
        if (ui && ev.target === ui.btn) {
            return;
        }
        lastUserAction = Date.now();
        stopAlarm();
        if (!unlocked) {
            unlock();
        }
    }

    async function start() {
        await loadConfig();
        document.addEventListener("pointerdown", onUserAction, true);
        document.addEventListener("keydown", onUserAction, true);
        const observer = new MutationObserver(scheduleCheck);
        observer.observe(document.body, { childList: true, subtree: true });
        setInterval(check, 3000);
        setInterval(loadConfig, CONFIG_REFRESH_MS);
        window.addEventListener("hashchange", scheduleCheck);
        window.addEventListener("popstate", scheduleCheck);
        check();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", start);
    } else {
        start();
    }
})();
