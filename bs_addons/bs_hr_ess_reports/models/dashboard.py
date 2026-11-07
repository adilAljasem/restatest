import base64
from datetime import timedelta

from odoo import api, fields, models
from odoo.fields import Domain


class BsHrEssDashboard(models.AbstractModel):
    _name = 'bs.hr.ess.dashboard'
    _description = 'ESS HR dashboard'

    # ------------------------------------------------------------------
    def _scope(self, location_id=None):
        companies = self.env.companies.ids
        loc = int(location_id) if location_id else False
        return companies, loc

    def _emp_domain(self, location_id=None):
        companies, loc = self._scope(location_id)
        domain = Domain('company_id', 'in', companies)
        if loc:
            domain &= Domain('work_location_id', '=', loc)
        return domain

    def _act(self, name, model, domain, views=('list', 'form'), context=None):
        return {'type': 'ir.actions.act_window', 'name': name, 'res_model': model,
                'domain': list(domain), 'views': [[False, v] for v in views], 'context': context or {}}

    @api.model
    def get_data(self, location_id=None):
        """All KPIs of the dashboard (read in sudo, scoped to the allowed companies)."""
        if not self.env.user.has_group('bs_hr_ess_base.group_ess_hr_officer'):
            return {}
        env = self.sudo().env
        now = fields.Datetime.now()
        today = fields.Date.context_today(self)
        month_start = today.replace(day=1)
        companies, loc = self._scope(location_id)
        emp_dom = self._emp_domain(location_id)
        Emp = env['hr.employee']
        employees = Emp.search(emp_dom)
        emp_ids = employees.ids
        on_emp = Domain('employee_id', 'in', emp_ids)
        tiles = []

        def tile(group, key, label, value, action=None, tone='info', suffix=''):
            tiles.append({'group': group, 'key': key, 'label': label, 'value': value, 'tone': tone,
                          'suffix': suffix, 'action': action})

        # ---------------- workforce
        saudi = sum(1 for e in employees if e.sudo().country_id.code == 'SA')
        headcount = len(employees)
        saudization = round(100.0 * saudi / headcount, 1) if headcount else 0.0
        tile('workforce', 'headcount', self.env._('Employees'), headcount,
             self._act(self.env._('Employees'), 'hr.employee', emp_dom, ('list', 'kanban', 'form')))
        tile('workforce', 'saudization', self.env._('Saudization'), saudization,
             self._act(self.env._('Workforce'), 'bs.hr.ess.workforce.report',
                       [('company_id', 'in', companies)] + ([('work_location_id', '=', loc)] if loc else []),
                       ('pivot', 'graph'), {'pivot_row_groupby': ['work_location_id'],
                                            'pivot_column_groupby': ['nationality_group']}),
             'success' if saudization >= 30 else 'warning', '%')
        Version = env['hr.version']
        hires = Version.search_count([('employee_id', 'in', emp_ids),
                                      ('contract_date_start', '>=', today - timedelta(days=30))])
        leavers_12m = Emp.with_context(active_test=False).search_count(
            emp_dom & Domain('departure_date', '>=', today - timedelta(days=365)))
        turnover = round(100.0 * leavers_12m / headcount, 1) if headcount else 0.0
        tile('workforce', 'hires', self.env._('New hires (30 days)'), hires)
        tile('workforce', 'turnover', self.env._('Turnover (12 months)'), turnover,
             tone='danger' if turnover > 30 else 'info', suffix='%')
        probation = Version.search([('employee_id', 'in', emp_ids), ('trial_date_end', '>=', today),
                                    ('trial_date_end', '<=', today + timedelta(days=30))])
        tile('workforce', 'probation', self.env._('Probation ending (30 days)'), len(probation.employee_id),
             self._act(self.env._('Probation ending'), 'hr.employee', [('id', 'in', probation.employee_id.ids)]),
             'warning' if probation else 'info')

        # ---------------- attendance
        Att = env['hr.attendance']
        present = Att.search_count(on_emp & Domain('check_out', '=', False)
                                   & Domain('check_in', '>=', now - timedelta(hours=16)))
        today_att = Att.search(on_emp & Domain('check_in', '>=', fields.Datetime.to_datetime(today)
                                                - timedelta(hours=3)))
        late_today = len(today_att.filtered(lambda a: a.ess_late_minutes > 0).employee_id)
        late_minutes = int(Att._read_group(on_emp & Domain('date', '>=', month_start), [],
                                           ['ess_late_minutes:sum'])[0][0] or 0)
        suspicious = Att.search_count(on_emp & Domain('ess_suspicious', '=', True)
                                      & Domain('check_in', '>=', now - timedelta(days=7)))
        rate = round(100.0 * present / headcount, 1) if headcount else 0.0
        tile('attendance', 'present', self.env._('Present now'), present,
             self._act(self.env._('Present now'), 'hr.attendance', on_emp & Domain('check_out', '=', False)),
             'success', '/ %s' % headcount)
        tile('attendance', 'presence_rate', self.env._('Presence rate now'), rate, tone='info', suffix='%')
        tile('attendance', 'late_today', self.env._('Late today'), late_today,
             self._act(self.env._('Late today'), 'hr.attendance', [('id', 'in', today_att.filtered('ess_late_minutes').ids)]),
             'warning' if late_today else 'info')
        tile('attendance', 'late_minutes', self.env._('Late minutes this month'), late_minutes,
             self._act(self.env._('Lateness'), 'hr.attendance', on_emp & Domain('date', '>=', month_start)
                       & Domain('ess_late_minutes', '>', 0), ('pivot', 'list')))
        tile('attendance', 'suspicious', self.env._('Suspicious check-ins (7 days)'), suspicious,
             self._act(self.env._('Suspicious check-ins'), 'hr.attendance', on_emp & Domain('ess_suspicious', '=', True)),
             'danger' if suspicious else 'info')

        # ---------------- requests & approvals
        Line = env['bs.hr.approval.line']
        line_dom = Domain('company_id', 'in', companies) & Domain('employee_id', 'in', emp_ids)
        pending = Line.search(line_dom & Domain('state', '=', 'pending'))
        breached = pending.filtered(lambda l: l.deadline and l.deadline < now)
        decided = Line.search(line_dom & Domain('decided_on', '>=', now - timedelta(days=30))
                              & Domain('state', 'in', ('approved', 'rejected')))
        avg_hours = round(sum(decided.mapped('duration_hours')) / len(decided), 1) if decided else 0.0
        tile('requests', 'pending', self.env._('Requests waiting approval'), len(pending),
             self._act(self.env._('Pending approvals'), 'bs.hr.approval.line', line_dom & Domain('state', '=', 'pending')),
             'warning' if pending else 'info')
        tile('requests', 'sla', self.env._('Overdue approvals'), len(breached),
             self._act(self.env._('Overdue approvals'), 'bs.hr.approval.line', [('id', 'in', breached.ids)]),
             'danger' if breached else 'success')
        tile('requests', 'avg_hours', self.env._('Average approval time'), avg_hours, suffix=self.env._('h'))
        on_leave = env['hr.leave'].search_count(on_emp & Domain('state', '=', 'validate')
                                                & Domain('date_from', '<=', now) & Domain('date_to', '>=', now))
        tile('requests', 'on_leave', self.env._('On leave today'), on_leave)

        # ---------------- loans / custody
        loans = env['bs.hr.loan'].search(on_emp & Domain('state', 'in', ('approved', 'done'))
                                         & Domain('remaining_amount', '>', 0))
        currency = self.env.company.currency_id
        tile('finance', 'loans', self.env._('Outstanding loans'), round(sum(loans.mapped('remaining_amount')), 2),
             self._act(self.env._('Loans'), 'bs.hr.loan', [('id', 'in', loans.ids)]), suffix=currency.symbol or '')
        holdings = env['bs.hr.custody.holding'].search(on_emp & Domain('state', '=', 'held'))
        tile('finance', 'custody', self.env._('Custody held'), round(sum(holdings.mapped('value')), 2),
             self._act(self.env._('Custody'), 'bs.hr.custody.holding', [('id', 'in', holdings.ids)]),
             suffix=currency.symbol or '')
        lost = env['bs.hr.custody.holding'].search_count(on_emp & Domain('state', 'in', ('lost', 'damaged')))
        tile('finance', 'custody_lost', self.env._('Custody lost / damaged'), lost, tone='warning' if lost else 'info')
        adjust = env['bs.hr.payroll.adjustment'].search(on_emp & Domain('state', '=', 'pending'))
        tile('finance', 'adjustments', self.env._('Pending payroll adjustments'), len(adjust),
             self._act(self.env._('Pending adjustments'), 'bs.hr.payroll.adjustment', [('id', 'in', adjust.ids)]))

        # ---------------- documents
        Doc = env['bs.hr.document']
        expired = Doc.search(on_emp & Domain('state', '=', 'expired'))
        expiring = Doc.search(on_emp & Domain('state', '=', 'expiring'))
        tile('documents', 'doc_expired', self.env._('Expired documents'), len(expired),
             self._act(self.env._('Expired documents'), 'bs.hr.document', [('id', 'in', expired.ids)]),
             'danger' if expired else 'success')
        tile('documents', 'doc_expiring', self.env._('Expiring soon'), len(expiring),
             self._act(self.env._('Expiring documents'), 'bs.hr.document', [('id', 'in', expiring.ids)]),
             'warning' if expiring else 'info')
        health = expired.filtered(lambda d: d.type_id.block_attendance)
        tile('documents', 'health_expired', self.env._('Expired health certificates'), len(health.employee_id),
             tone='danger' if health else 'success')

        # ---------------- people
        grievances = env['bs.hr.grievance'].search_count(
            [('company_id', 'in', companies), ('state', 'in', ('received', 'investigating', 'appealed'))]
            + ([('work_location_id', '=', loc)] if loc else []))
        tile('people', 'grievances', self.env._('Open grievances'), grievances, tone='warning' if grievances else 'info')
        acks = env['bs.hr.announcement'].search([('company_ids', 'in', companies), ('state', '=', 'published'),
                                                 ('require_ack', '=', True)]) \
            if 'company_ids' in env['bs.hr.announcement']._fields else env['bs.hr.announcement']
        ack_rate = round(sum(acks.mapped('ack_rate')) / len(acks), 1) if acks else 0.0
        tile('people', 'ack_rate', self.env._('Circulars acknowledged'), ack_rate, suffix='%')
        appraisals = env['bs.hr.ess.appraisal'].search(on_emp & Domain('state', '=', 'done')
                                                       & Domain('write_date', '>=', now - timedelta(days=365)))
        avg_score = round(sum(appraisals.mapped('final_score')) / len(appraisals), 1) if appraisals else 0.0
        tile('people', 'appraisal', self.env._('Average appraisal score'), avg_score, suffix='/100')

        # ---------------- per branch
        branches = []
        Loc = env['hr.work.location']
        for branch in Loc.search([('company_id', 'in', companies)] + ([('id', '=', loc)] if loc else [])):
            b_emps = employees.filtered(lambda e, b=branch: e.work_location_id == b)
            if not b_emps:
                continue
            b_late = Att._read_group([('employee_id', 'in', b_emps.ids), ('date', '>=', month_start)], [],
                                     ['ess_late_minutes:sum'])[0][0]
            branches.append({
                'id': branch.id, 'name': branch.name, 'headcount': len(b_emps),
                'saudi': sum(1 for e in b_emps if e.sudo().country_id.code == 'SA'),
                'present': Att.search_count([('employee_id', 'in', b_emps.ids), ('check_out', '=', False),
                                             ('check_in', '>=', now - timedelta(hours=16))]),
                'late_minutes': int(b_late or 0),
                'doc_expired': Doc.search_count([('employee_id', 'in', b_emps.ids), ('state', '=', 'expired')]),
                'pending': Line.search_count([('employee_id', 'in', b_emps.ids), ('state', '=', 'pending')]),
            })
        locations = [{'id': l.id, 'name': l.name} for l in Loc.search([('company_id', 'in', companies)])]
        return {'tiles': tiles, 'branches': branches, 'locations': locations,
                'location_id': loc, 'today': fields.Date.to_string(today)}

    @api.model
    def _cron_monthly_report(self):
        report = self.env.ref('bs_hr_ess_reports.action_report_monthly_hr')
        for company in self.env['res.company'].search([]):
            hr_managers = self.env.ref('bs_hr_ess_base.group_ess_hr_manager').all_user_ids.filtered(
                lambda u: company in u.company_ids and u.id != 1 and u.email)
            partners = hr_managers.partner_id | company.ess_monthly_report_partner_ids
            if not partners:
                continue
            ctx_self = self.with_company(company).with_context(allowed_company_ids=company.ids)
            pdf, _fmt = self.env['ir.actions.report'].with_company(company).with_context(
                allowed_company_ids=company.ids)._render_qweb_pdf(report, res_ids=company.ids)
            attachment = self.env['ir.attachment'].create({
                'name': ctx_self.env._('HR report %s.pdf', fields.Date.today().strftime('%Y-%m')),
                'datas': base64.b64encode(pdf), 'res_model': 'res.company', 'res_id': company.id,
            })
            self.env['mail.mail'].sudo().create({
                'subject': ctx_self.env._('Monthly HR report - %s', company.name),
                'body_html': ctx_self.env._('<p>Please find attached the monthly HR report.</p>'),
                'recipient_ids': [(6, 0, partners.ids)],
                'attachment_ids': [(6, 0, attachment.ids)],
                'email_from': company.email or self.env.user.email,
            }).send()
