from datetime import datetime, time

from werkzeug.exceptions import NotFound

from odoo import _, fields, http
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.http import request

from odoo.addons.bs_hr_ess_base.controllers.main import EssPortalBase

from ..models.kudos import BADGE_ICONS, DAILY_LIMIT


class EssAppraisalPortal(EssPortalBase):

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _appr_get_own(self, appraisal_id, employee):
        return self._ess_get_own_record('bs.hr.ess.appraisal', appraisal_id, employee)

    def _appr_team_domain(self, employee):
        team = employee._ess_team()
        return ['&', ('employee_id', '!=', employee.id),
                '|', ('manager_id', '=', employee.id), ('employee_id', 'in', team.ids)]

    def _appr_get_team(self, appraisal_id, employee):
        record = request.env['bs.hr.ess.appraisal'].sudo().search(
            [('id', '=', int(appraisal_id))] + self._appr_team_domain(employee), limit=1)
        if not record:
            raise NotFound()
        return record

    @staticmethod
    def _appr_scores(post, prefix):
        scores = {}
        for key, value in post.items():
            if key.startswith(prefix) and key[len(prefix):].isdigit() and value in ('1', '2', '3', '4', '5'):
                scores[int(key[len(prefix):])] = value
        return scores

    def _appr_values(self, record):
        rating_label = dict(record._fields['rating']._description_selection(record.env)).get(record.rating, '')
        return {'record': record, 'rating_label': rating_label, 'flash': self._ess_pop_flash()}

    # ------------------------------------------------------------------
    # employee: appraisals
    # ------------------------------------------------------------------
    @http.route(['/my/hr/appraisals'], type='http', auth='user', website=True)
    def ess_appraisals(self, **kw):
        employee = self._ess_employee()
        if not employee:
            return self._ess_no_employee()
        records = request.env['bs.hr.ess.appraisal'].sudo().search(
            [('employee_id', '=', employee.id), ('state', '!=', 'cancelled')], limit=100)
        return self._ess_render('bs_hr_ess_appraisal.ess_appraisals', employee, records=records,
                                flash=self._ess_pop_flash(), page_name='ess_appraisals')

    @http.route(['/my/hr/appraisal/<int:appraisal_id>'], type='http', auth='user', website=True,
                methods=['GET', 'POST'])
    def ess_appraisal(self, appraisal_id, **post):
        employee = self._ess_employee()
        if not employee:
            return self._ess_no_employee()
        record = self._appr_get_own(appraisal_id, employee)
        url = record._ess_portal_url()
        if record.state == 'cancelled':
            raise NotFound()
        if request.httprequest.method == 'POST':
            user_rec = record.with_user(request.env.user).sudo()
            action = post.get('action')
            try:
                if action in ('save', 'submit'):
                    if record.state != 'self_review':
                        return self._ess_error_redirect(url, _('The self review is already submitted.'))
                    scores = self._appr_scores(post, 'score_')
                    for line in record.line_ids:
                        vals = {'self_comment': (post.get('comment_%s' % line.id) or '').strip() or False}
                        if line.id in scores:
                            vals['self_score'] = scores[line.id]
                        line.write(vals)
                    record.write({'self_comment': (post.get('self_comment') or '').strip() or False})
                    if action == 'submit':
                        user_rec.action_submit_self_review()
                        return self._ess_ok_redirect(url, _('Your self review was sent to your manager.'))
                    return self._ess_ok_redirect(url, _('Draft saved.'))
                if action == 'acknowledge':
                    user_rec.action_acknowledge()
                    return self._ess_ok_redirect(url, _('Thank you, the result is acknowledged.'))
                if action == 'appeal':
                    user_rec.action_appeal(post.get('appeal_reason'))
                    return self._ess_ok_redirect(url, _('Your appeal was sent to HR.'))
            except (UserError, ValidationError, AccessError) as e:
                request.env.cr.rollback()
                return self._ess_error_redirect(url, e.args[0])
            raise NotFound()
        return self._ess_render('bs_hr_ess_appraisal.ess_appraisal', employee,
                                ess_back='/my/hr/appraisals', **self._appr_values(record))

    # ------------------------------------------------------------------
    # manager: team appraisals
    # ------------------------------------------------------------------
    @http.route(['/my/hr/team/appraisals'], type='http', auth='user', website=True)
    def ess_team_appraisals(self, state='todo', **kw):
        employee = self._ess_employee()
        if not employee:
            return self._ess_no_employee()
        domain = self._appr_team_domain(employee)
        if state == 'todo':
            domain += [('state', '=', 'manager_review')]
        else:
            domain += [('state', '!=', 'cancelled')]
        records = request.env['bs.hr.ess.appraisal'].sudo().search(domain, limit=200)
        return self._ess_render('bs_hr_ess_appraisal.ess_team_appraisals', employee, records=records,
                                state=state, flash=self._ess_pop_flash())

    @http.route(['/my/hr/team/appraisal/<int:appraisal_id>'], type='http', auth='user', website=True,
                methods=['GET', 'POST'])
    def ess_team_appraisal(self, appraisal_id, **post):
        employee = self._ess_employee()
        if not employee:
            return self._ess_no_employee()
        record = self._appr_get_team(appraisal_id, employee)
        url = record._ess_team_portal_url()
        if request.httprequest.method == 'POST':
            action = post.get('action')
            if action not in ('save', 'done', 'refresh'):
                raise NotFound()
            if record.state != 'manager_review':
                return self._ess_error_redirect(url, _('This appraisal is not waiting for your review.'))
            try:
                if action == 'refresh':
                    record.action_refresh_kpis()
                    return self._ess_ok_redirect(url, _('KPIs refreshed.'))
                scores = self._appr_scores(post, 'score_')
                for line in record.line_ids:
                    vals = {'comment': (post.get('comment_%s' % line.id) or '').strip() or False}
                    if line.id in scores:
                        vals['manager_score'] = scores[line.id]
                    line.write(vals)
                vals = {'manager_comment': (post.get('manager_comment') or '').strip() or False}
                if record.is_probation and post.get('probation_decision') in ('confirm', 'extend', 'terminate'):
                    vals['probation_decision'] = post['probation_decision']
                record.write(vals)
                if action == 'done':
                    # identity kept for the chatter; access already checked through the team domain
                    record.with_user(request.env.user).sudo().action_manager_done()
                    return self._ess_ok_redirect('/my/hr/team/appraisals', _('Appraisal completed.'))
                return self._ess_ok_redirect(url, _('Draft saved.'))
            except (UserError, ValidationError, AccessError) as e:
                request.env.cr.rollback()
                return self._ess_error_redirect(url, e.args[0])
        return self._ess_render('bs_hr_ess_appraisal.ess_team_appraisal', employee,
                                ess_back='/my/hr/team/appraisals', **self._appr_values(record))

    # ------------------------------------------------------------------
    # goals
    # ------------------------------------------------------------------
    @http.route(['/my/hr/goals'], type='http', auth='user', website=True, methods=['GET', 'POST'])
    def ess_goals(self, **post):
        employee = self._ess_employee()
        if not employee:
            return self._ess_no_employee()
        if request.httprequest.method == 'POST':
            goal = self._ess_get_own_record('bs.hr.goal', post.get('goal_id') or '', employee)
            try:
                goal._ess_update_progress(int(post.get('progress') or 0), (post.get('note') or '').strip())
            except (UserError, ValidationError, ValueError) as e:
                request.env.cr.rollback()
                return self._ess_error_redirect('/my/hr/goals', e.args[0])
            return self._ess_ok_redirect('/my/hr/goals', _('Progress updated.'))
        goals = request.env['bs.hr.goal'].sudo().search([('employee_id', '=', employee.id)], limit=100)
        return self._ess_render('bs_hr_ess_appraisal.ess_goals', employee, goals=goals,
                                flash=self._ess_pop_flash(), ess_back='/my/hr')

    # ------------------------------------------------------------------
    # kudos
    # ------------------------------------------------------------------
    def _kudos_colleagues(self, employee):
        return request.env['hr.employee'].sudo().search([
            ('company_id', '=', employee.company_id.id), ('id', '!=', employee.id)], order='name')

    @http.route(['/my/hr/kudos'], type='http', auth='user', website=True, methods=['GET', 'POST'])
    def ess_kudos(self, tab='received', **post):
        employee = self._ess_employee()
        if not employee:
            return self._ess_no_employee()
        Kudos = request.env['bs.hr.kudos'].sudo()
        if request.httprequest.method == 'POST':
            to_id = post.get('to_employee_id') or ''
            colleague = self._kudos_colleagues(employee).filtered(
                lambda e: str(e.id) == to_id) if to_id.isdigit() else False
            if not colleague:
                return self._ess_error_redirect('/my/hr/kudos?tab=send', _('Please choose a colleague.'))
            message = (post.get('message') or '').strip()
            if not message:
                return self._ess_error_redirect('/my/hr/kudos?tab=send', _('Please write a message.'))
            badge = post.get('badge') if post.get('badge') in BADGE_ICONS else 'thanks'
            try:
                Kudos.create({'from_employee_id': employee.id, 'to_employee_id': colleague.id,
                              'message': message[:1000], 'badge': badge,
                              'company_id': employee.company_id.id})
            except ValidationError as e:
                request.env.cr.rollback()
                return self._ess_error_redirect('/my/hr/kudos?tab=send', e.args[0])
            return self._ess_ok_redirect('/my/hr/kudos?tab=sent', _('Kudos sent to %s!', colleague.name))
        received = Kudos.search([('to_employee_id', '=', employee.id)], limit=100)
        sent = Kudos.search([('from_employee_id', '=', employee.id)], limit=100)
        today = fields.Date.context_today(employee)
        sent_today = Kudos.search_count([
            ('from_employee_id', '=', employee.id),
            ('date', '>=', datetime.combine(today, time.min)), ('date', '<=', datetime.combine(today, time.max))])
        badges = Kudos._fields['badge']._description_selection(request.env)
        return self._ess_render('bs_hr_ess_appraisal.ess_kudos', employee, tab=tab, received=received, sent=sent,
                                colleagues=self._kudos_colleagues(employee) if tab == 'send' else [],
                                badges=badges, badge_icons=BADGE_ICONS,
                                remaining=max(0, DAILY_LIMIT - sent_today),
                                flash=self._ess_pop_flash(), ess_back='/my/hr')
