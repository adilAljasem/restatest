# bs_hr_ess_appraisal — ESS Appraisal (Odoo 19)

Standalone, Community-compatible appraisal engine for restaurant companies, part of the
`bs_hr_ess` Employee Self-Service suite by Basic Solution.

`hr_appraisal` is an Odoo Enterprise module, so this module ships its own models
(`bs.hr.ess.appraisal`, cycles, templates, competencies). A bridge module to `hr_appraisal`
(e.g. `bs_hr_ess_appraisal_enterprise`) can be added later to sync appraisals both ways.

## Features
* Competencies (performance / behaviour / hygiene / customer / attendance) and weighted templates; a restaurant template and a probation template are shipped (noupdate).
* Cycles (annual / semi-annual / probation) for all employees, branches or departments; "Generate appraisals" creates one appraisal per employee (manager = employee manager).
* Flow: self review (portal) -> manager review (portal or backend) -> done -> appealed.
* Objective KPIs, frozen when the manager review starts or with "Refresh KPIs": attendance rate (working days of the employee calendar minus validated time off), late arrivals (first check-in of the day after the scheduled start + company grace minutes, `ess_late_grace_minutes` from `bs_hr_ess_attendance`, default 10) and disciplinary actions (`bs.hr.disciplinary`, when installed).
* Final score 0-100 = weighted manager scores, optionally blended with the attendance rate (template `kpi_weight`), and a rating label.
* Appeal within 15 days of completion: creates a `bs.hr.grievance` (type `appeal_appraisal`) when `bs_hr_ess_grievance` is installed, otherwise a to-do activity for HR officers.
* Daily cron: probation appraisal 15 days before `trial_date_end` + activity for the manager.
* Goals with progress updates from the portal, peer kudos (max 5 per day).

## Portal routes
`/my/hr/appraisals`, `/my/hr/appraisal/<id>`, `/my/hr/team/appraisals`, `/my/hr/team/appraisal/<id>`,
`/my/hr/goals`, `/my/hr/kudos`.
