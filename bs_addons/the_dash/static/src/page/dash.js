/* The Dash - page logic (Odoo 19).
 *
 * Two independent loaders, both manual:
 *   refreshNow()  -> /thedash/data    (KPIs, charts, tables)
 *   loadReport()  -> /thedash/report  (the multi-layer report)
 * Nothing else here talks to the server. There is no fetch on page load and
 * no polling: changing a filter marks the affected view "stale" and waits
 * for a click.
 *
 * The single setInterval is a pure UI clock that re-renders the "last updated
 * N minutes ago" labels. It never issues a network request.
 *
 * Scope: every point of sale by default. The scope panel narrows it; there is
 * no shop picker to get past first.
 */
(function () {
  "use strict";

  var BASE = "/thedash";

  var state = {
    period: "today",
    dfrom: "",
    dto: "",
    group1: "pos",
    group2: "none",
    branches: [],
    selBranches: [],
    teams: [],
    selTeams: [],
    loaded: false,
    busy: false,
    dirty: false,
    lastAt: null,        // Date of last successful load
    lastDuration: null,  // {server: ms, total: ms}
    lastTimings: null,
    lastData: null,
    prodCache: [],
    prodExpanded: false,
    dim: "pos",
    measures: [],        // [[key, label, kind], ...] rendered by the server
    columns: [],         // visible measure keys, persisted in localStorage
    dimsLoaded: false,
    // The report is loaded and refreshed on its own, independently of the
    // rest of the dashboard, because it is the expensive part.
    rpt: {
      loaded: false, busy: false, dirty: false,
      at: null, atLabel: "", duration: null, data: null
    }
  };

  var charts = {};
  // Series colours are read from the stylesheet so both themes stay coherent.
  function palette() {
    var cs = getComputedStyle(document.body);
    var accent = cs.getPropertyValue("--accent").trim();
    return [accent, cs.getPropertyValue("--info").trim(),
            cs.getPropertyValue("--pos").trim(), cs.getPropertyValue("--warn").trim(),
            cs.getPropertyValue("--neg").trim(), "#a78bfa", "#2dd4bf", "#fb923c"];
  }

  var TIMING_LABELS = {
    kpis: "المؤشرات", hourly: "المبيعات بالساعة", items: "الأصناف والمودفايرز",
    payments: "التحصيل", closing: "الإغلاقات والفروقات", breakdowns: "حسب الفرع والكاشير",
    journals: "دفاتر الدفع", aggregators: "التطبيقات", order_types: "أنواع الطلبات",
    kitchen: "وقت الخدمة", report: "التقرير متعدد الطبقات",
    pay_hours: "التحصيل بالساعة", profit: "ربحية الأصناف (الرسبي)", commissions: "عمولات التطبيقات"
  };

  function $(id) { return document.getElementById(id); }

  function fmt(n) {
    return new Intl.NumberFormat(undefined, {
      minimumFractionDigits: 0, maximumFractionDigits: 2
    }).format(n || 0);
  }

  function fmtCell(value, kind) {
    if (value === null || value === undefined) {
      return '<span class="na">—</span>';
    }
    var cls = value < 0 ? ' class="neg"' : "";
    if (kind === "pct") { return "<span" + cls + ">" + value + "%</span>"; }
    if (kind === "int") { return "<span" + cls + ">" + fmt(value) + "</span>"; }
    return "<span" + cls + ">" + fmt(value) + "</span>";
  }

  function esc(s) {
    return String(s === null || s === undefined ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function humanMs(ms) {
    if (ms === null || ms === undefined) { return ""; }
    return ms < 1000 ? ms + " مللي ثانية" : (ms / 1000).toFixed(1) + " ثانية";
  }

  function humanAge(seconds) {
    if (seconds < 60) { return "قبل " + Math.max(1, Math.round(seconds)) + " ثانية"; }
    if (seconds < 3600) { return "قبل " + Math.round(seconds / 60) + " دقيقة"; }
    return "قبل " + Math.floor(seconds / 3600) + " ساعة و" +
           Math.round((seconds % 3600) / 60) + " دقيقة";
  }

  /* ── Theme ─────────────────────────────────────────────────────────── */

  function themeColors() {
    var cs = getComputedStyle(document.body);
    return {
      accent: cs.getPropertyValue("--accent").trim(),
      pos: cs.getPropertyValue("--pos").trim(),
      text: cs.getPropertyValue("--text").trim(),
      muted: cs.getPropertyValue("--muted").trim(),
      border: cs.getPropertyValue("--line").trim(),
      card: cs.getPropertyValue("--card").trim(),
      warn: cs.getPropertyValue("--warn").trim(),
      info: cs.getPropertyValue("--info").trim(),
      neg: cs.getPropertyValue("--neg").trim()
    };
  }

  function axisOpts() {
    var t = themeColors();
    return {
      x: { ticks: { color: t.muted, font: { size: 10 } }, grid: { color: t.border } },
      y: { ticks: { color: t.muted, font: { size: 10 } }, grid: { color: t.border } }
    };
  }

  function legendOpts() {
    return { position: "right",
             labels: { color: themeColors().text, font: { size: 11 }, padding: 10 } };
  }

  function setTheme(t) {
    document.body.dataset.theme = t;
    try { localStorage.setItem("theDashTheme", t); } catch (e) { /* private mode */ }
    $("themeBtn").textContent = t === "dark" ? "☀" : "☾";
  }

  /* ── Status bar ────────────────────────────────────────────────────── */

  function setPill(cls, text) {
    var el = $("stState");
    el.className = "pill " + cls;
    el.textContent = text;
  }

  function markDirty() {
    if (!state.loaded) { return; }
    state.dirty = true;
    $("pendingNote").style.display = "block";
    $("refreshBtn").classList.add("attention");
  }

  function clearDirty() {
    state.dirty = false;
    $("pendingNote").style.display = "none";
    $("refreshBtn").classList.remove("attention");
  }

  function renderStatus() {
    var dur = $("stDuration");
    var stale = $("stStale");

    if (state.busy) {
      setPill("pill-busy", "جارٍ التحديث…");
      stale.textContent = "الاستعلام يعمل الآن على الخادم";
      return;
    }
    if (!state.loaded) {
      setPill("pill-idle", "لم يتم التحميل بعد");
      stale.textContent = "اضغط «تحديث اللوحة» لتشغيل الاستعلام";
      dur.textContent = "";
      return;
    }
    setPill("pill-ok", "محدَّث يدوياً");
    var age = (Date.now() - state.lastAt.getTime()) / 1000;
    stale.innerHTML = "آخر تحديث: <b>" + esc(state.lastAtLabel) + "</b> (" +
                      esc(humanAge(age)) + ")";
    dur.innerHTML = "مدة التحديث: <b>" + esc(humanMs(state.lastDuration.total)) +
                    "</b> <span style=\"opacity:.75\">(الخادم " +
                    esc(humanMs(state.lastDuration.server)) + ")</span>";
  }

  function renderTimings() {
    var box = $("stTimings");
    if (!state.lastTimings) { box.innerHTML = ""; return; }
    var rows = Object.keys(state.lastTimings)
      .map(function (k) { return [TIMING_LABELS[k] || k, state.lastTimings[k]]; })
      .sort(function (a, b) { return b[1] - a[1]; })
      .map(function (r) {
        return "<tr><td>" + esc(r[0]) + "</td><td>" + esc(humanMs(r[1])) + "</td></tr>";
      }).join("");
    box.innerHTML = "<table><tbody>" + rows + "</tbody></table>";
  }

  /* ── Data loading — only ever called from an explicit click ────────── */

  function periodSignature() {
    return [state.period, state.dfrom, state.dto,
            state.selBranches.join("|"), state.selTeams.join("|")].join("~");
  }

  function reportSignature() {
    return [periodSignature(), state.group1, state.group2].join("~");
  }

  function buildQS() {
    var q = "period=" + encodeURIComponent(state.period);
    if (state.period === "custom" && state.dfrom && state.dto) {
      q += "&date_from=" + encodeURIComponent(state.dfrom) +
           "&date_to=" + encodeURIComponent(state.dto);
    }
    q += "&group1=" + encodeURIComponent(state.group1);
    q += "&group2=" + encodeURIComponent(state.group2);
    if (state.selBranches.length) {
      q += "&config_ids=" + state.selBranches.join(",");
    }
    if (state.selTeams.length) { q += "&team_ids=" + state.selTeams.join(","); }
    return q;
  }

  function refreshNow() {
    if (state.busy) { return; }
    if (state.period === "custom") {
      state.dfrom = $("dateFrom").value;
      state.dto = $("dateTo").value;
      if (!state.dfrom || !state.dto) { alert("اختر تاريخ البداية والنهاية"); return; }
      if (state.dfrom > state.dto) { alert("تاريخ البداية بعد تاريخ النهاية"); return; }
      $("dRangeResult").textContent = state.dfrom + " → " + state.dto;
    }

    state.busy = true;
    var btn = $("refreshBtn");
    var idleBtn = $("idleBtn");
    btn.disabled = true;
    idleBtn.disabled = true;
    btn.textContent = "جارٍ التحديث…";
    renderStatus();

    var t0 = performance.now();

    fetch(BASE + "/data?" + buildQS(), { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.error) { throw new Error(d.error); }
        var total = Math.round(performance.now() - t0);

        state.lastData = d;
        renderAll(d);
        state.loaded = true;
        state.lastAt = new Date();
        state.lastAtLabel = d.last_updated;
        state.lastDuration = { server: d.duration_ms, total: total };
        state.lastTimings = d.timings;
        $("stPeriod").innerHTML = "الفترة: <b>" + esc(d.period_label) + "</b>";
        $("stDetailsBtn").style.display = "inline";
        $("idleCard").style.display = "none";
        $("content").style.display = "block";
        clearDirty();
        renderTimings();
        state.busy = false;
        renderStatus();
      })
      .catch(function (e) {
        setPill("pill-err", "فشل التحديث");
        $("stStale").textContent = "تعذر جلب البيانات: " + (e && e.message ? e.message : e);
      })
      .finally(function () {
        state.busy = false;
        btn.disabled = false;
        idleBtn.disabled = false;
        btn.textContent = "تحديث اللوحة";
      });
  }

  /* ── Report: its own load, its own status, its own failures ────────── */

  function setRptPill(cls, text) {
    var el = $("rptState");
    el.className = "pill " + cls;
    el.textContent = text;
  }

  function markRptDirty() {
    if (!state.rpt.loaded) { return; }
    if (reportSignature() === state.rpt.sig) { return; }
    state.rpt.dirty = true;
    $("rptPending").style.display = "block";
    $("rptRefreshBtn").classList.add("attention");
  }

  function renderRptStatus() {
    var r = state.rpt;
    if (r.busy) {
      setRptPill("pill-busy", "جارٍ الحساب…");
      $("rptStale").textContent = "الاستعلام يعمل الآن";
      return;
    }
    if (!r.loaded) {
      setRptPill("pill-idle", "لم يُحمَّل");
      $("rptStale").textContent =
        "للتقرير زر تحديث مستقل ولا يعمل مع تحديث اللوحة";
      $("rptDuration").textContent = "";
      return;
    }
    setRptPill("pill-ok", "محسوب");
    $("rptStale").innerHTML = "آخر حساب: <b>" + esc(r.atLabel) + "</b> (" +
      esc(humanAge((Date.now() - r.at.getTime()) / 1000)) + ")";
    $("rptDuration").innerHTML = "مدة الحساب: <b>" +
      esc(humanMs(r.duration.total)) + "</b> <span style=\"opacity:.75\">(الخادم " +
      esc(humanMs(r.duration.server)) + ")</span>";
  }

  function showRptError(detail, sql) {
    var box = $("rptError");
    box.style.display = "block";
    box.innerHTML = '<div class="err-title">تعذّر حساب التقرير</div>' +
      "<div>" + esc(detail || "خطأ غير معروف") + "</div>" +
      (sql ? '<button class="linkbtn" type="button" id="sqlToggle">عرض الاستعلام</button>' +
             '<pre id="sqlBox" style="display:none">' + esc(sql) + "</pre>" : "");
    if (sql) {
      $("sqlToggle").addEventListener("click", function () {
        var pre = $("sqlBox");
        var open = pre.style.display === "block";
        pre.style.display = open ? "none" : "block";
        this.textContent = open ? "عرض الاستعلام" : "إخفاء الاستعلام";
      });
    }
    setRptPill("pill-err", "فشل");
    $("rptStale").textContent = "لم يتغيّر شيء على الخادم - التقرير فقط لم يُحسب";
  }

  function loadReport() {
    if (state.rpt.busy) { return; }
    if (state.period === "custom") {
      state.dfrom = $("dateFrom").value;
      state.dto = $("dateTo").value;
      if (!state.dfrom || !state.dto) { alert("اختر تاريخ البداية والنهاية"); return; }
      if (state.dfrom > state.dto) { alert("تاريخ البداية بعد تاريخ النهاية"); return; }
      $("dRangeResult").textContent = state.dfrom + " → " + state.dto;
    }

    state.rpt.busy = true;
    var btn = $("rptRefreshBtn");
    btn.disabled = true;
    btn.textContent = "جارٍ الحساب…";
    $("rptError").style.display = "none";
    renderRptStatus();

    var t0 = performance.now();
    var sig = reportSignature();

    fetch(BASE + "/report?" + buildQS(), { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.error) {
          showRptError(d.detail || d.error, d.sql);
          return;
        }
        state.rpt.loaded = true;
        state.rpt.data = d.report;
        state.rpt.sig = sig;
        state.rpt.at = new Date();
        state.rpt.atLabel = d.last_updated;
        state.rpt.duration = {
          server: d.duration_ms,
          total: Math.round(performance.now() - t0)
        };
        state.rpt.dirty = false;
        $("rptPending").style.display = "none";
        btn.classList.remove("attention");
        $("rptPeriod").innerHTML = "الفترة: <b>" + esc(d.period_label) + "</b>";
        $("rptIdle").style.display = "none";
        $("rptContent").style.display = "block";
        buildColumnPicker(d.report.unavailable);
        renderReport(d.report);
        state.rpt.busy = false;
        renderRptStatus();
      })
      .catch(function (e) {
        showRptError(e && e.message ? e.message : String(e));
      })
      .finally(function () {
        state.rpt.busy = false;
        btn.disabled = false;
        btn.textContent = "تحديث التقرير";
      });
  }

  /* ── Renderers ─────────────────────────────────────────────────────── */

  function renderAll(d) {
    renderKPIs(d.kpis);
    renderHours(d);
    renderTypes(d);
    renderTrend(d);
    renderTop(d.top_products);
    renderCats(d.categories);
    renderProducts(d.top_products);
    renderSales(d.sales);
    renderProfit(d.profit);
    renderMenu(d);
    renderCommissions(d);
    renderCollections(d.collections);
    renderAggregators(d.aggregators);
    renderOrderTypes(d.order_types);
    renderDims();
    renderClosing(d.closing);
    renderKitchen(d.kitchen);
  }

  /* ── Tiles and small tables ─────────────────────────────────────────── */

  function stat(label, value, note, cls) {
    return '<div class="stat' + (cls ? " " + cls : "") + '"><div class="stat-lbl">' + label +
      '</div><div class="stat-val">' + value + "</div>" +
      (note ? '<div class="stat-note">' + note + "</div>" : "") + "</div>";
  }

  function signed(v) {
    if (!v) { return '<span class="na">0</span>'; }
    return '<span class="' + (v < 0 ? "neg" : "posv") + '">' + (v > 0 ? "+" : "") + fmt(v) + "</span>";
  }

  function table(head, rows, foot) {
    return '<table class="dtable"><thead><tr>' + head.map(function (h) {
      return "<th>" + h + "</th>";
    }).join("") + "</tr></thead><tbody>" + (rows.length ? rows.map(function (r) {
      return "<tr>" + r.map(function (c) { return "<td>" + c + "</td>"; }).join("") + "</tr>";
    }).join("") : '<tr><td colspan="' + head.length + '" class="empty">لا توجد بيانات</td></tr>') +
      "</tbody>" + (foot ? "<tfoot><tr>" + foot.map(function (c) {
        return "<td>" + c + "</td>";
      }).join("") + "</tr></tfoot>" : "") + "</table>";
  }

  /* ── Items and modifiers ────────────────────────────────────────────── */

  function renderSales(s) {
    if (!s) { return; }
    var t = s.totals;
    $("salesStats").innerHTML =
      stat("الأصناف", fmt(t.items), fmt(t.items_qty) + " قطعة") +
      stat("المودفايرز", fmt(t.modifiers), fmt(t.modifiers_qty) + " قطعة · " + t.modifier_share + "%") +
      stat("الإجمالي", fmt(t.all), "الأصناف + المودفايرز", "stat-accent");
    if (!s.modifier_source) {
      $("modBody").innerHTML = '<tr><td colspan="5" class="empty">لا يوجد موديول مودفايرز مثبت — ' +
        "الخيارات (الأتربيوت) تحت.</td></tr>";
    } else {
      $("modBody").innerHTML = s.modifiers.length ? s.modifiers.map(function (m, i) {
        return "<tr><td>" + (i + 1) + "</td><td>" + esc(m.name) + "</td><td>" + fmt(m.qty) +
          "</td><td><strong>" + fmt(m.revenue) + "</strong></td><td>" + m.pct + "%</td></tr>";
      }).join("") : '<tr><td colspan="5" class="empty">لا توجد مودفايرز في هذه الفترة</td></tr>';
    }
    var attrs = s.attributes || [];
    $("attrBox").innerHTML = attrs.length
      ? '<div class="sub-title mt10">الخيارات (الأتربيوت) — قيمتها داخل سعر الصنف</div>' +
        table(["الخيار", "الكمية", "قيمة الإضافة"], attrs.map(function (a) {
          return [esc(a.name), fmt(a.qty), a.extra ? fmt(a.extra) : '<span class="na">—</span>'];
        }))
      : "";
  }

  /* ── Collections: cash / bank / apps ────────────────────────────────── */

  function renderCollections(c) {
    if (!c) { return; }
    var cls = { cash: "stat-cash", bank: "stat-bank", app: "stat-app", credit: "" };
    $("colStats").innerHTML = c.kinds.map(function (k) {
      return stat(k.name, fmt(k.amount), k.pct + "%", cls[k.key]);
    }).join("") + stat("إجمالي التحصيل", fmt(c.total), "", "stat-accent");
    $("appTable").innerHTML = table(["التطبيق", "الطلبات", "المبلغ", "%", "نقدي", "بنك"],
      c.apps.map(function (a) {
        return [esc(a.name), fmt(a.orders), "<strong>" + fmt(a.amount) + "</strong>",
                a.pct + "%", fmt(a.cash), fmt(a.bank)];
      }));
    var kinds = c.kinds.filter(function (k) { return k.amount; });
    pie("kindChart", "kinds", kinds.map(function (k) { return k.name; }),
        kinds.map(function (k) { return k.amount; }),
        kinds.map(function (k) { return kindColor(k.key); }));
    hbar("methodChart", "methods", c.methods.slice(0, 10).map(function (m) { return m.name; }),
         [{ label: "المبلغ", data: c.methods.slice(0, 10).map(function (m) { return m.amount; }),
            backgroundColor: c.methods.slice(0, 10).map(function (m) { return alpha(kindColor(m.kind), "CC"); }),
            borderRadius: 4 }]);
    hbar("appChart", "apps", c.apps.map(function (a) { return a.name; }),
         [{ label: "نقدي", data: c.apps.map(function (a) { return a.cash; }),
            backgroundColor: alpha(kindColor("cash"), "CC"), borderRadius: 4 },
          { label: "بنك / شبكة", data: c.apps.map(function (a) { return a.bank; }),
            backgroundColor: alpha(kindColor("bank"), "CC"), borderRadius: 4 }],
         { stacked: true, legend: true, empty: "لا توجد طلبات تطبيقات في هذه الفترة" });
    $("methodTable").innerHTML = table(["طريقة الدفع", "النوع", "المبلغ", "منها تطبيقات", "%"],
      c.methods.map(function (m) {
        return [esc(m.name), esc(m.kind_label), "<strong>" + fmt(m.amount) + "</strong>",
                m.app_amount ? fmt(m.app_amount) : '<span class="na">—</span>', m.pct + "%"];
      }));
  }

  /* ── By POS / cost center / region / cashier ────────────────────────── */

  var DIM_NOTES = {
    pos: "", cost_center: "مركز التكلفة من إعدادات نقطة البيع (Cost Center (The Dash)).",
    region: "المنطقة من إعدادات نقطة البيع (Region (The Dash)) — The Dash ← Configuration ← Regions.",
    cashier: ""
  };

  function renderDims() {
    var b = state.lastData && state.lastData.breakdowns;
    if (!b) { return; }
    var rows = b[state.dim] || [];
    var keys = ["orders", "sales", "refunds", "cash", "bank", "app", "credit", "sessions",
                "expected_cash", "counted_cash", "shortage", "overage", "difference", "bank_difference"];
    var head = ["الاسم", "الطلبات", "المبيعات", "المرتجعات", "نقدي", "بنك", "تطبيقات", "آجل",
                "الجلسات", "النقد المتوقع", "النقد المعدود", "العجز", "الزيادة", "صافي فرق النقد",
                "فرق غير نقدي"];
    var total = {};
    keys.forEach(function (k) {
      total[k] = rows.reduce(function (a, r) { return a + (r[k] || 0); }, 0);
    });
    function cells(r) {
      return keys.map(function (k) {
        if (k === "shortage") { return r[k] ? '<span class="neg">' + fmt(r[k]) + "</span>" : '<span class="na">0</span>'; }
        if (k === "overage") { return r[k] ? '<span class="posv">' + fmt(r[k]) + "</span>" : '<span class="na">0</span>'; }
        if (k === "difference" || k === "bank_difference") { return signed(r[k]); }
        return fmt(r[k]);
      });
    }
    $("dimTable").innerHTML = "<thead><tr>" + head.map(function (h) { return "<th>" + h + "</th>"; }).join("") +
      "</tr></thead><tbody>" + (rows.length ? rows.map(function (r) {
        return "<tr><td><strong>" + esc(r.name) + "</strong></td>" +
          cells(r).map(function (c) { return '<td class="num">' + c + "</td>"; }).join("") + "</tr>";
      }).join("") : '<tr><td colspan="' + head.length + '" class="empty">لا توجد بيانات</td></tr>') +
      "</tbody><tfoot><tr><td>الإجمالي</td>" + cells(total).map(function (c) {
        return '<td class="num">' + c + "</td>";
      }).join("") + "</tr></tfoot>";
    var note = DIM_NOTES[state.dim] || "";
    if (state.dim === "cashier") {
      note = (b.cashier_source === "employee" ? "الكاشير = موظف نقطة البيع (pos_hr). " : "الكاشير = المستخدم. ") +
        "فروقات الإغلاق تُنسب لكاشير الجلسة (صاحب أكثر الطلبات فيها).";
    }
    $("dimNote").textContent = note;
  }

  /* ── Closing: shortages and overages ────────────────────────────────── */

  function renderClosing(c) {
    if (!c) { return; }
    var t = c.totals;
    $("closeStats").innerHTML =
      stat("الجلسات", fmt(t.sessions), t.open ? t.open + " غير مغلقة" : "كلها مغلقة") +
      stat("النقد المتوقع", fmt(t.expected), "") +
      stat("النقد المعدود", fmt(t.counted), "") +
      stat("العجز", fmt(t.shortage), "", t.shortage ? "stat-neg" : "") +
      stat("الزيادة", fmt(t.overage), "", t.overage ? "stat-pos" : "") +
      stat("صافي الفروقات", (t.net > 0 ? "+" : "") + fmt(t.net),
           t.with_difference + " جلسة فيها فرق", t.net < 0 ? "stat-neg" : (t.net > 0 ? "stat-pos" : "")) +
      stat("منها فرق غير نقدي", (t.bank_difference > 0 ? "+" : "") + fmt(t.bank_difference), "");
    drawShortages();
    var sessions = c.sessions.slice().sort(function (a, b) {
      return (Math.abs(b.difference) + Math.abs(b.bank_difference)) -
             (Math.abs(a.difference) + Math.abs(a.bank_difference));
    });
    $("closeTable").innerHTML = "<thead><tr><th>الجلسة</th><th>نقطة البيع</th><th>الكاشير</th>" +
      "<th>الحالة</th><th>رصيد البداية</th><th>النقد المتوقع</th><th>النقد المعدود</th>" +
      "<th>فرق النقد</th><th>فرق غير نقدي</th></tr></thead><tbody>" +
      (sessions.length ? sessions.map(function (x) {
        var open = x.state !== "closed";
        return "<tr><td><strong>" + esc(x.name) + "</strong></td><td>" + esc(x.pos) + "</td><td>" +
          esc(x.cashier) + "</td><td>" + esc(x.state_label) + "</td><td class=\"num\">" + fmt(x.opening) +
          '</td><td class="num">' + (open ? '<span class="na">—</span>' : fmt(x.expected)) +
          '</td><td class="num">' + (open ? '<span class="na">—</span>' : fmt(x.counted)) +
          '</td><td class="num">' + signed(x.difference) + '</td><td class="num">' +
          signed(x.bank_difference) + "</td></tr>";
      }).join("") : '<tr><td colspan="9" class="empty">لا توجد جلسات في هذه الفترة</td></tr>') +
      "</tbody>";
  }

  function delta(v) {
    if (v === null || v === undefined) { return ""; }
    return '<span class="kpi-gr ' + (v >= 0 ? "up" : "dn") + '">' +
      (v >= 0 ? "▲" : "▼") + " " + Math.abs(v) + "%</span>";
  }

  function kpi(label, value, growthPct, prev) {
    return '<div class="kpi-card">' +
      '<div class="kpi-lbl">' + label + "</div>" +
      '<div class="kpi-val">' + value + "</div>" +
      '<div class="kpi-foot">' + delta(growthPct) +
      (prev !== undefined && prev !== null
        ? '<span class="kpi-prev">سابقاً ' + prev + "</span>" : "") +
      "</div></div>";
  }

  function renderKPIs(k) {
    $("kpiGrid").innerHTML =
      kpi("صافي المبيعات", fmt(k.total_sales), k.sales_growth, fmt(k.prev_sales)) +
      kpi("الطلبات", fmt(k.total_orders), k.orders_growth, fmt(k.prev_orders)) +
      kpi("العملاء", fmt(k.total_customers), k.customers_growth, fmt(k.prev_customers)) +
      kpi("متوسط الطلب", fmt(k.avg_order), k.avg_growth, fmt(k.prev_avg)) +
      kpi("المرتجعات", fmt(k.refund_amount), null, k.refund_count + " طلب") +
      kpi("الطلبات الملغية", fmt(k.cancel_amount), null, k.cancel_count + " طلب");
  }

  function destroy(name) {
    if (charts[name]) { charts[name].destroy(); charts[name] = null; }
  }

  function renderCats(cats) {
    pie("catChart", "cats", (cats || []).map(function (c) { return c.name; }),
        (cats || []).map(function (c) { return c.sales; }), palette());
  }

  function renderProducts(ps) { state.prodCache = ps || []; drawProducts(); }

  function drawProducts() {
    var body = $("topBody");
    var btn = $("moreProdBtn");
    if (!state.prodCache.length) {
      body.innerHTML = '<tr><td colspan="6" class="empty">لا توجد بيانات</td></tr>';
      btn.style.display = "none";
      return;
    }
    var list = state.prodCache.slice(0, state.prodExpanded ? 100 : 25);
    body.innerHTML = list.map(function (p, i) {
      // Rows are product templates; say so when variants were merged.
      var note = p.variants > 1
        ? ' <span class="na" style="font-size:.75rem">(' + p.variants + " متغيرات)</span>"
        : "";
      return '<tr><td><span class="rank r' + (i + 1) + '">' + (i + 1) +
             "</span></td><td>" + esc(p.name) + note + '</td><td class="muted">' +
             esc(p.category || "") + "</td><td>" + fmt(p.qty) +
             "</td><td><strong>" + fmt(p.revenue) + "</strong></td><td>" +
             (p.pct !== undefined ? p.pct + "%" : "") + "</td></tr>";
    }).join("");
    btn.style.display = state.prodCache.length > 25 ? "inline-block" : "none";
    btn.textContent = state.prodExpanded ? "عرض أقل (25)" : "عرض المزيد (100)";
  }

  function renderPayments(payments) {
    var el = $("payBars");
    if (!payments || !payments.length) {
      el.innerHTML = '<div class="empty">لا توجد بيانات</div>';
      return;
    }
    var pal = palette();
    el.innerHTML = payments.map(function (p, i) {
      return '<div class="pay-wrap"><div class="pay-lbl"><span>' + esc(p.name) +
             "</span><span><strong>" + fmt(p.amount) + "</strong> (" + p.pct +
             '%)</span></div><div class="pay-bar"><div class="pay-fill" style="width:' +
             p.pct + "%;background:" + pal[i % pal.length] + '"></div></div></div>';
    }).join("");
  }

  function breakdownTable(rows, head) {
    return '<table class="dtable"><thead><tr><th>' + head +
      "</th><th>الطلبات</th><th>المبيعات</th><th>%</th></tr></thead><tbody>" +
      rows.map(function (r) {
        return "<tr><td>" + esc(r.name) + "</td><td>" + r.orders +
               "</td><td><strong>" + fmt(r.sales) + "</strong></td><td>" + r.pct + "%</td></tr>";
      }).join("") + "</tbody></table>";
  }

  function doughnut(canvasId, chartKey, rows) {
    pie(canvasId, chartKey, rows.map(function (r) { return r.name; }),
        rows.map(function (r) { return r.sales; }), palette());
  }

  function renderAggregators(rows) {
    var card = $("aggCard");
    if (!rows || !rows.length) { card.style.display = "none"; return; }
    card.style.display = "block";
    doughnut("aggChart", "agg", rows);
    $("aggTable").innerHTML = breakdownTable(rows, "التطبيق");
  }

  function renderOrderTypes(rows) {
    var card = $("otCard");
    var act = state.lastData && state.lastData.activity;
    if (act && act.type_source === "order_type") { rows = null; }  // shown in the order-type card
    if (!rows || !rows.length) { card.style.display = "none"; return; }
    card.style.display = "block";
    doughnut("otChart", "ot", rows);
    $("otTable").innerHTML = breakdownTable(rows, "نوع الطلب");
  }

  function renderKitchen(k) {
    var card = $("kitchenCard");
    if (!k) { card.style.display = "none"; return; }
    card.style.display = "block";
    $("kitchenSource").textContent = k.source ? "المصدر: " + k.source : "";
    var box = $("kitchenBox");
    drawKitchenCharts(k);
    if (!k.count) {
      box.innerHTML = '<div class="empty">لا توجد طلبات منتهية في شاشة المطبخ في هذه الفترة' +
        (k.pending ? " (" + k.pending + " قيد التحضير)" : "") + "</div>";
      return;
    }
    if (k.by_pos === undefined) {
      // Older displays: average only.
      k = { count: k.count, avg: k.avg, median: k.avg, p90: k.avg, max: k.avg, late: 0, late_pct: 0,
            late_minutes: 15, by_pos: k.by_branch || [], by_display: [], by_hour: [], slowest: [] };
    }
    function rowsOf(list) {
      return list.map(function (b) {
        return [esc(b.name), fmt(b.count), "<strong>" + b.avg + "</strong>", b.median, b.p90, b.max,
                b.late ? '<span class="neg">' + b.late + " (" + b.late_pct + "%)</span>" : "0"];
      });
    }
    var head = ["", "الطلبات", "المتوسط", "الوسيط", "90% خلال", "الأطول", "متأخرة"];
    box.innerHTML =
      '<div class="stat-grid">' +
      stat("متوسط وقت الخدمة", k.avg + " د", "من وصول الطلب للشاشة حتى آخر صنف", "stat-accent") +
      stat("الوسيط", k.median + " د", "نصف الطلبات أسرع من هذا") +
      stat("90% من الطلبات خلال", k.p90 + " د", "") +
      stat("الأطول", k.max + " د", "") +
      stat("متأخرة (أكثر من " + k.late_minutes + " د)", fmt(k.late), k.late_pct + "%",
           k.late ? "stat-neg" : "") +
      stat("طلبات مقاسة", fmt(k.count), k.pending ? k.pending + " قيد التحضير" : "") +
      "</div>" +
      '<div class="bottom-grid nomb">' +
      "<div><div class=\"sub-title\">حسب نقطة البيع</div>" + table(["نقطة البيع"].concat(head.slice(1)), rowsOf(k.by_pos)) + "</div>" +
      "<div><div class=\"sub-title\">حسب الشاشة</div>" + table(["الشاشة"].concat(head.slice(1)), rowsOf(k.by_display)) + "</div>" +
      "</div>" +
      '<div class="bottom-grid nomb mt10">' +
      "<div><div class=\"sub-title\">حسب ساعة وصول الطلب</div>" + table(["الساعة"].concat(head.slice(1)), rowsOf(k.by_hour)) + "</div>" +
      "<div><div class=\"sub-title\">أبطأ الطلبات</div>" + table(["الطلب", "نقطة البيع", "الشاشة", "الدقائق"],
        (k.slowest || []).map(function (x) {
          return [esc(x.order), esc(x.pos), esc(x.display), '<strong>' + x.minutes + "</strong>"];
        })) + "</div>" +
      "</div>";
  }

  function renderTotalsStrip(r) {
    var t = r.totals || {};
    var na = r.unavailable || [];
    var chips = [
      ["net", "صافي المبيعات"], ["gross", "إجمالي المبيعات"],
      ["discount", "مبلغ الخصم"], ["tax", "الضرائب"],
      ["cost", "التكلفة"], ["profit", "الربح"],
      ["orders", "عدد الطلبات"], ["customers", "عدد العملاء"],
      ["visitors", "عدد الزوار"], ["avg_order", "متوسط الطلب"],
      ["avg_visitor", "المتوسط للزائر"], ["tips", "الإكراميات"]
    ];
    $("rptTotals").innerHTML = chips.filter(function (c) {
      return na.indexOf(c[0]) === -1;
    }).map(function (c) {
      return '<div class="tot-chip"><div class="v">' + fmt(t[c[0]]) +
        '</div><div class="l">' + c[1] + "</div></div>";
    }).join("");
  }

  function visibleMeasures() {
    return state.measures.filter(function (m) {
      return state.columns.indexOf(m[0]) !== -1;
    });
  }

  function renderReport(r) {
    if (!r) { return; }
    var cols = visibleMeasures();
    var na = r.unavailable || [];

    $("fanNote").style.display = r.fans ? "block" : "none";
    $("truncNote").style.display = r.truncated ? "block" : "none";
    $("rptHint").textContent = r.rows.length + " مجموعة";
    renderTotalsStrip(r);

    // header
    $("rptHead").innerHTML = "<th>المجموعة</th>" + cols.map(function (m) {
      var dim = na.indexOf(m[0]) !== -1 ? ' class="na"' : "";
      return "<th" + dim + ">" + esc(m[1]) + "</th>";
    }).join("");

    if (!r.rows.length) {
      $("rptBody").innerHTML = '<tr><td colspan="' + (cols.length + 1) +
        '" class="empty">لا توجد بيانات</td></tr>';
      $("rptFoot").innerHTML = "";
      destroy("rpt");
      return;
    }

    function cells(row) {
      return cols.map(function (m) {
        var v = na.indexOf(m[0]) !== -1 ? null : row[m[0]];
        return '<td class="num">' + fmtCell(v, m[2]) + "</td>";
      }).join("");
    }

    var html = "";
    r.rows.forEach(function (row, i) {
      var hasCh = row.children && row.children.length > 0;
      html += '<tr class="rpt-row1" data-idx="' + i + '"><td><strong>' +
        esc(row.label) + "</strong>" +
        (hasCh ? '<span class="caret" id="car' + i + '">▶</span>' : "") +
        "</td>" + cells(row) + "</tr>";
      (row.children || []).forEach(function (ch) {
        html += '<tr class="rpt-row2 rpt-ch' + i + '" style="display:none"><td>↳ ' +
          esc(ch.label) + "</td>" + cells(ch) + "</tr>";
      });
    });
    $("rptBody").innerHTML = html;
    $("rptFoot").innerHTML = "<td>الإجمالي</td>" + cells(r.totals);

    destroy("rpt");
    var t = themeColors();
    charts.rpt = new Chart($("rptChart"), {
      type: "bar",
      data: {
        labels: r.rows.slice(0, 40).map(function (x) { return x.label; }),
        datasets: [{
          label: "صافي المبيعات",
          data: r.rows.slice(0, 40).map(function (x) { return x.net; }),
          backgroundColor: t.accent + "99", borderColor: t.accent,
          borderWidth: 1, borderRadius: 4
        }]
      },
      options: baseOpts()
    });
  }

  /* ── Chart kit (business-day release) ───────────────────────────────
   * Every chart below is drawn from the payload already in memory. The
   * view switches (metric, split, range...) repaint locally and never call
   * the server; the choice is remembered per browser.
   */

  var VIEW = { hrMetric: "sales", hrSplit: "total", hrRange: "work",
               typeMetric: "sales", trendSplit: "type", shortDim: "by_pos",
               profitView: "all", menuScope: "all", varView: "all" };
  var TYPE_ORDER = ["dine_in", "takeaway", "delivery", "app", "other"];
  var KIND_ORDER = ["cash", "bank", "app", "credit"];
  var KIND_NAMES = { cash: "نقدي", bank: "بنك / شبكة", app: "تطبيقات التوصيل",
                     credit: "آجل / حساب عميل" };

  function loadView() {
    try {
      var v = JSON.parse(localStorage.getItem("theDashView") || "{}");
      Object.keys(VIEW).forEach(function (k) { if (v[k]) { VIEW[k] = v[k]; } });
    } catch (e) { /* private mode */ }
  }

  function saveView() {
    try { localStorage.setItem("theDashView", JSON.stringify(VIEW)); }
    catch (e) { /* private mode */ }
  }

  function syncSegs() {
    Array.prototype.forEach.call(document.querySelectorAll(".seg[data-pref]"), function (seg) {
      var v = VIEW[seg.getAttribute("data-pref")];
      Array.prototype.forEach.call(seg.querySelectorAll("button"), function (b) {
        b.classList.toggle("on", b.getAttribute("data-v") === v);
      });
    });
  }

  function alpha(color, a) {
    return /^#[0-9a-f]{6}$/i.test(color) ? color + a : color;
  }

  function typeColor(k) {
    var t = themeColors();
    return { dine_in: t.accent, takeaway: t.warn, delivery: t.info,
             app: "#a78bfa", other: t.muted }[k] || t.muted;
  }

  function kindColor(k) {
    var t = themeColors();
    return { cash: t.pos, bank: t.info, app: t.warn, credit: "#a78bfa" }[k] || t.muted;
  }

  function fmtK(v) {
    v = v || 0;
    if (Math.abs(v) >= 1000) { return (v / 1000).toFixed(Math.abs(v) >= 10000 ? 0 : 1) + "k"; }
    return fmt(Math.round(v));
  }

  function short(s, n) {
    s = String(s || "");
    return s.length > n ? s.slice(0, n - 1) + "…" : s;
  }

  function merge(a, b) {
    Object.keys(b || {}).forEach(function (k) {
      if (b[k] && typeof b[k] === "object" && !Array.isArray(b[k]) &&
          a[k] && typeof a[k] === "object" && !Array.isArray(a[k])) {
        merge(a[k], b[k]);
      } else {
        a[k] = b[k];
      }
    });
    return a;
  }

  function tipLabel(c) {
    var horizontal = c.chart.options.indexAxis === "y";
    var v = c.chart.config.type === "doughnut" ? c.parsed
      : (horizontal ? c.parsed.x : c.parsed.y);
    if (v === null || v === undefined) { return null; }
    var name = c.dataset.label || c.label || "";
    var suffix = c.dataset.suffix || "";
    if (c.chart.config.type === "doughnut") {
      var total = c.dataset.data.reduce(function (a, x) { return a + (x || 0); }, 0);
      suffix = total ? " (" + (v / total * 100).toFixed(1) + "%)" : "";
    }
    return name + ": " + fmt(Math.round(v * 100) / 100) + suffix;
  }

  function baseOpts(extra) {
    var t = themeColors();
    var o = {
      responsive: true, maintainAspectRatio: false, animation: { duration: 300 },
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { display: false, rtl: true, position: "top", align: "start",
                  labels: { color: t.text, boxWidth: 11, boxHeight: 11, padding: 12,
                            font: { size: 11 } } },
        tooltip: { rtl: true, textDirection: "rtl", callbacks: { label: tipLabel } }
      },
      scales: axisOpts()
    };
    return merge(o, extra || {});
  }

  function emptyState(canvasId, msg) {
    var box = $(canvasId).parentNode;
    var el = box.querySelector(".chart-empty");
    if (!msg) { if (el) { el.remove(); } return; }
    if (!el) {
      el = document.createElement("div");
      el.className = "chart-empty";
      box.appendChild(el);
    }
    el.textContent = msg;
  }

  function hasData(datasets) {
    return datasets.some(function (ds) {
      return (ds.data || []).some(function (v) { return v; });
    });
  }

  function draw(canvasId, key, config, emptyMsg) {
    destroy(key);
    var ok = hasData(config.data.datasets);
    emptyState(canvasId, ok ? null : (emptyMsg || "لا توجد بيانات"));
    if (!ok) { return; }
    charts[key] = new Chart($(canvasId), config);
  }

  function pie(canvasId, key, labels, values, colors, emptyMsg) {
    var opts = baseOpts({
      cutout: "62%", interaction: { mode: "nearest", intersect: true },
      plugins: { legend: merge(legendOpts(), { display: true, rtl: true, position: "bottom" }) }
    });
    delete opts.scales;   // a doughnut has no axes
    draw(canvasId, key, {
      type: "doughnut",
      data: { labels: labels, datasets: [{
        data: values, backgroundColor: colors, borderWidth: 2,
        borderColor: themeColors().card, hoverOffset: 6 }] },
      options: opts
    }, emptyMsg);
  }

  /* Horizontal bars grow from the right, as an Arabic reader expects. */
  function hbar(canvasId, key, labels, datasets, o) {
    o = o || {};
    var ax = axisOpts();
    ax.x.reverse = true;
    ax.x.ticks.callback = function (v) { return fmtK(v); };
    ax.y.position = "right";
    ax.y.grid = { display: false };
    ax.y.ticks.callback = function (v) { return short(this.getLabelForValue(v), 24); };
    if (o.stacked) { ax.x.stacked = true; ax.y.stacked = true; }
    draw(canvasId, key, {
      type: "bar",
      data: { labels: labels, datasets: datasets },
      options: baseOpts({ indexAxis: "y", scales: ax,
                          plugins: { legend: { display: !!o.legend } } })
    }, o.empty);
  }

  function workRange(hours) {
    var first = -1, last = -1;
    (hours || []).forEach(function (h, i) {
      if (h.orders || h.sales) { if (first < 0) { first = i; } last = i; }
    });
    return first < 0 ? [0, Math.max(0, (hours || []).length - 1)] : [first, last];
  }

  function presentTypes(a) {
    var have = {};
    (a.types || []).forEach(function (t) { have[t.key] = true; });
    return TYPE_ORDER.filter(function (k) { return have[k]; });
  }

  function typeName(a, k) { return (a.type_labels || {})[k] || k; }

  /* ── Hours: business-day order ──────────────────────────────────────── */

  function renderHours(d) {
    var a = d.activity || { hours: d.hourly_sales || [], types: [] };
    var bd = d.business_day || {};
    $("bdNote").innerHTML = bd.source
      ? "اليوم التشغيلي يبدأ <b>" + esc(bd.start_label) + "</b> ويستمر حتى نفس الساعة من اليوم التالي — " +
        "حسب إعداد Business Day (" + esc(bd.tz) + ")"
      : "موديول Business Day غير مثبت: اليوم يبدأ 00:00 (" + esc(bd.tz || "") + ")";
    var peak = a.peak;
    $("hoursStats").innerHTML =
      stat("بداية اليوم التشغيلي", esc(bd.start_label || "00:00"), bd.source ? "من Business Day" : "") +
      stat("أول طلب", esc(a.first_order || "—"), "") +
      stat("آخر طلب", esc(a.last_order || "—"), "", "stat-accent") +
      stat("ساعة الذروة", peak ? esc(peak.hour) : "—",
           peak ? fmt(peak.sales) + " · " + fmt(peak.orders) + " طلب" : "") +
      stat("ساعات فيها طلبات", fmt(a.active_hours || 0), "من 24 ساعة");
    drawHours();
  }

  function drawHours() {
    var d = state.lastData;
    if (!d || !d.activity) { return; }
    var a = d.activity, t = themeColors();
    var rng = VIEW.hrRange === "work" ? workRange(a.hours) : [0, a.hours.length - 1];
    var hs = a.hours.slice(rng[0], rng[1] + 1);
    var ps = ((d.pay_hours || {}).hours || []).slice(rng[0], rng[1] + 1);
    var labels = hs.map(function (h) { return h.hour; });
    var metric = VIEW.hrMetric, split = VIEW.hrSplit;
    var ds = [], stacked = false, y1 = false, note = "";

    if (split === "type") {
      presentTypes(a).forEach(function (k) {
        var c = typeColor(k);
        if (metric === "avg") {
          ds.push({ type: "line", label: typeName(a, k), borderColor: c, backgroundColor: c,
                    tension: .3, pointRadius: 2, spanGaps: true,
                    data: hs.map(function (h) {
                      var n = h.orders_by_type[k];
                      return n ? Math.round(h.by_type[k] / n * 100) / 100 : null;
                    }) });
        } else {
          stacked = true;
          ds.push({ label: typeName(a, k), backgroundColor: alpha(c, "D9"), borderRadius: 3,
                    data: hs.map(function (h) {
                      return metric === "orders" ? h.orders_by_type[k] : h.by_type[k];
                    }) });
        }
      });
    } else if (split === "pay") {
      stacked = true;
      KIND_ORDER.forEach(function (k) {
        if (!ps.some(function (p) { return p[k]; })) { return; }
        ds.push({ label: KIND_NAMES[k], backgroundColor: alpha(kindColor(k), "D9"),
                  borderRadius: 3, data: ps.map(function (p) { return p[k]; }) });
      });
      if (metric !== "sales") { note = "التقسيم حسب طريقة الدفع يعرض المبالغ المحصّلة."; }
    } else {
      ds.push({ label: metric === "orders" ? "الطلبات" : (metric === "avg" ? "متوسط الطلب" : "المبيعات"),
                backgroundColor: alpha(t.accent, "CC"), borderColor: t.accent, borderWidth: 1,
                borderRadius: 4,
                data: hs.map(function (h) {
                  return metric === "orders" ? h.orders : (metric === "avg" ? h.avg : h.sales);
                }) });
      if (metric === "sales") {
        y1 = true;
        ds.push({ type: "line", label: "الطلبات", yAxisID: "y1", borderColor: t.warn,
                  backgroundColor: t.warn, pointRadius: 2, tension: .3, borderWidth: 2,
                  data: hs.map(function (h) { return h.orders; }) });
      }
    }
    var ax = axisOpts();
    ax.y.ticks.callback = function (v) { return fmtK(v); };
    if (stacked) { ax.x.stacked = true; ax.y.stacked = true; }
    if (y1) {
      ax.y1 = { position: "right", grid: { display: false },
                ticks: { color: t.warn, font: { size: 10 } }, beginAtZero: true };
    }
    draw("hourlyChart", "hourly", {
      type: "bar", data: { labels: labels, datasets: ds },
      options: baseOpts({ scales: ax, plugins: { legend: { display: ds.length > 1 } } })
    });
    $("hrNote").textContent = note || (VIEW.hrRange === "work"
      ? "من أول ساعة فيها طلبات حتى آخر ساعة، بترتيب اليوم التشغيلي."
      : "الساعات الأربع والعشرون بترتيب اليوم التشغيلي.");
  }

  /* ── Order type: dine-in / takeaway / delivery / apps ───────────────── */

  function renderTypes(d) {
    var a = d.activity || {};
    var types = a.types || [];
    $("typeStats").innerHTML = types.length ? types.map(function (x) {
      return '<div class="stat stat-type" style="border-inline-start-color:' + typeColor(x.key) +
        '"><div class="stat-lbl">' + esc(x.name) + '</div><div class="stat-val">' + fmt(x.sales) +
        '</div><div class="stat-note">' + fmt(x.orders) + " طلب · متوسط " + fmt(x.avg) + " · " +
        x.pct + "%</div></div>";
    }).join("") : '<div class="empty">لا توجد طلبات في هذه الفترة</div>';
    var byName = a.type_source === "order_type";
    var withPresets = byName || a.type_source === "preset";
    $("presetTitle").textContent = byName
      ? "حسب نوع الطلب المختار في نقطة البيع (POS Order Types)"
      : "حسب الإعداد المسبق في نقطة البيع (Preset)";
    $("presetTitle").style.display = withPresets ? "" : "none";
    $("presetTable").style.display = withPresets ? "" : "none";
    $("presetTable").innerHTML = table([byName ? "نوع الطلب" : "الإعداد المسبق", "يُحسب",
                                        "الطلبات", "المبيعات", "%"],
      (a.presets || []).map(function (p) {
        return [esc(p.name), '<span style="color:' + typeColor(p.type) + ';font-weight:700">' +
                esc(p.type_name) + "</span>", fmt(p.orders), "<strong>" + fmt(p.sales) + "</strong>",
                p.pct + "%"];
      }));
    $("typeNote").textContent = a.type_source === "order_type"
      ? "النوع من «نوع الطلب» الذي يختاره الكاشير (POS Order Types)، ويُصنَّف من اسمه: " +
        "محلي/صالة/Dine In = محلي، سفري/استلام/سيارة/Take Away = سفري، توصيل/Delivery = توصيل. " +
        "طلبات كنكت أو FeedUs = تطبيقات. طلب بدون نوع أو باسم غير معروف: على طاولة = محلي، وإلا سفري."
      : a.type_source === "preset"
      ? "النوع من الإعداد المسبق (Preset) للطلب: Dine In = محلي، Takeout = سفري، Delivery = توصيل، " +
        "وأي إعداد آخر يُصنَّف من اسمه (محلي/صالة، سفري/استلام، توصيل). طلبات تطبيقات التوصيل " +
        "(كنكت أو FeedUs) تُحسب «تطبيقات» مهما كان إعدادها. بدون إعداد: على طاولة = محلي، وإلا سفري."
      : (a.type_source === "table"
        ? "بدون إعدادات مسبقة: الطلب على طاولة = محلي، وبدون طاولة = سفري؛ وطلبات التطبيقات = تطبيقات."
        : "لا يوجد في قاعدة البيانات ما يميز نوع الطلب (لا Presets ولا طاولات).");
    drawTypes();
  }

  function drawTypes() {
    var d = state.lastData;
    if (!d || !d.activity) { return; }
    var a = d.activity, t = themeColors();
    var types = a.types || [];
    var byOrders = VIEW.typeMetric === "orders";
    pie("typeShareChart", "typeShare", types.map(function (x) { return x.name; }),
        types.map(function (x) { return byOrders ? x.orders : x.sales; }),
        types.map(function (x) { return typeColor(x.key); }));

    var ax = axisOpts();
    ax.y.ticks.callback = function (v) { return fmtK(v); };
    ax.y1 = { position: "right", grid: { display: false }, beginAtZero: true,
              ticks: { color: t.warn, font: { size: 10 }, callback: function (v) { return fmtK(v); } } };
    draw("typeAvgChart", "typeAvg", {
      type: "bar",
      data: { labels: types.map(function (x) { return x.name; }), datasets: [
        { label: "الطلبات", data: types.map(function (x) { return x.orders; }),
          backgroundColor: types.map(function (x) { return alpha(typeColor(x.key), "CC"); }),
          borderRadius: 5 },
        { type: "line", label: "متوسط الطلب", yAxisID: "y1", borderColor: t.warn,
          backgroundColor: t.warn, pointRadius: 4, borderWidth: 2,
          data: types.map(function (x) { return x.avg; }) }
      ] },
      options: baseOpts({ scales: ax, plugins: { legend: { display: true } } })
    });

    var rng = workRange(a.hours);
    var hs = a.hours.slice(rng[0], rng[1] + 1);
    var mix = presentTypes(a).map(function (k) {
      return { label: typeName(a, k), backgroundColor: alpha(typeColor(k), "D9"), suffix: "%",
               data: hs.map(function (h) {
                 var tot = byOrders ? h.orders : h.sales;
                 var v = byOrders ? h.orders_by_type[k] : h.by_type[k];
                 return tot ? Math.round(v / tot * 1000) / 10 : 0;
               }) };
    });
    var ax2 = axisOpts();
    ax2.x.stacked = true; ax2.y.stacked = true; ax2.y.max = 100;
    ax2.y.ticks.callback = function (v) { return v + "%"; };
    draw("typeMixChart", "typeMix", {
      type: "bar", data: { labels: hs.map(function (h) { return h.hour; }), datasets: mix },
      options: baseOpts({ scales: ax2 })
    });
  }

  /* ── Trend by business day + weekday x hour heat grid ───────────────── */

  function renderTrend(d) {
    var a = d.activity || {};
    var show = (a.days || []).length > 1;
    $("trendCard").style.display = show ? "block" : "none";
    if (!show) { destroy("trend"); return; }
    drawTrend();
    renderHeat(a);
  }

  function drawTrend() {
    var d = state.lastData;
    if (!d || !d.activity) { return; }
    var a = d.activity, t = themeColors();
    var days = a.days || [];
    var labels = days.map(function (x) { return x.day.slice(5) + " " + x.weekday; });
    var ax = axisOpts(), ds = [];
    ax.y.ticks.callback = function (v) { return fmtK(v); };
    if (VIEW.trendSplit === "pay") {
      var pd = {};
      ((d.pay_hours || {}).days || []).forEach(function (x) { pd[x.day] = x; });
      ax.x.stacked = true; ax.y.stacked = true;
      KIND_ORDER.forEach(function (k) {
        var vals = days.map(function (x) { return (pd[x.day] || {})[k] || 0; });
        if (!vals.some(function (v) { return v; })) { return; }
        ds.push({ label: KIND_NAMES[k], data: vals, backgroundColor: alpha(kindColor(k), "D9"),
                  borderRadius: 3 });
      });
    } else if (VIEW.trendSplit === "orders") {
      ax.y1 = { position: "right", grid: { display: false }, beginAtZero: true,
                ticks: { color: t.warn, font: { size: 10 } } };
      ds.push({ label: "الطلبات", data: days.map(function (x) { return x.orders; }),
                backgroundColor: alpha(t.accent, "CC"), borderRadius: 4 });
      ds.push({ type: "line", label: "متوسط الطلب", yAxisID: "y1", borderColor: t.warn,
                backgroundColor: t.warn, pointRadius: 3, tension: .3,
                data: days.map(function (x) { return x.avg; }) });
    } else {
      ax.x.stacked = true; ax.y.stacked = true;
      presentTypes(a).forEach(function (k) {
        ds.push({ label: typeName(a, k), backgroundColor: alpha(typeColor(k), "D9"), borderRadius: 3,
                  data: days.map(function (x) { return x.by_type[k]; }) });
      });
    }
    draw("trendChart", "trend", {
      type: "bar", data: { labels: labels, datasets: ds },
      options: baseOpts({ scales: ax, plugins: { legend: { display: true } } })
    });
  }

  function renderHeat(a) {
    var heat = a.heat || [];
    if (!heat.length) { $("heatBox").innerHTML = ""; return; }
    var rng = workRange(a.hours);
    var max = 0;
    heat.forEach(function (r) {
      r.cells.slice(rng[0], rng[1] + 1).forEach(function (v) { if (v > max) { max = v; } });
    });
    var acc = themeColors().accent;
    var head = "<tr><th></th>" + a.hours.slice(rng[0], rng[1] + 1).map(function (h) {
      return "<th>" + esc(h.hour.slice(0, 2)) + "</th>";
    }).join("") + "</tr>";
    var body = heat.map(function (r) {
      return '<tr><th class="wd">' + esc(r.weekday) + "</th>" +
        r.cells.slice(rng[0], rng[1] + 1).map(function (v, i) {
          var f = max ? v / max : 0;
          var a2 = Math.round((0.08 + 0.92 * f) * 255).toString(16);
          if (a2.length < 2) { a2 = "0" + a2; }
          return "<td" + (f > 0.55 ? ' class="hot"' : "") + ' style="background:' +
            (v ? alpha(acc, a2) : "") + '" title="' + esc(r.weekday) + " " +
            esc(a.hours[rng[0] + i].hour) + ": " + fmt(v) + " · " + r.orders[rng[0] + i] + ' طلب">' +
            (v ? fmtK(v) : "") + "</td>";
        }).join("") + "</tr>";
    }).join("");
    $("heatBox").innerHTML = '<table class="heat">' + head + body + "</table>" +
      '<div class="heat-legend">أقل <i style="background:linear-gradient(90deg,' + alpha(acc, "14") +
      "," + acc + ')"></i> أعلى · متوسط اليوم الواحد من كل يوم أسبوع في الفترة</div>';
  }

  /* ── Top items ──────────────────────────────────────────────────────── */

  function renderTop(items) {
    var top = (items || []).slice(0, 12);
    var t = themeColors();
    hbar("topChart", "top", top.map(function (p) { return p.name; }),
         [{ label: "المبيعات", data: top.map(function (p) { return p.revenue; }),
            backgroundColor: top.map(function (p, i) { return alpha(t.accent, i < 3 ? "F2" : "99"); }),
            borderRadius: 4 }]);
  }

  /* ── Shortages / overages ───────────────────────────────────────────── */

  var SHORT_TITLES = { by_pos: "العجز والزيادة حسب نقطة البيع",
                       by_cashier: "العجز والزيادة حسب الكاشير",
                       by_day: "العجز والزيادة حسب اليوم التشغيلي" };

  function drawShortages() {
    var d = state.lastData;
    if (!d || !d.shortages) { return; }
    var t = themeColors(), sh = d.shortages;
    var rows = (sh[VIEW.shortDim] || []).slice(0, 20);
    $("shortTitle").textContent = SHORT_TITLES[VIEW.shortDim] || "";
    var ax = axisOpts();
    ax.y.ticks.callback = function (v) { return fmtK(v); };
    var ds = [
      { label: "العجز", data: rows.map(function (r) { return -r.shortage; }),
        backgroundColor: alpha(t.neg, "CC"), borderRadius: 4 },
      { label: "الزيادة", data: rows.map(function (r) { return r.overage; }),
        backgroundColor: alpha(t.pos, "CC"), borderRadius: 4 }
    ];
    if (VIEW.shortDim === "by_day") {
      ds.push({ type: "line", label: "الصافي", data: rows.map(function (r) { return r.net; }),
                borderColor: t.warn, backgroundColor: t.warn, pointRadius: 3, tension: .3 });
    } else {
      ax.x.stacked = true; ax.y.stacked = true;
    }
    draw("shortChart", "short", {
      type: "bar",
      data: { labels: rows.map(function (r) { return short(r.name, 18); }), datasets: ds },
      options: baseOpts({ scales: ax, plugins: { legend: { display: true } } })
    }, "لا توجد فروقات في الإغلاقات — ممتاز");

    var top = sh.top_sessions || [];
    hbar("sessChart", "sess", top.map(function (s) { return s.name + " · " + s.pos; }),
         [{ label: "نقدي", data: top.map(function (s) { return s.cash; }),
            backgroundColor: top.map(function (s) { return alpha(s.cash < 0 ? t.neg : t.pos, "CC"); }),
            borderRadius: 3 },
          { label: "غير نقدي", data: top.map(function (s) { return s.bank; }),
            backgroundColor: top.map(function (s) { return alpha(s.bank < 0 ? t.neg : t.pos, "77"); }),
            borderRadius: 3 }],
         { stacked: true, legend: true, empty: "لا توجد جلسات فيها فرق" });
  }

  /* ── Kitchen charts ─────────────────────────────────────────────────── */

  function drawKitchenCharts(k) {
    var box = $("kitchenCharts");
    if (!k || !k.count || !k.by_hour || !k.by_hour.length) {
      box.style.display = "none";
      destroy("kHour"); destroy("kDisp");
      return;
    }
    box.style.display = "";
    var t = themeColors();
    var ax = axisOpts();
    ax.y.title = { display: true, text: "دقيقة", color: t.muted };
    ax.y1 = { position: "right", grid: { display: false }, beginAtZero: true,
              ticks: { color: t.neg, font: { size: 10 }, precision: 0 } };
    draw("kHourChart", "kHour", {
      type: "bar",
      data: { labels: k.by_hour.map(function (h) { return h.name; }), datasets: [
        { label: "متوسط (دقيقة)", data: k.by_hour.map(function (h) { return h.avg; }),
          backgroundColor: alpha(t.accent, "CC"), borderRadius: 4 },
        { type: "line", label: "متأخرة", yAxisID: "y1",
          data: k.by_hour.map(function (h) { return h.late; }),
          borderColor: t.neg, backgroundColor: t.neg, pointRadius: 3, tension: .3 }
      ] },
      options: baseOpts({ scales: ax, plugins: { legend: { display: true } } })
    });
    hbar("kDispChart", "kDisp", k.by_display.map(function (b) { return b.name; }),
         [{ label: "متوسط (دقيقة)", data: k.by_display.map(function (b) { return b.avg; }),
            backgroundColor: k.by_display.map(function (b) {
              return alpha(b.avg > k.late_minutes ? t.neg : t.accent, "CC");
            }), borderRadius: 4 }]);
  }

  /* ── Item profitability (recipe cost) ───────────────────────────────── */

  function renderProfit(p) {
    var card = $("profitCard");
    if (!p) { card.style.display = "none"; return; }
    card.style.display = "block";
    var t = p.totals;
    $("profitSub").textContent = p.mrp
      ? "التكلفة من رسبي كل صنف (قائمة المواد)"
      : "موديول التصنيع (mrp) غير مثبت: لا توجد رسبيات، التكلفة من تكلفة المنتج";
    $("profitStats").innerHTML =
      stat("المبيعات قبل الضريبة", fmt(t.net), "") +
      stat("تكلفة الرسبي", fmt(t.cost), t.net ? (t.cost / t.net * 100).toFixed(1) + "% من المبيعات" : "") +
      stat("إجمالي الربح", fmt(t.profit), "", t.profit < 0 ? "stat-neg" : "stat-pos") +
      stat("هامش الربح", t.margin + "%", "بعد الرسبي", "stat-accent") +
      (p.connect ? stat("عمولة التطبيقات", fmt(t.commission),
                        "الربح بعدها " + fmt(t.profit_after) + " · " + t.margin_after + "%",
                        t.commission ? "stat-neg" : "") : "") +
      stat("أصناف بدون رسبي", fmt(t.no_recipe_items),
           t.no_recipe_items ? fmt(t.no_recipe_net) + " · " + t.no_recipe_pct + "% من المبيعات" : "كل الأصناف لها رسبي",
           t.no_recipe_items ? "stat-neg" : "stat-pos");
    drawProfit();
  }

  function marginColor(m) {
    var t = themeColors();
    return m < 50 ? t.neg : (m < 65 ? t.warn : t.pos);
  }

  function drawProfit() {
    var d = state.lastData;
    if (!d || !d.profit) { return; }
    var p = d.profit, t = themeColors();
    var view = VIEW.profitView || "all";
    var rows = view === "modifiers" ? p.modifiers.slice() : p.items.slice();
    if (view === "missing") { rows = rows.filter(function (e) { return e.no_recipe || e.note; }); }
    if (view === "low") {
      rows = rows.filter(function (e) { return e.net > 0; })
                 .sort(function (a, b) { return a.margin - b.margin; });
    }
    var top = p.items.slice(0, 12);
    hbar("profitChart", "profit", top.map(function (e) { return e.name; }), [
      { label: "الربح", data: top.map(function (e) { return e.profit; }),
        backgroundColor: top.map(function (e) { return alpha(e.no_recipe ? t.warn : t.pos, "CC"); }),
        borderRadius: 4 },
      { label: "تكلفة الرسبي", data: top.map(function (e) { return e.cost; }),
        backgroundColor: alpha(t.neg, "88"), borderRadius: 4 }
    ], { stacked: true, legend: true });
    var bySales = p.items.slice().sort(function (a, b) { return b.net - a.net; }).slice(0, 12);
    var ax = axisOpts();
    ax.y.ticks.callback = function (v) { return v + "%"; };
    draw("marginChart", "margin", {
      type: "bar",
      data: { labels: bySales.map(function (e) { return short(e.name, 14); }), datasets: [
        { label: "الهامش", suffix: "%", data: bySales.map(function (e) { return e.margin; }),
          backgroundColor: bySales.map(function (e) {
            return alpha(e.no_recipe ? t.muted : marginColor(e.margin), "CC");
          }), borderRadius: 4 }] },
      options: baseOpts({ scales: ax })
    });
    var withComm = p.connect;
    $("profitTable").innerHTML = table(
      ["#", view === "modifiers" ? "المودفاير" : "الصنف", "الكمية", "المبيعات", "تكلفة الوحدة",
       "التكلفة", "الربح", "الهامش"].concat(withComm ? ["العمولة", "بعد العمولة"] : []).concat(["ملاحظات"]),
      rows.slice(0, 200).map(function (e, i) {
        return [i + 1, esc(e.name) + (e.combo ? ' <span class="na">(كومبو)</span>' : ""),
                fmt(e.qty), fmt(e.net), fmt(e.unit_cost), fmt(e.cost),
                '<strong class="' + (e.profit < 0 ? "neg" : "") + '">' + fmt(e.profit) + "</strong>",
                '<span style="color:' + (e.no_recipe ? t.muted : marginColor(e.margin)) +
                  ';font-weight:700">' + e.margin + "%</span>"].concat(withComm ? [
                e.commission ? '<span class="neg">' + fmt(e.commission) + "</span>" : '<span class="na">—</span>',
                "<strong>" + fmt(e.profit_after) + '</strong> <span class="na">' + e.margin_after + "%</span>"] : []).concat([
                e.note ? '<span class="' + (e.no_recipe ? "neg" : "na") + '" style="font-size:.76rem">' +
                  esc(e.note) + "</span>" : '<span class="posv" style="font-size:.76rem">رسبي ✓</span>']);
      }));
    var miss = p.missing || [];
    $("missingTitle").textContent = "منتجات بدون رسبي أو مكونات بدون تكلفة (" + (p.missing_count || 0) + ")";
    $("missingTable").innerHTML = table(["المنتج", "يُباع كـ", "الكمية", "المبيعات", "تكلفة الوحدة المستخدمة", "الملاحظة"],
      miss.map(function (n) {
        return [esc(n.name), esc(n.role), fmt(n.qty), fmt(n.net),
                n.unit_cost ? fmt(n.unit_cost) : '<span class="neg">0</span>',
                '<span class="' + (n.source === "none" ? "neg" : "") + '">' + esc(n.note) + "</span>"];
      }));
  }

  /* ── Menu engineering ───────────────────────────────────────────────── */

  var MENU_ORDER = ["star", "plowhorse", "puzzle", "dog"];

  function menuColor(k) {
    var t = themeColors();
    return { star: t.pos, plowhorse: t.warn, puzzle: t.info, dog: t.neg }[k] || t.muted;
  }

  function renderMenu(d) {
    var m = d.menu, card = $("menuCard");
    if (!m || !m.items || !m.items.length) { card.style.display = "none"; return; }
    card.style.display = "block";
    drawMenu();
  }

  function drawMenu() {
    var d = state.lastData;
    if (!d || !d.menu) { return; }
    var m = d.menu, t = themeColors();
    var key = VIEW.menuScope === "cat" ? "class_cat" : "class";
    var counts = {};
    MENU_ORDER.forEach(function (k) { counts[k] = { n: 0, c: 0 }; });
    m.items.forEach(function (it) { counts[it[key]].n += 1; counts[it[key]].c += it.contribution; });
    $("menuStats").innerHTML = MENU_ORDER.map(function (k) {
      return '<div class="stat" style="border-inline-start-color:' + menuColor(k) + '"><div class="stat-lbl">' +
        esc(m.labels[k]) + '</div><div class="stat-val">' + counts[k].n + ' <span style="font-size:.8rem;color:var(--muted)">صنف</span></div>' +
        '<div class="stat-note">مساهمة ' + fmt(counts[k].c) + "<br>" + esc(m.advice[k]) + "</div></div>";
    }).join("");

    // Matrix: each class its own dataset, threshold lines across.
    var ds = MENU_ORDER.map(function (k) {
      var pts = m.items.filter(function (it) { return it[key] === k; });
      return { type: "scatter", label: m.labels[k], backgroundColor: alpha(menuColor(k), "CC"),
               borderColor: menuColor(k), pointRadius: 7, pointHoverRadius: 9,
               data: pts.map(function (it) { return { x: it.qty, y: it.cm_unit, name: it.name }; }) };
    });
    var maxX = Math.max.apply(null, m.items.map(function (i) { return i.qty; })) * 1.1 || 1;
    if (VIEW.menuScope !== "cat") {
      ds.push({ type: "line", label: "حد الشعبية", borderColor: t.muted, borderDash: [5, 4],
                pointRadius: 0, borderWidth: 1.5,
                data: [{ x: m.pop_threshold, y: 0 },
                       { x: m.pop_threshold, y: Math.max.apply(null, m.items.map(function (i) { return i.cm_unit; })) * 1.1 }] });
      ds.push({ type: "line", label: "متوسط المساهمة", borderColor: t.muted, borderDash: [2, 3],
                pointRadius: 0, borderWidth: 1.5,
                data: [{ x: 0, y: m.cm_threshold }, { x: maxX, y: m.cm_threshold }] });
    }
    var ax = axisOpts();
    ax.x.type = "linear"; ax.x.min = 0;
    ax.x.title = { display: true, text: "الكمية المباعة (الشعبية)", color: t.muted };
    ax.y.title = { display: true, text: "المساهمة للوحدة", color: t.muted };
    draw("menuChart", "menu", {
      type: "scatter", data: { datasets: ds },
      options: baseOpts({ scales: ax, interaction: { mode: "nearest", intersect: true },
        plugins: { legend: { display: true },
                   tooltip: { callbacks: { label: function (c) {
                     var r = c.raw || {};
                     return r.name ? r.name + ": " + fmt(r.x) + " قطعة · مساهمة " + fmt(r.y) : null;
                   } } } } })
    });
    pie("menuShareChart", "menuShare", MENU_ORDER.map(function (k) { return m.labels[k]; }),
        MENU_ORDER.map(function (k) { return Math.max(0, counts[k].c); }),
        MENU_ORDER.map(function (k) { return menuColor(k); }));

    var rows = m.items.slice().sort(function (a, b) {
      return MENU_ORDER.indexOf(a[key]) - MENU_ORDER.indexOf(b[key]) || b.contribution - a.contribution;
    });
    $("menuTable").innerHTML = table(["الصنف", "الفئة", "الكمية", "سعر البيع", "المساهمة للوحدة",
                                      "إجمالي المساهمة", "التصنيف", "التوصية"],
      rows.map(function (it) {
        return [esc(it.name) + (it.no_recipe ? ' <span class="neg" style="font-size:.72rem">(بدون رسبي)</span>' : ""),
                '<span class="na">' + esc(it.category) + "</span>", fmt(it.qty), fmt(it.price),
                fmt(it.cm_unit), "<strong>" + fmt(it.contribution) + "</strong>",
                '<span style="font-weight:800;color:' + menuColor(it[key]) + '">' + esc(m.labels[it[key]]) + "</span>",
                '<span class="na" style="font-size:.76rem">' + esc(m.advice[it[key]]) + "</span>"];
      }));
    $("menuNote").textContent = "طريقة Kasavana & Smith: الصنف «شائع» إذا كميته ≥ 70% من الحصة المتساوية" +
      (VIEW.menuScope === "cat" ? " داخل فئته" : " (" + fmt(m.pop_threshold) + " قطعة)") +
      "، و«مربح» إذا مساهمته للوحدة ≥ متوسط المساهمة الموزون" +
      (VIEW.menuScope === "cat" ? " لفئته." : " (" + fmt(m.cm_threshold) + ").") +
      " المساهمة = المبيعات قبل الضريبة − تكلفة الرسبي − عمولة التطبيقات. تصنيف الأصناف بدون رسبي غير دقيق.";
  }

  /* ── Actual vs recipe usage: its own load ───────────────────────────── */

  var vr = { loaded: false, busy: false, sig: null, data: null };

  function markVarDirty() {
    var stale = vr.loaded && periodSignature() !== vr.sig;
    $("varPending").style.display = stale ? "block" : "none";
    $("varRefreshBtn").classList.toggle("attention", stale);
  }

  function loadVariance() {
    if (vr.busy) { return; }
    if (state.period === "custom") {
      state.dfrom = $("dateFrom").value;
      state.dto = $("dateTo").value;
      if (!state.dfrom || !state.dto) { alert("اختر تاريخ البداية والنهاية"); return; }
    }
    vr.busy = true;
    var btn = $("varRefreshBtn"), pill = $("varState");
    btn.disabled = true; btn.textContent = "جارٍ الحساب…";
    pill.className = "pill pill-busy"; pill.textContent = "جارٍ الحساب…";
    $("varError").style.display = "none";
    var t0 = performance.now(), sig = periodSignature();
    fetch(BASE + "/variance?" + buildQS(), { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.error) {
          throw new Error({ no_stock: "موديول المخزون غير مثبت", no_warehouse: "لا يوجد مستودع مرتبط بنقاط البيع المحددة" }[d.error] ||
                          d.detail || d.error);
        }
        vr.loaded = true; vr.data = d; vr.sig = sig;
        $("varContent").style.display = "block";
        drawVariance();
        pill.className = "pill pill-ok"; pill.textContent = "محسوب";
        $("varStale").innerHTML = "آخر حساب: <b>" + esc(d.last_updated) + "</b>";
        $("varDuration").innerHTML = "مدة الحساب: <b>" + esc(humanMs(Math.round(performance.now() - t0))) + "</b>";
        $("varPeriod").innerHTML = "الفترة: <b>" + esc(d.period_label) + "</b>";
        markVarDirty();
      })
      .catch(function (e) {
        var box = $("varError");
        box.style.display = "block";
        box.innerHTML = '<div class="err-title">تعذّر حساب المقارنة</div><div>' + esc(e && e.message ? e.message : e) + "</div>";
        pill.className = "pill pill-err"; pill.textContent = "فشل";
      })
      .finally(function () {
        vr.busy = false; btn.disabled = false; btn.textContent = "تحديث المقارنة";
      });
  }

  function drawVariance() {
    var d = vr.data;
    if (!d) { return; }
    var t = themeColors(), tt = d.totals;
    var gap = tt.food_cost_actual - tt.food_cost_theoretical;
    $("varStats").innerHTML =
      stat("تكلفة الأكل النظرية", tt.food_cost_theoretical + "%", fmt(tt.theoretical_value) + " من الرسبي") +
      stat("تكلفة الأكل الفعلية", tt.food_cost_actual + "%", fmt(tt.actual_value) + " من المخزون",
           gap > 1 ? "stat-neg" : "stat-pos") +
      stat("الفرق", (tt.variance_value > 0 ? "+" : "") + fmt(tt.variance_value),
           (tt.variance_pct > 0 ? "+" : "") + tt.variance_pct + "% · " + (gap > 0 ? "+" : "") + gap.toFixed(1) + " نقطة من المبيعات",
           tt.variance_value > 0 ? "stat-neg" : "stat-pos") +
      stat("الهالك", fmt(tt.waste_value), "من عمليات الإتلاف", tt.waste_value ? "stat-neg" : "") +
      stat("صافي فروقات الجرد", fmt(Math.abs(tt.counts_value)),
           !tt.counts_value ? "لا توجد فروقات جرد" : (tt.counts_value > 0 ? "نقص (المعدود أقل من النظام)" : "زيادة (المعدود أكثر من النظام)"),
           tt.counts_value > 0 ? "stat-neg" : "") +
      stat("مكونات فرقها أكثر من 5%", fmt(tt.over_5),
           tt.no_moves ? tt.no_moves + " مكون بدون أي حركة مخزون" : "", tt.over_5 ? "stat-neg" : "");

    var rows = d.rows;
    var view = VIEW.varView || "all";
    if (view === "over") { rows = rows.filter(function (r) { return r.variance_pct !== null && Math.abs(r.variance_pct) > 5; }); }
    if (view === "waste") { rows = rows.filter(function (r) { return r.waste; }).sort(function (a, b) { return b.waste_value - a.waste_value; }); }

    var top = d.rows.filter(function (r) { return r.variance_value; }).slice(0, 12);
    hbar("varChart", "var", top.map(function (r) { return r.name; }),
         [{ label: "قيمة الفرق", data: top.map(function (r) { return r.variance_value; }),
            backgroundColor: top.map(function (r) { return alpha(r.variance_value > 0 ? t.neg : t.pos, "CC"); }),
            borderRadius: 4 }], { empty: "لا توجد فروقات" });
    var mix = d.rows.slice().sort(function (a, b) { return b.actual_value - a.actual_value; }).slice(0, 10);
    var parts = [["sold", "مبيعات", t.accent], ["production", "إنتاج", t.info],
                 ["waste", "هالك", t.neg], ["counts", "جرد", t.warn]];
    hbar("varMixChart", "varMix", mix.map(function (r) { return r.name; }),
         parts.map(function (p) {
           return { label: p[1], backgroundColor: alpha(p[2], "CC"), borderRadius: 3,
                    data: mix.map(function (r) { return Math.round(r[p[0]] * r.unit_cost * 100) / 100; }) };
         }).concat([{ type: "line", label: "النظري", indexAxis: "y", showLine: false, pointStyle: "line",
                      pointRadius: 12, borderWidth: 3, borderColor: t.text, backgroundColor: t.text,
                      data: mix.map(function (r) { return r.theoretical_value; }) }]),
         { stacked: true, legend: true });

    function q(v, r) { return v ? fmt(Math.round(v * 1000) / 1000) : '<span class="na">—</span>'; }
    // Using more than the recipe is the bad direction here.
    function over(v) {
      if (!v) { return '<span class="na">0</span>'; }
      return '<span class="' + (v > 0 ? "neg" : "posv") + '">' + (v > 0 ? "+" : "") + fmt(v) + "</span>";
    }
    $("varTable").innerHTML = table(["المكون", "الوحدة", "النظري", "مبيعات", "إنتاج", "هالك", "جرد",
                                     "الفعلي", "الفرق", "%", "قيمة الفرق", "ملاحظة"],
      rows.slice(0, 300).map(function (r) {
        var note = [];
        if (r.no_cost) { note.push("بدون تكلفة"); }
        if (r.no_moves && r.theoretical) { note.push("لا توجد حركة مخزون — هل المنتج مخزّن؟"); }
        if (!r.theoretical && r.actual) { note.push("مصروف وليس في أي رسبي مُباع"); }
        return [esc(r.name), esc(r.uom), q(r.theoretical), q(r.sold), q(r.production), q(r.waste), q(r.counts),
                "<strong>" + q(r.actual) + "</strong>", over(Math.round(r.variance * 1000) / 1000),
                r.variance_pct === null ? '<span class="na">—</span>' :
                  '<span class="' + (Math.abs(r.variance_pct) > 5 ? (r.variance_pct > 0 ? "neg" : "posv") : "na") + '">' +
                  (r.variance_pct > 0 ? "+" : "") + r.variance_pct + "%</span>",
                "<strong>" + over(r.variance_value) + "</strong>",
                '<span class="na" style="font-size:.74rem">' + esc(note.join(" · ")) + "</span>"];
      }));
    $("varNote").textContent = "النظري = المبيعات مفكوكة بالرسبي حتى المكونات المخزّنة (والمنتجات نصف المصنّعة المنتَجة في الفترة بقائمة موادها). " +
      "الفعلي = ما خرج من مستودعات الفروع: للعميل (حركات نقطة البيع ناقص المرتجعات) + للإنتاج + الإتلاف + نقص الجرد (ناقص زيادته). " +
      "التحويلات بين الفروع ليست استهلاكاً. القيمة بتكلفة المنتج الحالية. المستودعات: " + (d.warehouses || []).join("، ") + "." +
      (d.untracked && d.untracked.length ? " منتجات مباعة غير مخزّنة وبدون رسبي (لا تدخل المقارنة): " +
        d.untracked.slice(0, 8).map(function (u) { return u.name; }).join("، ") + "." : "");
  }

  /* ── App commissions (Connect) ──────────────────────────────────────── */

  function renderCommissions(d) {
    var c = d.commissions, card = $("commCard");
    if (!c) { card.style.display = "none"; return; }
    card.style.display = "block";
    var t = themeColors(), tt = c.totals;
    var prof = d.profit || { channels: [], totals: {} };
    $("commStats").innerHTML =
      stat("طلبات التطبيقات", fmt(tt.orders), fmt(tt.gmv) + " مبيعات التطبيقات") +
      stat("إجمالي العمولة", fmt(tt.commission), tt.rate + "% من مبيعات التطبيقات", "stat-neg") +
      stat("منها مقدّرة", fmt(tt.estimated),
           tt.estimated_orders ? fmt(tt.estimated_orders) + " طلب بدون عمولة مسجلة" : "كل العمولات مسجلة") +
      stat("العمولة للطلب", tt.orders ? fmt(tt.commission / tt.orders) : "0", "") +
      stat("الربح بعد الرسبي والعمولة", fmt(prof.totals.profit_after || 0),
           (prof.totals.margin_after || 0) + "% هامش", "stat-accent") +
      (c.no_data.orders ? stat("تطبيقات بدون بيانات عمولة", fmt(c.no_data.orders),
                               fmt(c.no_data.sales) + " — ليست من كنكت", "stat-neg") : "");

    var apps = c.apps;
    var ax = axisOpts();
    ax.y.ticks.callback = function (v) { return fmtK(v); };
    ax.y1 = { position: "right", grid: { display: false }, beginAtZero: true,
              ticks: { color: t.warn, font: { size: 10 }, callback: function (v) { return v + "%"; } } };
    draw("commAppChart", "commApp", {
      type: "bar",
      data: { labels: apps.map(function (a) { return a.name; }), datasets: [
        { label: "العمولة المسجلة", data: apps.map(function (a) { return a.commission - a.estimated; }),
          backgroundColor: alpha(t.neg, "CC"), borderRadius: 4, stack: "c" },
        { label: "العمولة المقدّرة", data: apps.map(function (a) { return a.estimated; }),
          backgroundColor: alpha(t.neg, "55"), borderRadius: 4, stack: "c" },
        { type: "line", label: "النسبة", yAxisID: "y1", suffix: "%", showLine: false,
          data: apps.map(function (a) { return a.rate; }), borderColor: t.warn,
          backgroundColor: t.warn, pointRadius: 6, pointStyle: "rectRot" }
      ] },
      options: baseOpts({ scales: merge(ax, { x: { stacked: true }, y: { stacked: true } }),
                          plugins: { legend: { display: true } } })
    }, "لا توجد طلبات تطبيقات في هذه الفترة");

    var ch = prof.channels || [];
    var ax2 = axisOpts();
    ax2.y.ticks.callback = function (v) { return v + "%"; };
    draw("channelChart", "channel", {
      type: "bar",
      data: { labels: ch.map(function (x) { return x.name; }), datasets: [
        { label: "الهامش بعد الرسبي", suffix: "%", data: ch.map(function (x) { return x.margin; }),
          backgroundColor: alpha(t.accent, "99"), borderRadius: 4 },
        { label: "الهامش بعد العمولة", suffix: "%", data: ch.map(function (x) { return x.margin_after; }),
          backgroundColor: ch.map(function (x) { return alpha(x.app ? t.warn : t.pos, "DD"); }), borderRadius: 4 }
      ] },
      options: baseOpts({ scales: ax2, plugins: { legend: { display: true } } })
    });

    var days = c.days || [];
    $("commDayBox").style.display = days.length > 1 ? "" : "none";
    if (days.length > 1) {
      var ax3 = axisOpts();
      ax3.x.stacked = true; ax3.y.stacked = true;
      ax3.y.ticks.callback = function (v) { return fmtK(v); };
      var pal = palette();
      draw("commDayChart", "commDay", {
        type: "bar",
        data: { labels: days.map(function (x) { return x.day.slice(5); }),
                datasets: c.app_names.map(function (n, i) {
                  return { label: "عمولة " + n, data: days.map(function (x) { return x[n] || 0; }),
                           backgroundColor: alpha(pal[(i + 3) % pal.length], "CC"), borderRadius: 3 };
                }) },
        options: baseOpts({ scales: ax3, plugins: { legend: { display: true } } })
      });
    } else { destroy("commDay"); }

    $("commTable").innerHTML = table(["التطبيق", "الطلبات", "مبيعات التطبيق", "العمولة", "منها مقدّرة",
                                      "النسبة", "للطلب", "الصافي بعد العمولة"],
      apps.map(function (a) {
        return [esc(a.name), fmt(a.orders), fmt(a.gmv), '<strong class="neg">' + fmt(a.commission) + "</strong>",
                a.estimated ? fmt(a.estimated) + ' <span class="na">(' + a.estimated_orders + " طلب)</span>"
                            : '<span class="na">—</span>',
                a.rate + "%", fmt(a.per_order), "<strong>" + fmt(a.after) + "</strong>"];
      }),
      apps.length ? ["الإجمالي", fmt(tt.orders), fmt(tt.gmv), fmt(tt.commission), fmt(tt.estimated),
                     tt.rate + "%", tt.orders ? fmt(tt.commission / tt.orders) : "0", ""] : null);
    $("channelTable").innerHTML = table(["القناة", "المبيعات قبل الضريبة", "تكلفة الرسبي", "العمولة",
                                         "الربح بعد الرسبي", "الربح بعد العمولة", "الهامش بعد العمولة"],
      ch.map(function (x) {
        return [esc(x.name), fmt(x.net), fmt(x.cost), x.commission ? '<span class="neg">' + fmt(x.commission) + "</span>"
                : '<span class="na">—</span>', fmt(x.profit), "<strong>" + fmt(x.profit_after) + "</strong>",
                '<span style="font-weight:700;color:' + marginColor(x.margin_after) + '">' + x.margin_after + "%</span>"];
      }));
    $("commNote").textContent = "العمولة تُوزّع على أصناف الطلب بنسبة مبيعات كل صنف، فتظهر «الربح بعد العمولة» في ربحية الأصناف، " +
      "وتدخل نقطة التعادل كتكلفة متغيرة لكل طلب. العمولة كما سجّلها كنكت (من المنصة أو من نسبة الربط وقت استلام الطلب)." +
      (c.no_data.orders ? " طلبات التطبيقات المسجلة بدون كنكت (مثل FeedUs) لا توجد لها بيانات عمولة." : "");
  }

  /* ── Break-even: its own load, its own status ───────────────────────── */

  var be = { loaded: false, busy: false, sig: null, data: null, basis: "avg" };

  function beSignature() { return [periodSignature(), be.basis].join("~"); }

  function markBeDirty() {
    if (!be.loaded || beSignature() === be.sig) {
      $("bePending").style.display = "none";
      $("beRefreshBtn").classList.remove("attention");
      return;
    }
    $("bePending").style.display = "block";
    $("beRefreshBtn").classList.add("attention");
  }

  function setBePill(cls, text) {
    var el = $("beState");
    el.className = "pill " + cls;
    el.textContent = text;
  }


  /* ── Operations across apps (own route and button) ─────────────────── */

  var ops = { loaded: false, busy: false, sig: null, data: null, tab: null };
  var OPS_ICONS = { inventory: "📦", purchase: "🛒", accounting: "📒", manufacturing: "🍳", people: "👥", guests: "⭐" };

  function opsSignature() { return periodSignature(); }

  function markOpsDirty() {
    if (!ops.loaded || opsSignature() === ops.sig) {
      $("opsPending").style.display = "none";
      $("opsRefreshBtn").classList.remove("attention");
      return;
    }
    $("opsPending").style.display = "block";
    $("opsRefreshBtn").classList.add("attention");
  }

  function setOpsPill(cls, text) {
    var el = $("opsState");
    el.className = "pill " + cls;
    el.textContent = text;
  }

  function opsValue(v, kind) {
    if (v === null || v === undefined || v === "—") { return '<span class="na">—</span>'; }
    if (kind === "text") { return esc(v); }
    if (kind === "pct") { return fmt(v) + "%"; }
    return fmt(v);
  }

  function loadOps() {
    if (ops.busy) { return; }
    if (state.period === "custom") {
      state.dfrom = $("dateFrom").value;
      state.dto = $("dateTo").value;
      if (!state.dfrom || !state.dto) { alert("اختر تاريخ البداية والنهاية"); return; }
    }
    ops.busy = true;
    var btn = $("opsRefreshBtn");
    btn.disabled = true;
    btn.textContent = "جارٍ الحساب…";
    setOpsPill("pill-busy", "جارٍ الحساب…");
    $("opsError").style.display = "none";
    var t0 = performance.now(), sig = opsSignature();
    fetch(BASE + "/ops?" + buildQS(), { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.error) { throw new Error(d.detail || d.error); }
        ops.loaded = true; ops.data = d; ops.sig = sig;
        $("opsContent").style.display = "block";
        renderOps(d);
        setOpsPill("pill-ok", "محسوب");
        $("opsStale").innerHTML = "آخر حساب: <b>" + esc(d.last_updated) + "</b>";
        $("opsDuration").innerHTML = "مدة الحساب: <b>" + esc(humanMs(Math.round(performance.now() - t0))) +
          "</b> <span style=\"opacity:.75\">(الخادم " + esc(humanMs(d.duration_ms)) + ")</span>";
        $("opsPeriod").innerHTML = "الفترة: <b>" + esc(d.period_label) + "</b>";
        markOpsDirty();
      })
      .catch(function (e) {
        var box = $("opsError");
        box.style.display = "block";
        box.innerHTML = '<div class="err-title">تعذّر حساب العمليات</div><div>' +
          esc(e && e.message ? e.message : e) + "</div>";
        setOpsPill("pill-err", "فشل");
      })
      .finally(function () {
        ops.busy = false;
        btn.disabled = false;
        btn.textContent = "تحديث العمليات";
      });
  }

  function renderOps(d) {
    var secs = d.sections || [];
    if (!secs.length) {
      $("opsContent").innerHTML = '<div class="empty">لا توجد تطبيقات تشغيلية مثبتة.</div>';
      $("opsTabs").innerHTML = "";
      return;
    }
    if (!ops.tab || !secs.some(function (s) { return s.key === ops.tab; })) { ops.tab = secs[0].key; }
    $("opsTabs").innerHTML = '<div class="seg">' + secs.map(function (s) {
      return '<button type="button" data-ops="' + s.key + '" class="' + (s.key === ops.tab ? "on" : "") + '">' +
        (OPS_ICONS[s.key] || "") + " " + esc(s.title) + "</button>";
    }).join("") + "</div>";
    Array.prototype.forEach.call($("opsTabs").querySelectorAll("button[data-ops]"), function (b) {
      b.addEventListener("click", function () { ops.tab = this.dataset.ops; renderOps(ops.data); });
    });
    var s = secs.filter(function (x) { return x.key === ops.tab; })[0];
    var html = "";
    if (s.error) {
      html = '<div class="err-box" style="display:block"><div class="err-title">تعذّر حساب «' + esc(s.title) +
        '»</div><div>' + esc(s.error) + "</div></div>";
      $("opsContent").innerHTML = html;
      return;
    }
    html += '<div class="stat-grid">' + (s.stats || []).map(function (st) {
      var cls = st.tone === "pos" ? "stat-pos" : st.tone === "neg" ? "stat-neg" :
        st.tone === "accent" ? "stat-accent" : st.tone === "warn" ? "stat-warn" : "";
      return stat(esc(st.label), opsValue(st.value, st.kind), esc(st.note || ""), cls);
    }).join("") + "</div>";
    if (s.chart) { html += '<div class="chart-box tall mt10"><canvas id="opsChart"></canvas></div>'; }
    (s.tables || []).forEach(function (tb) {
      html += '<div class="sub-title mt10">' + esc(tb.title) + '</div><div class="scrollx">' +
        table(tb.head.map(esc), tb.rows.map(function (r) {
          return r.map(function (c, i) {
            var k = (tb.kinds || [])[i];
            return k === "text" || !k ? esc(c) : fmtCell(c, k === "money" || k === "qty" ? "num" : k);
          });
        })) + "</div>";
    });
    var foot = [];
    if (s.note) { foot.push(esc(s.note)); }
    if (s.link) {
      foot.push('<a class="linkbtn" target="_top" href="' + esc(s.link) + '">فتح في أودو ↗</a>');
    }
    if (foot.length) { html += '<div class="card-note mt10">' + foot.join(" · ") + "</div>"; }
    $("opsContent").innerHTML = html;
    if (s.chart) {
      var t = themeColors();
      var tone = function (k) { return t[k] || t.accent; };
      var ax = axisOpts();
      ax.y.ticks.callback = function (v) { return fmtK(v); };
      draw("opsChart", "opsChart", {
        type: s.chart.type || "bar",
        data: { labels: s.chart.labels, datasets: s.chart.series.map(function (sr) {
          return { label: sr.label, data: sr.data, backgroundColor: tone(sr.tone),
                   borderColor: tone(sr.tone), borderRadius: 6, maxBarThickness: 38 };
        }) },
        options: baseOpts({ scales: ax, plugins: { legend: { display: s.chart.series.length > 1, rtl: true,
          labels: { color: t.text } } } })
      });
    }
  }

  function loadBreakeven() {
    if (be.busy) { return; }
    if (state.period === "custom") {
      state.dfrom = $("dateFrom").value;
      state.dto = $("dateTo").value;
      if (!state.dfrom || !state.dto) { alert("اختر تاريخ البداية والنهاية"); return; }
    }
    be.busy = true;
    var btn = $("beRefreshBtn");
    btn.disabled = true;
    btn.textContent = "جارٍ الحساب…";
    setBePill("pill-busy", "جارٍ الحساب…");
    $("beError").style.display = "none";
    var t0 = performance.now(), sig = beSignature();
    fetch(BASE + "/breakeven?" + buildQS() + "&basis=" + be.basis, { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.error) { throw new Error(d.detail || d.error); }
        be.loaded = true; be.data = d; be.sig = sig;
        $("beContent").style.display = "block";
        renderBreakeven(d);
        setBePill("pill-ok", "محسوب");
        $("beStale").innerHTML = "آخر حساب: <b>" + esc(d.last_updated) + "</b>";
        $("beDuration").innerHTML = "مدة الحساب: <b>" + esc(humanMs(Math.round(performance.now() - t0))) +
          "</b> <span style=\"opacity:.75\">(الخادم " + esc(humanMs(d.duration_ms)) + ")</span>";
        $("bePeriod").innerHTML = "الفترة: <b>" + esc(d.period_label) + "</b>";
        markBeDirty(); markVarDirty(); markOpsDirty();
      })
      .catch(function (e) {
        var box = $("beError");
        box.style.display = "block";
        box.innerHTML = '<div class="err-title">تعذّر حساب نقطة التعادل</div><div>' +
          esc(e && e.message ? e.message : e) + "</div>";
        setBePill("pill-err", "فشل");
      })
      .finally(function () {
        be.busy = false;
        btn.disabled = false;
        btn.textContent = "تحديث نقطة التعادل";
      });
  }

  function renderBreakeven(d) {
    var t = themeColors();
    var reached = d.be_orders !== null && d.orders >= d.be_orders;
    $("beStats").innerHTML =
      stat("المصاريف التشغيلية للفترة", fmt(d.expenses),
           fmt(d.expenses_per_day) + " لليوم · " + d.days + " يوم") +
      stat("مساهمة الطلب الواحد", fmt(d.contribution_per_order),
           "متوسط الطلب " + fmt(d.avg_net) + " − رسبي " + fmt(d.avg_cost) +
           (d.avg_commission ? " − عمولة " + fmt(d.avg_commission) : ""), "stat-accent") +
      stat("طلبات التعادل", d.be_orders === null ? "—" : fmt(Math.ceil(d.be_orders)),
           d.be_orders_per_day === null ? "المساهمة صفر أو سالبة" : fmt(d.be_orders_per_day) + " طلب لليوم") +
      stat("الطلبات الفعلية", fmt(d.orders), reached ? "تجاوزت التعادل" : "أقل من التعادل",
           reached ? "stat-pos" : "stat-neg") +
      stat("هامش الأمان", d.safety_pct === null ? "—" : d.safety_pct + "%",
           "نسبة الطلبات فوق التعادل", d.safety_pct !== null && d.safety_pct < 0 ? "stat-neg" : "") +
      stat("مبيعات التعادل", d.be_sales === null ? "—" : fmt(d.be_sales), "قبل الضريبة") +
      stat("صافي الربح التقديري", fmt(d.profit), "المساهمة − المصاريف", d.profit < 0 ? "stat-neg" : "stat-pos");

    // Curve: revenue and total cost by number of orders.
    var maxN = Math.max(10, Math.ceil(Math.max(d.orders, d.be_orders || 0) * 1.35));
    var step = Math.max(1, Math.ceil(maxN / 40));
    var xs = [];
    for (var n = 0; n <= maxN; n += step) { xs.push(n); }
    var axC = axisOpts();
    axC.x.type = "linear"; axC.x.min = 0; axC.x.max = xs[xs.length - 1];
    axC.x.title = { display: true, text: "عدد الطلبات", color: t.muted };
    axC.y.ticks.callback = function (v) { return fmtK(v); };
    var pts = function (fn) { return xs.map(function (x) { return { x: x, y: Math.round(fn(x)) }; }); };
    var marks = [];
    if (d.be_orders !== null) {
      marks.push({ type: "scatter", label: "نقطة التعادل", pointRadius: 7, pointStyle: "rectRot",
                   backgroundColor: t.warn, borderColor: t.warn,
                   data: [{ x: Math.round(d.be_orders), y: Math.round(d.be_orders * d.avg_net) }] });
    }
    marks.push({ type: "scatter", label: "الفعلي", pointRadius: 7,
                 backgroundColor: reached ? t.pos : t.neg, borderColor: t.card, borderWidth: 2,
                 data: [{ x: d.orders, y: Math.round(d.orders * d.avg_net) }] });
    draw("beCurveChart", "beCurve", {
      type: "line",
      data: { datasets: [
        { label: "الإيراد", data: pts(function (x) { return x * d.avg_net; }),
          borderColor: t.pos, backgroundColor: t.pos, pointRadius: 0, borderWidth: 2 },
        { label: d.avg_commission ? "إجمالي التكلفة (مصاريف + رسبي + عمولة)" : "إجمالي التكلفة (مصاريف + رسبي)",
          data: pts(function (x) { return d.expenses + x * (d.avg_cost + (d.avg_commission || 0)); }),
          borderColor: t.neg, backgroundColor: t.neg, pointRadius: 0, borderWidth: 2 },
        { label: "المصاريف الثابتة", data: pts(function () { return d.expenses; }),
          borderColor: t.muted, backgroundColor: t.muted, pointRadius: 0, borderDash: [5, 4], borderWidth: 1.5 }
      ].concat(marks) },
      options: baseOpts({ scales: axC, interaction: { mode: "nearest", intersect: false },
                          plugins: { legend: { display: true } } })
    }, "لا توجد بيانات");

    var daily = d.daily || [];
    var labels = daily.map(function (x) { return x.day.slice(5) + " " + x.weekday; });
    var axD = axisOpts();
    draw("beDailyChart", "beDaily", {
      type: "bar",
      data: { labels: labels, datasets: [
        { label: "الطلبات", data: daily.map(function (x) { return x.orders; }),
          backgroundColor: daily.map(function (x) {
            return alpha(x.be_orders !== null && x.orders >= x.be_orders ? t.pos : t.neg, "BB");
          }), borderRadius: 4 },
        { type: "line", label: "طلبات التعادل لليوم", data: daily.map(function (x) { return x.be_orders; }),
          borderColor: t.warn, backgroundColor: t.warn, borderDash: [6, 4], pointRadius: 0, borderWidth: 2 }
      ] },
      options: baseOpts({ scales: axD, plugins: { legend: { display: true } } })
    });

    var axCum = axisOpts();
    axCum.y.ticks.callback = function (v) { return fmtK(v); };
    draw("beCumChart", "beCum", {
      type: "line",
      data: { labels: labels, datasets: [
        { label: "المساهمة التراكمية", data: daily.map(function (x) { return x.cum_contribution; }),
          borderColor: t.accent, backgroundColor: alpha(t.accent, "33"), fill: true, tension: .25, pointRadius: 2 },
        { label: "المصاريف التراكمية", data: daily.map(function (x) { return x.cum_expenses; }),
          borderColor: t.neg, backgroundColor: t.neg, borderDash: [5, 4], pointRadius: 0, borderWidth: 2 }
      ] },
      options: baseOpts({ scales: axCum, plugins: { legend: { display: true } } })
    });

    var acc = d.by_account || [];
    hbar("beExpChart", "beExp", acc.map(function (a) { return a.name; }),
         [{ label: "المبلغ", data: acc.map(function (a) { return a.amount; }),
            backgroundColor: alpha(t.neg, "BB"), borderRadius: 4 }],
         { empty: "لا توجد مصاريف مرحّلة في " + (d.basis === "period" ? "الفترة" : "آخر " + d.months + " أشهر") });

    var notes = [];
    notes.push(d.basis === "period"
      ? "المصاريف: القيود المرحّلة على حسابات المصاريف من " + d.expense_from + " إلى " + d.expense_to + "."
      : "المصاريف: متوسط آخر " + d.months + " أشهر كاملة (" + d.expense_from + " ← " + d.expense_to +
        ") موزّعاً على أيام الفترة — لأن الإيجار والرواتب تُرحّل مرة في الشهر.");
    notes.push("حسابات تكلفة الإيرادات مستبعدة لأن تكلفة الرسبي هي التكلفة المتغيرة." +
               (d.commission ? " عمولة التطبيقات (" + fmt(d.commission) + ") محسوبة تكلفة متغيرة لكل طلب." : ""));
    notes.push({ company: "المصاريف للشركة كاملة.",
                 company_no_cc: "الفروع المحددة بدون مركز تكلفة، فالمصاريف للشركة كاملة — اربط مركز التكلفة في إعدادات نقطة البيع.",
                 branches: "المصاريف المرتبطة بمراكز تكلفة الفروع المحددة فقط." }[d.expense_scope] || "");
    if (d.no_recipe_pct) { notes.push(d.no_recipe_pct + "% من المبيعات أصناف بدون رسبي، فتكلفتها تقديرية."); }
    $("beNote").textContent = notes.join(" ");
  }

  /* ── Column picker ──────────────────────────────────────────────────── */

  function loadColumns(defaults) {
    var saved = null;
    try { saved = JSON.parse(localStorage.getItem("theDashColumns")); }
    catch (e) { saved = null; }
    var known = state.measures.map(function (m) { return m[0]; });
    if (saved && saved.length) {
      state.columns = saved.filter(function (k) { return known.indexOf(k) !== -1; });
    }
    if (!state.columns.length) { state.columns = (defaults || known).slice(); }
  }

  function saveColumns() {
    try { localStorage.setItem("theDashColumns", JSON.stringify(state.columns)); }
    catch (e) { /* private mode */ }
  }

  function buildColumnPicker(unavailable) {
    var na = unavailable || [];
    var panel = $("colPanel");
    panel.innerHTML = state.measures.map(function (m) {
      var off = na.indexOf(m[0]) !== -1;
      return '<label class="' + (off ? "off" : "") + '"><input type="checkbox" value="' +
        m[0] + '"' + (state.columns.indexOf(m[0]) !== -1 ? " checked" : "") + "> " +
        esc(m[1]) + (off ? " (غير متوفر)" : "") + "</label>";
    }).join("") +
      '<div class="colactions"><button type="button" id="colAll">الكل</button>' +
      '<button type="button" id="colNone">لا شيء</button></div>';

    Array.prototype.forEach.call(panel.querySelectorAll("input"), function (cb) {
      cb.addEventListener("change", applyColumns);
    });
    $("colAll").addEventListener("click", function () {
      state.columns = state.measures.map(function (m) { return m[0]; });
      refreshPicker();
    });
    $("colNone").addEventListener("click", function () {
      state.columns = ["net"];
      refreshPicker();
    });
  }

  function refreshPicker() {
    Array.prototype.forEach.call($("colPanel").querySelectorAll("input"), function (cb) {
      cb.checked = state.columns.indexOf(cb.value) !== -1;
    });
    saveColumns();
    if (state.rpt.data) { renderReport(state.rpt.data); }
  }

  function applyColumns() {
    // Keep the server's column order rather than click order.
    var checked = {};
    Array.prototype.forEach.call($("colPanel").querySelectorAll("input:checked"), function (cb) {
      checked[cb.value] = true;
    });
    state.columns = state.measures
      .map(function (m) { return m[0]; })
      .filter(function (k) { return checked[k]; });
    if (!state.columns.length) { state.columns = ["net"]; }
    saveColumns();
    if (state.rpt.data) { renderReport(state.rpt.data); }
  }

  /* ── Scope: which branches and teams are in play ───────────────────── */

  function buildScopePanel(cfg) {
    state.branches = cfg.branches || [];
    state.teams = cfg.teams || [];

    $("branchList").innerHTML = state.branches.map(function (b) {
      return '<label><input type="checkbox" data-kind="branch" value="' + b.id +
        '" checked> ' + esc(b.name) + "</label>";
    }).join("") || '<div class="empty">لا توجد نقاط بيع</div>';

    if (state.teams.length) {
      $("teamSec").style.display = "block";
      $("teamList").innerHTML = state.teams.map(function (t) {
        return '<label><input type="checkbox" data-kind="team" value="' + t.id +
          '" checked> ' + esc(t.name) + "</label>";
      }).join("");
    }

    $("branchAll").addEventListener("click", function () { toggleAll("branch"); });
    $("teamAll").addEventListener("click", function () { toggleAll("team"); });
    $("scopeApply").addEventListener("click", applyScope);
  }

  function boxes(kind) {
    return Array.prototype.slice.call(
      document.querySelectorAll('#scopePanel input[data-kind="' + kind + '"]'));
  }

  function toggleAll(kind) {
    var list = boxes(kind);
    var allOn = list.every(function (b) { return b.checked; });
    list.forEach(function (b) { b.checked = !allOn; });
  }

  function applyScope() {
    // An empty selection means "everything", which is exactly what the server
    // does when no ids are sent - so all-checked and none-checked agree.
    function picked(kind) {
      var list = boxes(kind);
      var on = list.filter(function (b) { return b.checked; })
                   .map(function (b) { return parseInt(b.value, 10); });
      return (on.length && on.length < list.length) ? on : [];
    }
    state.selBranches = picked("branch");
    state.selTeams = picked("team");

    var parts = [];
    parts.push(state.selBranches.length
      ? state.selBranches.length + " فروع" : "كل الفروع");
    if (state.selTeams.length) { parts.push(state.selTeams.length + " فرق"); }
    $("scopeLabel").textContent = parts.join(" · ");

    $("scopePanel").style.display = "none";
    markDirty();
    markRptDirty();
    markBeDirty(); markVarDirty(); markOpsDirty();
  }

  /* ── Wiring ────────────────────────────────────────────────────────── */

  function buildDimSelects(dims) {
    if (state.dimsLoaded || !dims) { return; }
    // Rendered by the server into the page, so the selects work before any
    // query has run.
    var g1 = $("grp1"), g2 = $("grp2");
    g1.innerHTML = "";
    g2.innerHTML = '<option value="none">لا شيء</option>';
    dims.layer1.forEach(function (d) {
      var o = document.createElement("option");
      o.value = d.key; o.textContent = d.label;
      g1.appendChild(o);
    });
    dims.layer2.forEach(function (d) {
      var o = document.createElement("option");
      o.value = d.key; o.textContent = d.label;
      g2.appendChild(o);
    });
    g1.value = state.group1;
    g2.value = state.group2;
    if (!g1.value) { g1.selectedIndex = 0; state.group1 = g1.value; }
    state.dimsLoaded = true;
  }

  function readConfig() {
    var el = document.getElementById("dashConfig");
    if (!el) { return null; }
    try { return JSON.parse(el.textContent); } catch (e) { return null; }
  }

  var SEG_REDRAW = {
    hrMetric: function () { drawHours(); }, hrSplit: function () { drawHours(); },
    hrRange: function () { drawHours(); }, typeMetric: function () { drawTypes(); },
    trendSplit: function () { drawTrend(); }, shortDim: function () { drawShortages(); },
    profitView: function () { drawProfit(); }, menuScope: function () { drawMenu(); }
  };

  function init() {
    try { setTheme(localStorage.getItem("theDashTheme") || "light"); }
    catch (e) { setTheme("dark"); }

    if (window.Chart) {
      Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
    }
    loadView();
    syncSegs();
    // View switches repaint from the payload in memory - no request.
    document.addEventListener("click", function (ev) {
      var b = ev.target.closest && ev.target.closest(".seg[data-pref] button");
      if (!b) { return; }
      var pref = b.parentNode.getAttribute("data-pref");
      VIEW[pref] = b.getAttribute("data-v");
      saveView();
      syncSegs();
      if (pref === "varView") { if (vr.loaded) { drawVariance(); } return; }
      if (state.loaded && state.lastData && SEG_REDRAW[pref]) { SEG_REDRAW[pref](); }
    });

    // Dimensions and measures are rendered into the page by the server and
    // cost no data query, so the controls are usable before the first refresh.
    var cfg = readConfig();
    if (cfg) {
      state.measures = cfg.measures || [];
      loadColumns(cfg.default_columns);
      buildColumnPicker([]);
      buildDimSelects(cfg.dimensions);
      buildScopePanel(cfg);
    }

    var today = new Date().toISOString().split("T")[0];
    $("dateFrom").value = today;
    $("dateTo").value = today;


    $("tabs").addEventListener("click", function (ev) {
      var btn = ev.target.closest(".tab");
      if (!btn) { return; }
      Array.prototype.forEach.call(document.querySelectorAll(".tab"), function (t) {
        t.classList.remove("active");
      });
      btn.classList.add("active");
      state.period = btn.dataset.period;
      if (state.period === "custom") {
        $("customRow").style.display = "flex";
      } else {
        $("customRow").style.display = "none";
        state.dfrom = "";
        state.dto = "";
        $("dRangeResult").textContent = "";
      }
      markDirty();
      markRptDirty();
      markBeDirty(); markVarDirty(); markOpsDirty();
    });

    $("dateFrom").addEventListener("change", function () { markDirty(); markRptDirty(); markBeDirty(); markVarDirty(); markOpsDirty(); });
    $("dateTo").addEventListener("change", function () { markDirty(); markRptDirty(); markBeDirty(); markVarDirty(); markOpsDirty(); });

    $("refreshBtn").addEventListener("click", refreshNow);
    $("idleBtn").addEventListener("click", refreshNow);
    $("rptRefreshBtn").addEventListener("click", loadReport);
    $("beRefreshBtn").addEventListener("click", loadBreakeven);
    $("opsRefreshBtn").addEventListener("click", loadOps);
    // Opened from an app menu (?focus=inventory|purchase|...): jump to that section and load it.
    (function () {
      var focus = new URLSearchParams(window.location.search).get("focus");
      if (!focus) { return; }
      if (focus === "pos") { return; }
      ops.tab = focus;
      $("opsCard").scrollIntoView({ behavior: "smooth", block: "start" });
      loadOps();
    })();
    $("varRefreshBtn").addEventListener("click", loadVariance);
    $("beBasis").addEventListener("click", function (ev) {
      var b = ev.target.closest("button");
      if (!b) { return; }
      be.basis = b.getAttribute("data-v");
      Array.prototype.forEach.call(this.querySelectorAll("button"), function (x) {
        x.classList.toggle("on", x === b);
      });
      markBeDirty(); markVarDirty(); markOpsDirty();
    });

    $("grp1").addEventListener("change", function () {
      state.group1 = this.value; markRptDirty();
    });
    $("grp2").addEventListener("change", function () {
      state.group2 = this.value; markRptDirty();
    });

    $("themeBtn").addEventListener("click", function () {
      setTheme(document.body.dataset.theme === "dark" ? "light" : "dark");
      // Repaint the charts from the already-fetched payload.
      // This does NOT call the server.
      if (state.loaded && state.lastData) { renderAll(state.lastData); }
      if (state.rpt.loaded && state.rpt.data) { renderReport(state.rpt.data); }
      if (be.loaded && be.data) { renderBreakeven(be.data); }
      if (ops.loaded && ops.data) { renderOps(ops.data); }
      if (vr.loaded && vr.data) { drawVariance(); }
    });

    $("dimTabs").addEventListener("click", function (ev) {
      var btn = ev.target.closest("[data-dim]");
      if (!btn) { return; }
      state.dim = btn.getAttribute("data-dim");
      Array.prototype.forEach.call($("dimTabs").querySelectorAll(".tab"), function (b) {
        b.classList.toggle("active", b === btn);
      });
      renderDims();   // client side only: all four were sent together
    });

    $("moreProdBtn").addEventListener("click", function () {
      state.prodExpanded = !state.prodExpanded;
      drawProducts();
    });

    $("rptBody").addEventListener("click", function (ev) {
      var row = ev.target.closest(".rpt-row1");
      if (!row) { return; }
      var i = row.dataset.idx;
      Array.prototype.forEach.call(document.querySelectorAll(".rpt-ch" + i), function (rw) {
        rw.style.display = rw.style.display === "none" ? "" : "none";
      });
      var car = $("car" + i);
      if (car) { car.classList.toggle("open"); }
    });

    $("colBtn").addEventListener("click", function (ev) {
      ev.stopPropagation();
      var p = $("colPanel");
      p.style.display = p.style.display === "block" ? "none" : "block";
    });
    document.addEventListener("click", function (ev) {
      if (!ev.target || !ev.target.closest) { return; }
      if (!ev.target.closest("#colWrap")) { $("colPanel").style.display = "none"; }
      if (!ev.target.closest("#scopeWrap")) { $("scopePanel").style.display = "none"; }
    });

    $("scopeBtn").addEventListener("click", function (ev) {
      ev.stopPropagation();
      var p = $("scopePanel");
      p.style.display = p.style.display === "block" ? "none" : "block";
    });

    $("stDetailsBtn").addEventListener("click", function () {
      var box = $("stTimings");
      var open = box.style.display === "block";
      box.style.display = open ? "none" : "block";
      this.textContent = open ? "تفاصيل الأداء ▾" : "إخفاء التفاصيل ▴";
    });

    $("btnPdf").addEventListener("click", function () { window.print(); });
    $("btnExcel").addEventListener("click", function () {
      window.open(BASE + "/export/excel?" + buildQS(), "_blank");
    });
    $("btnCsv").addEventListener("click", function () {
      window.open(BASE + "/export/csv?" + buildQS(), "_blank");
    });

    // Keeps the "how old is this data" label honest. Pure UI clock -
    // it never touches the server.
    setInterval(function () {
      if (state.loaded && !state.busy) { renderStatus(); }
      if (state.rpt.loaded && !state.rpt.busy) { renderRptStatus(); }
    }, 15000);

    renderStatus();
    renderRptStatus();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
