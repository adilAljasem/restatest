/* Resta site: password toggle, provisioning status polling, account-number login. */
(function () {
    "use strict";

    function ready(fn) {
        if (document.readyState !== "loading") { fn(); } else { document.addEventListener("DOMContentLoaded", fn); }
    }

    ready(function () {
        // Show / hide password
        document.querySelectorAll(".rs-pw-toggle").forEach(function (btn) {
            btn.addEventListener("click", function () {
                var input = btn.parentElement.querySelector("input");
                input.type = input.type === "password" ? "text" : "password";
            });
        });

        // Provisioning status
        var status = document.querySelector(".rs-status[data-token]");
        if (status) {
            var token = status.dataset.token;
            var steps = status.querySelectorAll(".rs-step");
            var setStep = function (n) {
                steps.forEach(function (s, i) {
                    s.classList.toggle("is-done", i < n);
                    s.classList.toggle("is-active", i === n);
                });
            };
            var poll = function () {
                fetch("/resta/signup/status/" + token + "/json", { cache: "no-store" })
                    .then(function (r) { return r.json(); })
                    .then(function (d) {
                        if (d.ready) {
                            setStep(4);
                            status.querySelector(".rs-status-text").classList.add("d-none");
                            status.querySelector(".rs-account").textContent = d.account;
                            status.querySelector(".rs-go").href = d.login_url;
                            status.querySelector(".rs-ready").classList.remove("d-none");
                            return;
                        }
                        if (d.state === "failed") {
                            status.querySelector(".rs-failed").classList.remove("d-none");
                            return;
                        }
                        setStep(/تجهيز بيانات/.test(d.step || "") ? 2 : 1);
                        setTimeout(poll, 2500);
                    })
                    .catch(function () { setTimeout(poll, 4000); });
            };
            poll();
        }

        // Login by account number: resolve the restaurant's system, then post there.
        var form = document.querySelector("form.rs-login");
        if (form) {
            var alertBox = form.querySelector(".rs-alert");
            var showError = function (msg) { alertBox.textContent = msg; alertBox.classList.remove("d-none"); };
            var lookup = function () {
                var body = new FormData();
                body.append("account", form.account.value);
                body.append("csrf_token", form.csrf_token.value);
                return fetch("/resta/login/lookup", { method: "POST", body: body }).then(function (r) { return r.json(); });
            };
            form.addEventListener("submit", function (ev) {
                ev.preventDefault();
                if (!form.account.value || !form.login.value || !form.password.value) {
                    showError("أدخل رقم الحساب واسم المستخدم وكلمة المرور.");
                    return;
                }
                var btn = form.querySelector("button[type=submit]");
                btn.disabled = true;
                lookup().then(function (d) {
                    if (!d.ok) { showError(d.error); btn.disabled = false; return; }
                    var post = document.createElement("form");
                    post.method = "POST";
                    post.action = d.auth_url;
                    post.style.display = "none";
                    var add = function (k, v) {
                        var i = document.createElement("input");
                        i.type = "hidden"; i.name = k; i.value = v; post.appendChild(i);
                    };
                    // emails are stored in lower case; a username is kept as typed
                    var login = form.login.value.trim();
                    add("login", login.indexOf("@") > -1 ? login.toLowerCase() : login);
                    add("password", form.password.value);
                    add("account", d.account);
                    add("ts", d.ts);
                    add("sig", d.sig);
                    add("back", window.location.origin + "/resta/login?account=" + encodeURIComponent(d.account));
                    document.body.appendChild(post);
                    post.submit();
                }).catch(function () { showError("تعذّر الاتصال، حاول مرة أخرى."); btn.disabled = false; });
            });
            var forgot = document.querySelector(".rs-forgot");
            if (forgot) {
                forgot.addEventListener("click", function (ev) {
                    ev.preventDefault();
                    if (!form.account.value) { showError("أدخل رقم الحساب أولاً."); return; }
                    lookup().then(function (d) {
                        if (!d.ok) { showError(d.error); return; }
                        window.location.href = d.reset_url;
                    });
                });
            }
        }
    });
})();
