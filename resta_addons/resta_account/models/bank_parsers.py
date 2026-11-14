"""Readers for bank statement files: Excel / CSV exports of Saudi banks, CAMT.053 (XML),
OFX / QFX and MT940. Every reader returns plain dicts:

    {"date": date, "amount": float (+ in, - out), "label": str, "ref": str, "uid": str}

Written from the public format descriptions; nothing here depends on Odoo.
"""
import csv
import io
import re
from datetime import date, datetime, timedelta

ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩٫٬", "0123456789.,")

HEADERS = {
    "date": ["transaction date", "posting date", "trans date", "date", "booking date", "value date",
             "تاريخ العملية", "تاريخ الحركة", "تاريخ القيد", "تاريخ المعاملة", "التاريخ", "تاريخ"],
    "label": ["transaction details", "description", "details", "narrative", "narration", "particulars", "memo",
              "remarks", "البيان", "الوصف", "التفاصيل", "تفاصيل العملية", "وصف العملية", "الشرح", "ملاحظات"],
    "debit": ["debit amount", "debit", "withdrawal", "withdrawals", "money out", "dr", "المبلغ المدين", "مدين",
              "سحب", "مسحوبات", "الخصم"],
    "credit": ["credit amount", "credit", "deposit", "deposits", "money in", "cr", "المبلغ الدائن", "دائن",
               "إيداع", "ايداع", "إيداعات", "الإضافة"],
    "amount": ["transaction amount", "amount", "مبلغ العملية", "المبلغ", "القيمة"],
    "ref": ["reference number", "reference", "ref no", "ref", "transaction id", "cheque no", "رقم المرجع",
            "الرقم المرجعي", "المرجع", "رقم العملية"],
    "balance": ["running balance", "balance", "الرصيد بعد العملية", "الرصيد"],
}


def normalize(text):
    text = str(text or "").strip().lower().translate(ARABIC_DIGITS)
    text = re.sub("[إأآ]", "ا", text).replace("ـ", "")
    return re.sub(r"[\s_:./()-]+", " ", text).strip()


_NORMALIZED = {key: [normalize(s) for s in values] for key, values in HEADERS.items()}


# ---------------------------------------------------------------------- values
def parse_amount(value):
    """1,250.00 / (1,250.00) / 1,250.00- / SAR 1,250 / ١٬٢٥٠٫٠٠ -> float; '' -> None."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).translate(ARABIC_DIGITS).strip()
    if not text:
        return None
    negative = text.startswith("(") and text.endswith(")") or text.endswith("-") or text.startswith("-")
    text = re.sub(r"[^\d.,]", "", text)
    if not text:
        return None
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):  # 1.250,00
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        head, _sep, tail = text.rpartition(",")
        text = head.replace(",", "") + ("." + tail if len(tail) in (1, 2) else tail)
    amount = float(text)
    return -amount if negative else amount


DATE_FORMATS_DAY_FIRST = ["%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%d-%m-%y", "%d %b %Y", "%d %B %Y",
                          "%d-%b-%Y", "%d-%b-%y"]
DATE_FORMATS_MONTH_FIRST = ["%m/%d/%Y", "%m-%d-%Y", "%m/%d/%y"]
DATE_FORMATS_ISO = ["%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"]


def parse_date(value, day_first=True):
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)) and 20000 < value < 80000:  # Excel serial number
        return date(1899, 12, 30) + timedelta(days=int(value))
    text = str(value).translate(ARABIC_DIGITS).strip()
    text = re.split(r"[ T](?=\d{1,2}:\d{2})", text)[0].strip()  # drop the time
    formats = DATE_FORMATS_ISO + (DATE_FORMATS_DAY_FIRST + DATE_FORMATS_MONTH_FIRST if day_first
                                  else DATE_FORMATS_MONTH_FIRST + DATE_FORMATS_DAY_FIRST)
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def guess_day_first(values):
    """Month-first only when a second component above 12 proves it (12/25/2026)."""
    for value in values:
        if isinstance(value, (date, datetime, int, float)) or value is None:
            continue
        parts = re.split(r"[/.-]", str(value).translate(ARABIC_DIGITS).strip().split(" ")[0])
        if len(parts) == 3 and len(parts[0]) <= 2 and parts[1].isdigit():
            if int(parts[0]) > 12:
                return True
            if int(parts[1]) > 12:
                return False
    return True


# ---------------------------------------------------------------------- tables
def read_table(data, filename=""):
    """Rows (lists of cell values) from an xlsx / xls / csv file."""
    name = (filename or "").lower()
    if name.endswith(".xlsx") or data[:2] == b"PK":
        import openpyxl
        book = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        sheet = book.worksheets[0]
        return [list(row) for row in sheet.iter_rows(values_only=True)]
    if name.endswith(".xls") or data[:4] == b"\xd0\xcf\x11\xe0":
        import xlrd
        book = xlrd.open_workbook(file_contents=data)
        sheet = book.sheet_by_index(0)
        rows = []
        for r in range(sheet.nrows):
            row = []
            for c in range(sheet.ncols):
                cell = sheet.cell(r, c)
                if cell.ctype == xlrd.XL_CELL_DATE:
                    row.append(xlrd.xldate.xldate_as_datetime(cell.value, book.datemode))
                else:
                    row.append(cell.value)
            rows.append(row)
        return rows
    for encoding in ("utf-8-sig", "cp1256", "latin-1"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ","
    return [row for row in csv.reader(io.StringIO(text), delimiter=delimiter)]


def detect_columns(rows):
    """(header row index, {field: column index}) for the first row that looks like a header."""
    best = (None, {}, 0)
    for index, row in enumerate(rows[:40]):
        cells = [normalize(c) for c in row]
        mapping = {}
        for field, names in _NORMALIZED.items():
            for name in names:  # names are ordered by preference
                hits = [i for i, cell in enumerate(cells) if cell == name and i not in mapping.values()]
                if not hits:
                    hits = [i for i, cell in enumerate(cells)
                            if name and len(name) > 2 and name in cell and i not in mapping.values()]
                if hits:
                    mapping[field] = hits[0]
                    break
        has_amount = "amount" in mapping or ("debit" in mapping and "credit" in mapping)
        score = len(mapping) + (5 if "date" in mapping and has_amount else 0)
        if score > best[2]:
            best = (index, mapping, score)
    if best[0] is None or best[2] < 6:
        return None, {}
    mapping = dict(best[1])
    if "debit" in mapping and "credit" in mapping:
        mapping.pop("amount", None)
    return best[0], mapping


def rows_to_lines(rows, header, mapping, day_first=None):
    body = rows[header + 1:]
    if day_first is None:
        day_first = guess_day_first([r[mapping["date"]] for r in body if len(r) > mapping["date"]])

    def cell(row, field):
        i = mapping.get(field)
        return row[i] if i is not None and i < len(row) else None

    lines = []
    for row in body:
        day = parse_date(cell(row, "date"), day_first)
        if not day:
            continue
        if "amount" in mapping:
            amount = parse_amount(cell(row, "amount"))
        else:
            debit, credit = parse_amount(cell(row, "debit")) or 0.0, parse_amount(cell(row, "credit")) or 0.0
            amount = abs(credit) - abs(debit) if (debit or credit) else None
        if amount is None or abs(amount) < 0.005:
            continue
        label = str(cell(row, "label") or "").strip()
        ref = cell(row, "ref")
        ref = ("%d" % ref if isinstance(ref, float) and ref.is_integer() else str(ref or "")).strip()
        lines.append({"date": day, "amount": round(amount, 2), "label": label, "ref": ref, "uid": ""})
    return lines


# ---------------------------------------------------------------------- OFX
def parse_ofx(data):
    text = data.decode("utf-8", errors="ignore") if isinstance(data, bytes) else data
    lines = []
    for block in re.findall(r"<STMTTRN>(.*?)(?:</STMTTRN>|(?=<STMTTRN>)|(?=</BANKTRANLIST>))", text, re.S | re.I):
        def tag(name, block=block):
            m = re.search(r"<%s>([^<\r\n]*)" % name, block, re.I)
            return m.group(1).strip() if m else ""
        posted = tag("DTPOSTED")[:8]
        amount = parse_amount(tag("TRNAMT").replace(",", "."))
        if not posted or amount is None:
            continue
        label = " - ".join(x for x in (tag("NAME"), tag("MEMO")) if x)
        lines.append({"date": datetime.strptime(posted, "%Y%m%d").date(), "amount": round(amount, 2),
                      "label": label, "ref": tag("CHECKNUM") or tag("REFNUM"), "uid": tag("FITID")})
    return lines


# ---------------------------------------------------------------------- CAMT.053
def parse_camt(data):
    from lxml import etree
    root = etree.fromstring(data)

    def first(node, path):
        found = node.xpath(path)
        if not found:
            return ""
        value = found[0]
        return (value if isinstance(value, str) else value.text or "").strip()

    lines = []
    for entry in root.xpath("//*[local-name()='Stmt' or local-name()='Rpt']/*[local-name()='Ntry']"):
        amount = parse_amount(first(entry, "./*[local-name()='Amt']"))
        if amount is None:
            continue
        if first(entry, "./*[local-name()='CdtDbtInd']") == "DBIT":
            amount = -amount
        if first(entry, "./*[local-name()='RvslInd']").lower() == "true":
            amount = -amount
        day = first(entry, "./*[local-name()='BookgDt']/*[local-name()='Dt']") \
            or first(entry, "./*[local-name()='BookgDt']/*[local-name()='DtTm']")[:10] \
            or first(entry, "./*[local-name()='ValDt']/*[local-name()='Dt']")
        parts = [first(entry, ".//*[local-name()='RmtInf']/*[local-name()='Ustrd']"),
                 first(entry, ".//*[local-name()='RltdPties']//*[local-name()='Nm']"),
                 first(entry, "./*[local-name()='AddtlNtryInf']")]
        ref = first(entry, ".//*[local-name()='Refs']/*[local-name()='EndToEndId']") \
            or first(entry, "./*[local-name()='NtryRef']")
        if ref == "NOTPROVIDED":
            ref = ""
        lines.append({"date": datetime.strptime(day[:10], "%Y-%m-%d").date(), "amount": round(amount, 2),
                      "label": " - ".join(dict.fromkeys(p for p in parts if p)), "ref": ref,
                      "uid": first(entry, "./*[local-name()='AcctSvcrRef']")})
    return lines


# ---------------------------------------------------------------------- MT940
def parse_mt940(data):
    text = data.decode("utf-8", errors="ignore") if isinstance(data, bytes) else data
    lines = []
    current = None
    tags = re.split(r"\r?\n(?=:\d{2}[A-Z]?:)", "\n" + text)
    for chunk in tags:
        m = re.match(r":(\d{2}[A-Z]?):(.*)", chunk.strip(), re.S)
        if not m:
            continue
        code, value = m.group(1), m.group(2).strip()
        if code == "61":
            # YYMMDD[MMDD](C|D|RC|RD)[funds code]amount N... //bank ref
            t = re.match(r"(\d{6})(\d{4})?(R?[CD])([A-Z])?([\d,]+)(?:N\w{3})?([^/\n]*)(?://([^\n]*))?", value)
            if not t:
                continue
            day = datetime.strptime(t.group(1), "%y%m%d").date()
            amount = parse_amount(t.group(5).replace(",", "."))
            if t.group(3) in ("D", "RC"):
                amount = -amount
            current = {"date": day, "amount": round(amount, 2), "label": "", "ref": (t.group(6) or "").strip(),
                       "uid": (t.group(7) or "").strip()}
            extra = value.split("\n", 1)[1].strip() if "\n" in value else ""
            current["label"] = extra
            lines.append(current)
        elif code == "86" and current is not None:
            info = re.sub(r"\?\d{2}", " ", value.replace("\n", " "))
            current["label"] = " ".join(x for x in (current["label"], info.strip()) if x)
    return lines


def detect_format(data, filename=""):
    name = (filename or "").lower()
    text = data[:20000].decode("utf-8", errors="ignore") if isinstance(data, bytes) else data[:20000]
    head = text[:2000].upper()
    if name.endswith((".ofx", ".qfx")) or "<OFX>" in head or "OFXHEADER" in head:
        return "ofx"
    if name.endswith(".xml") or ("<?XML" in head and "CAMT.05" in head):
        return "camt"
    if name.endswith((".sta", ".mt940", ".940")) or (re.search(r"^:20:", text, re.M) and ":61:" in text):
        return "mt940"
    return "table"
