import ast, unittest
from pathlib import Path
from datetime import date, timedelta
source=ast.parse((Path(__file__).resolve().parents[1] / 'models/loyalty_program.py').read_text())
fn=next(n for n in source.body if isinstance(n,ast.ClassDef)).body
fn=next(n for n in fn if isinstance(n,ast.FunctionDef) and n.name=='_exclusive_period_bounds')
class Date:
    to_date=staticmethod(lambda x:x)
    context_today=staticmethod(lambda x:date.today())
ns={'fields':type('Fields',(),{'Date':Date}), 'timedelta':timedelta}
exec(compile(ast.fix_missing_locations(ast.Module(body=[fn],type_ignores=[])),'periods','exec'),ns)
class Program:
    ensure_one=lambda self:None
    date_from=None
    _exclusive_period_bounds=ns['_exclusive_period_bounds']
class PeriodTests(unittest.TestCase):
    def test_periods(self):
        p=Program()
        for period,anchor,day,start,end in [
            ('week',date(2026,11,3),date(2026,11,9),date(2026,11,3),date(2026,11,10)),
            ('week',date(2026,11,3),date(2026,11,10),date(2026,11,10),date(2026,11,17)),
            ('week',None,date(2026,10,30),date(2026,10,24),date(2026,10,31)),
            ('month',None,date(2028,2,29),date(2028,2,1),date(2028,3,1)),
            ('year',None,date(2026,12,31),date(2026,1,1),date(2027,1,1)),
            ('day',None,date(2026,12,31),date(2026,12,31),date(2027,1,1))]:
            with self.subTest(period=period,day=day):
                p.exclusive_limit_period=period;p.date_from=anchor
                self.assertEqual(p._exclusive_period_bounds(day),(start,end))
unittest.main()
