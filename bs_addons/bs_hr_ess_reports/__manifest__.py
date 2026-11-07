{
    'name': 'ESS HR Reports & Dashboard',
    'summary': 'HR dashboard (workforce, Saudization, attendance, requests, loans, documents, custody, '
               'grievances, performance) per branch + monthly PDF report to management',
    'version': '19.0.1.0.0',
    'category': 'Human Resources',
    'author': 'Basic Solution',
    'website': 'https://resta.sa',
    'license': 'LGPL-3',
    'depends': [
        'bs_hr_ess_attendance', 'bs_hr_ess_requests', 'bs_hr_ess_documents', 'bs_hr_ess_comms',
        'bs_hr_ess_grievance', 'bs_hr_ess_appraisal',
    ],
    'data': [
        'security/ir.model.access.csv',
        'views/workforce_report_views.xml',
        'views/dashboard_views.xml',
        'views/res_config_settings_views.xml',
        'report/monthly_report.xml',
        'data/ir_cron_data.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'bs_hr_ess_reports/static/src/dashboard/*',
        ],
    },
    'installable': True,
}
