{
    'name': 'ESS POS bridge - attendance before cashing, labor cost vs sales',
    'summary': 'Cashiers must be checked in at the branch to open the POS; labor cost / sales ratio per branch',
    'version': '19.0.1.0.0',
    'category': 'Human Resources/Attendances',
    'author': 'Basic Solution',
    'website': 'https://resta.sa',
    'license': 'LGPL-3',
    'depends': ['bs_hr_ess_attendance', 'point_of_sale', 'hr_hourly_cost'],
    'data': [
        'security/ir.model.access.csv',
        'views/pos_config_views.xml',
        'views/labor_cost_report_views.xml',
    ],
    'installable': True,
}
