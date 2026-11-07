# -*- coding: utf-8 -*-
import logging
from datetime import datetime, time

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


_logger = logging.getLogger(__name__)


class PosBusinessDayRecomputeJob(models.Model):
    _name = 'pos.business.day.recompute.job'
    _description = 'POS Business Day Recompute Job'
    _order = 'create_date desc, id desc'

    name = fields.Char(required=True, default='Business Day Recompute Job')
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    requested_by_id = fields.Many2one(
        'res.users',
        string='Requested By',
        default=lambda self: self.env.user,
        readonly=True,
    )
    state = fields.Selection(
        [
            ('draft', 'Draft'),
            ('running', 'Running'),
            ('done', 'Done'),
            ('failed', 'Failed'),
            ('cancelled', 'Cancelled'),
        ],
        default='draft',
        required=True,
        index=True,
        copy=False,
    )
    recompute_scope = fields.Selection(
        [
            ('all', 'All Existing Records'),
            ('missing', 'Only Empty Business Dates'),
            ('date_range', 'Date Range'),
        ],
        string='Recompute Scope',
        default='all',
        required=True,
    )
    date_from = fields.Date(string='Date From')
    date_to = fields.Date(string='Date To')
    batch_size = fields.Integer(
        string='Batch Size',
        default=500,
        required=True,
        help="Maximum number of records processed in each cron run.",
    )
    last_order_id = fields.Integer(string='Last POS Order ID', copy=False)
    last_stock_move_id = fields.Integer(string='Last Stock Move ID', copy=False)
    total_order_count = fields.Integer(string='Total POS Orders', copy=False)
    total_stock_move_count = fields.Integer(string='Total Stock Moves', copy=False)
    processed_order_count = fields.Integer(string='Processed POS Orders', copy=False)
    processed_stock_move_count = fields.Integer(
        string='Processed Stock Moves',
        copy=False,
    )
    progress = fields.Float(string='Progress', compute='_compute_progress')
    started_at = fields.Datetime(string='Started At', copy=False)
    finished_at = fields.Datetime(string='Finished At', copy=False)
    error_message = fields.Text(string='Error Message', copy=False, readonly=True)

    @api.depends(
        'state',
        'total_order_count',
        'total_stock_move_count',
        'processed_order_count',
        'processed_stock_move_count',
    )
    def _compute_progress(self):
        for job in self:
            total = job.total_order_count + job.total_stock_move_count
            processed = job.processed_order_count + job.processed_stock_move_count
            if job.state == 'done':
                job.progress = 100.0
            elif total:
                job.progress = min((processed / total) * 100.0, 99.99)
            else:
                job.progress = 0.0

    @api.constrains('batch_size', 'recompute_scope', 'date_from', 'date_to')
    def _check_job_values(self):
        for job in self:
            if job.batch_size < 1 or job.batch_size > 10000:
                raise ValidationError(_(
                    "Batch Size must be between 1 and 10000."
                ))
            if job.date_from and job.date_to and job.date_from > job.date_to:
                raise ValidationError(_(
                    "Date From must be before Date To."
                ))
            if job.recompute_scope == 'date_range' and not (
                job.date_from or job.date_to
            ):
                raise ValidationError(_(
                    "Set Date From or Date To when using Date Range."
                ))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            company = self.env['res.company'].browse(
                vals.get('company_id') or self.env.company.id
            )
            self._check_no_active_job(company)
            vals.setdefault(
                'name',
                _("Business Day Recompute - %s", company.display_name),
            )
        return super().create(vals_list)

    def write(self, vals):
        result = super().write(vals)
        if {'company_id', 'state'} & set(vals):
            for job in self.filtered(lambda item: item.state in ('draft', 'running')):
                job._check_no_active_job(job.company_id)
        return result

    def _check_no_active_job(self, company):
        domain = [
            ('company_id', '=', company.id),
            ('state', 'in', ['draft', 'running']),
        ]
        if self:
            domain.append(('id', 'not in', self.ids))
        active_job = self.sudo().search(domain, limit=1)
        if active_job:
            raise UserError(_(
                "A business day recompute job is already running for %s.",
                company.display_name,
            ))

    def _date_domain(self, field_name):
        self.ensure_one()
        domain = []
        if self.date_from:
            start_dt = datetime.combine(self.date_from, time.min)
            domain.append((field_name, '>=', fields.Datetime.to_string(start_dt)))
        if self.date_to:
            end_dt = datetime.combine(self.date_to, time.max)
            domain.append((field_name, '<=', fields.Datetime.to_string(end_dt)))
        return domain

    def _domain_for_model(self, model_name):
        self.ensure_one()
        domain = [('company_id', '=', self.company_id.id)]
        if self.recompute_scope == 'missing':
            domain.append(('business_date', '=', False))
        elif self.recompute_scope == 'date_range':
            date_field = 'date_order' if model_name == 'pos.order' else 'date'
            domain += self._date_domain(date_field)
        return domain

    def _try_lock_for_processing(self):
        self.ensure_one()
        self.env.cr.execute(
            """
            SELECT id
              FROM pos_business_day_recompute_job
             WHERE id = %s
               AND state IN ('draft', 'running')
             FOR UPDATE SKIP LOCKED
            """,
            [self.id],
        )
        return bool(self.env.cr.fetchone())

    def _prepare_run(self):
        self.ensure_one()
        PosOrder = self.env['pos.order'].sudo()
        StockMove = self.env['stock.move'].sudo()
        total_order_count = PosOrder.search_count(self._domain_for_model('pos.order'))
        total_stock_move_count = StockMove.search_count(
            self._domain_for_model('stock.move')
        )
        self.write({
            'state': 'running',
            'started_at': fields.Datetime.now(),
            'finished_at': False,
            'error_message': False,
            'last_order_id': 0,
            'last_stock_move_id': 0,
            'total_order_count': total_order_count,
            'total_stock_move_count': total_stock_move_count,
            'processed_order_count': 0,
            'processed_stock_move_count': 0,
        })

    def _mark_done(self):
        self.ensure_one()
        self.write({
            'state': 'done',
            'finished_at': fields.Datetime.now(),
            'processed_order_count': self.total_order_count,
            'processed_stock_move_count': self.total_stock_move_count,
        })

    def _process_model_batch(self, model_name, last_id_field, processed_field):
        self.ensure_one()
        Model = self.env[model_name].sudo()
        domain = self._domain_for_model(model_name)
        domain.append(('id', '>', self[last_id_field]))
        records = Model.search(domain, order='id', limit=self.batch_size)
        if not records:
            return 0

        self.env.add_to_compute(Model._fields['business_date'], records)
        records.flush_recordset(['business_date'])

        new_processed = self[processed_field] + len(records)
        self.write({
            last_id_field: records[-1].id,
            processed_field: new_processed,
        })
        return len(records)

    def _process_next_batch(self):
        self.ensure_one()
        if not self._try_lock_for_processing():
            return False
        if self.state not in ('draft', 'running'):
            return False

        try:
            if self.state == 'draft':
                self._prepare_run()
                if not (self.total_order_count or self.total_stock_move_count):
                    self._mark_done()
                    return True

            processed = self._process_model_batch(
                'pos.order',
                'last_order_id',
                'processed_order_count',
            )
            if processed:
                return True

            processed = self._process_model_batch(
                'stock.move',
                'last_stock_move_id',
                'processed_stock_move_count',
            )
            if processed:
                return True

            self._mark_done()
            return True
        except Exception as error:
            _logger.exception("Business day recompute job %s failed", self.id)
            self.write({
                'state': 'failed',
                'finished_at': fields.Datetime.now(),
                'error_message': str(error),
            })
            return False

    @api.model
    def _cron_process_business_day_recompute_jobs(self, limit=1):
        jobs = self.sudo().search(
            [('state', 'in', ['draft', 'running'])],
            order='create_date, id',
            limit=limit,
        )
        for job in jobs:
            job._process_next_batch()
        return True

    def action_process_next_batch(self):
        for job in self:
            job.sudo()._process_next_batch()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'success',
                'sticky': False,
                'message': _('The next recompute batch has been processed.'),
            },
        }

    def action_cancel(self):
        for job in self:
            if job.state not in ('draft', 'running'):
                continue
            job.write({
                'state': 'cancelled',
                'finished_at': fields.Datetime.now(),
            })
        return True

    def action_retry(self):
        for job in self:
            if job.state not in ('failed', 'cancelled'):
                continue
            job._check_no_active_job(job.company_id)
            job.write({
                'state': 'draft',
                'started_at': False,
                'finished_at': False,
                'error_message': False,
                'last_order_id': 0,
                'last_stock_move_id': 0,
                'total_order_count': 0,
                'total_stock_move_count': 0,
                'processed_order_count': 0,
                'processed_stock_move_count': 0,
            })
        return True
