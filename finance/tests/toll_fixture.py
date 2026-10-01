"""Invented E-470 statement PDFs for tests (reportlab). The data is made up: the tag numbers, the plate
placeholder and every time are fixtures, and the layout follows the two lines of the sample the feature
was specified from (a summary block, a heading per car, one line per pass)."""
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

STATUS = "VTOLL 8758490"     # the Toll Status column: read past, never kept

# (date, time, plaza, lane, direction, amount)
DEVICE_A = [
    ("8/10/2026", "6:30:53 AM", "SMOKY HILL RD", "1", "South", 1.25),
    ("8/10/2026", "6:37:12 AM", "PLAZA A", "3", "South", 2.60),
    ("8/10/2026", "3:45:10 PM", "PLAZA A", "3", "North", 2.60),
    ("8/10/2026", "3:52:31 PM", "SMOKY HILL RD", "1", "North", 1.25),
    ("8/11/2026", "7:00:05 AM", "SMOKY HILL RD", "1", "South", 1.25),
    ("8/11/2026", "7:06:44 AM", "PLAZA A", "3", "South", 2.60),
    ("8/11/2026", "3:00:20 PM", "PLAZA A", "3", "North", 2.60),
]
DEVICE_B = [
    ("8/18/2026", "4:41:37 PM", "PLAZA A", "3", "North", 2.60),
    ("8/18/2026", "4:47:02 PM", "SMOKY HILL RD", "1", "North", 1.25),
    ("8/19/2026", "7:02:11 AM", "SMOKY HILL RD", "1", "South", 1.25),
    ("8/19/2026", "7:08:40 AM", "PLAZA A", "3", "South", 2.60),
    ("8/19/2026", "4:11:15 PM", "PLAZA A", "3", "North", 2.60),
    ("8/19/2026", "4:17:42 PM", "SMOKY HILL RD", "1", "North", 1.25),
]
SAMPLE_TOTAL = round(sum(r[5] for r in DEVICE_A + DEVICE_B), 2)     # 25.70


def sample_cars():
    """Two cars on one statement; the plate placeholder is the same on both tags."""
    return [("1234567", "carplate-co", list(DEVICE_A)), ("5678945", "carplate-co", list(DEVICE_B))]


def row_line(row, agency="CO", road="E470", status=STATUS):
    date, time, plaza, lane, direction, amount = row
    return f"{date} {time} {agency} {road} {plaza} Lane {lane} {direction} {status} ${amount:.2f}"


def summary_lines(total):
    """The account summary block. Only Grand Totals is ever read; the others are there to be ignored. The Total
    Tolls line deliberately carries a DIFFERENT figure, so a reader that used it would be caught."""
    return ["Previous Balance : $35.00", f"Total Tolls : -${total + 7.13:.2f}", "Adjustments : -$18.00", "Payments : $166.60",
            f"Grand Totals:  ${total:.2f}"]


def make_pdf(path, cars=None, total="auto", summary=True, extra=(), lines_after_heading=None, footer="Thank you for using E-470"):
    """Write a statement PDF. cars: [(device, plate, rows)]; total: printed Grand Totals (positive), 'auto' for the
    passes' sum, or None for no Grand Totals line; extra: raw lines appended at the end of the last car;
    lines_after_heading: {car index: [raw lines]} inserted straight under that car's heading."""
    cars = sample_cars() if cars is None else cars
    passes_sum = round(sum(r[5] for _, _, rows in cars for r in rows if not isinstance(r, str)), 2)
    if total == "auto":
        total = passes_sum
    lines = ["E-470 Public Highway Authority", "Toll Statement"]
    if summary:
        lines += ["Account Summary"]
        lines += summary_lines(total) if total is not None else ["Previous Balance : $35.00", "Payments : $166.60"]
    lines += ["", "Transaction Date/Time Location Lane Toll Status* Amount"]
    for i, (device, plate, rows) in enumerate(cars):
        lines.append(f"Transactions For Device # {device} Plate # {plate}")
        lines += (lines_after_heading or {}).get(i, [])
        lines += [row if isinstance(row, str) else row_line(row) for row in rows]
    lines += list(extra)
    lines += ["", footer]

    c = canvas.Canvas(path, pagesize=letter)
    y = 750
    c.setFont("Courier", 8)
    for line in lines:
        if y < 40:
            c.showPage()
            c.setFont("Courier", 8)
            y = 750
        c.drawString(40, y, line)
        y -= 11
    c.save()
    return path
