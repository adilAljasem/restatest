# ESS Attendance — الحضور بالموقع / الباركود المتغير / بصمة الوجه

Configure per branch (Self-Service > Configuration > Branches > *Attendance check-in*), override per
employee (Employee > Self-Service tab):

* **Location** — browser GPS, distance checked server side (Haversine) against the branch point(s) and
  radius. Anti-fraud: accuracy limit, mock-GPS hint (accuracy < 1 m), impossible-travel flag.
* **Rotating QR** — a tablet in the branch opens `/hr/kiosk/<token>`, activated once with the kiosk key;
  QR = HMAC(secret, branch, time/10s), never stored. Anti-fraud: one period tolerance, one use per employee
  and code, combine with location so a photographed code is useless.
* **Face** — face-api.js (bundled, no CDN) computes a 128-d signature on the phone; random liveness challenge
  (blink or head turn); the server compares it with the enrolled signature (encrypted at rest). Anti-fraud:
  threshold setting, optional evidence photo kept N days, enrollment only when HR unlocks it, explicit PDPL
  consent stored.

Combination: **all** selected methods required, or **any** of them (employee chooses).

Also: device binding (first phone active, a new phone needs HR approval), rate limit (8 tries / min),
audit log of every attempt, attendance correction requests (approval flow), lateness / early leave
minutes from the working schedule with a grace period, "present now", lateness pivot, suspicious list,
photos purge cron, team attendance in the manager portal. Expired-document blocking comes from
`bs_hr_ess_documents` via the `_ess_attendance_precheck` hook.

Work entries / payroll: Odoo's standard *attendance-based work entries* (Enterprise payroll) consume these
`hr.attendance` records as usual — nothing to configure here. Auto check-out uses Odoo's native setting.

Notes: camera and GPS need **HTTPS** (Odoo.sh is). Scanning the kiosk QR with the native camera app opens
the portal and punches directly. Liveness runs in the browser: it stops photos/screens held in front of
the camera, not a determined attacker with a modified client — combine face with location or QR for
sensitive branches.
