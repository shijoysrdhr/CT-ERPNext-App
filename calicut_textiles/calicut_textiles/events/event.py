import frappe
from frappe import _
from datetime import datetime

def convert_date_to_code(sanforize):
    mapping = {
        '1': 'S',
        '2': 'A',
        '3': 'N',
        '4': 'F',
        '5': 'O',
        '6': 'R',
        '7': 'I',
        '8': 'Z',
        '9': 'E',
        '0': '+'
    }
    
    result = ''
    digits = str(sanforize)
    for digit in digits:
        if digit in mapping:
            result += mapping[digit]
    return result

def custom_date_code(doc, method):
    # Serial and Batch Bundle carried `posting_date` until ERPNext 15.11x, which
    # replaced it with `posting_datetime`. Reading the old field raised
    # AttributeError on before_save and blocked submitting any Sales Invoice
    # holding a batched item -- i.e. most counter sales. Accept whichever field
    # the installed version provides, and use .get() so a future rename degrades
    # to "no code" rather than an exception.
    raw = doc.get("posting_date") or doc.get("posting_datetime")
    if not raw:
        return

    if isinstance(raw, str):
        # posting_date is "YYYY-MM-DD"; posting_datetime adds " HH:MM:SS".
        # The first ten characters are the date either way.
        posting_date = datetime.strptime(raw.strip()[:10], '%Y-%m-%d')
    else:
        posting_date = raw

    month_year = posting_date.strftime('%m%y')

    custom_code = convert_date_to_code(month_year)

    doc.custom_sanforize = custom_code


def update_employee_advance(doc, method):
    ea = frappe.get_doc("Employee Advance", doc.name)
    
    advance = ea.custom_bulk_employee_advance
        
    if advance:
        sp = frappe.get_doc("Bulk Employee Advance", advance)
        sp.employee_advance = 1
        sp.save()


def update_employee_additional(doc, method):
    ea = frappe.get_doc("Additional Salary", doc.name)
    
    advance = ea.custom_bulk_employee_advance
        
    if advance:
        sp = frappe.get_doc("Bulk Employee Advance", advance)
        sp.additional_salary = 1
        sp.save()