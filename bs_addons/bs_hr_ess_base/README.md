# ESS Base — بوابة الخدمة الذاتية

Foundation of the suite. Everything else plugs into it.

* **Portal** `/my/hr` — mobile-first, RTL, installable on the phone (PWA: manifest + service worker,
  offline page). Bottom navigation, "+" new-request catalogue, dashboard cards, notifications,
  profile, update-my-details request, end-of-service calculator (Saudi Labor Law art. 84/85, estimate),
  Gregorian + Hijri dates (Umm al-Qura via `hijridate`).
* **Approval engine** — `bs.hr.approval.flow` per request type (+ company + domain), ordered steps
  (manager, manager's manager, department, branch manager, coach, HR, group, users), step conditions,
  SLA + automatic escalation (hourly cron), delegation during absence, no self-approval, fallback
  manager → HR when nothing is configured. Decisions from the backend or from the portal ("My team").
* **`bs.hr.request.mixin`** — inherited by every request (see `DEVELOPER_GUIDE.md`).
* **Payroll adjustments ledger** `bs.hr.payroll.adjustment` — single source for every amount that hits a
  payslip (loan installments, custody, penalties, objections) with a link back to its source.
* **Login with mobile + SMS code** (optional, needs the `sms` module + IAP credits) for employees without
  e-mail; rate-limited, codes hashed, 5 attempts.
* **Grant access wizard** (employee list → Action → Grant self-service access): creates portal users.

Security: portal users have no ACL on HR models. Controllers resolve the employee of the logged user and
only touch records filtered on him (ID tampering returns 404 — covered by tests).

Groups: Branch Supervisor < HR Officer < HR Manager, ESS Payroll Officer, ESS Grievance Committee.
