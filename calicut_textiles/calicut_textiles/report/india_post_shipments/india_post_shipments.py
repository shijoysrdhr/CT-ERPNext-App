# Copyright (c) 2026, Calicut Textiles and contributors
# For license information, please see license.txt
"""Every Sales Invoice / Delivery Note booked with India Post, with its tracking status.

Fields are written by calicut_textiles/calicut_textiles/india_post.py.
"""

import frappe
from frappe import _
from frappe.utils import add_days, getdate

DOCTYPES = ("Sales Invoice", "Delivery Note")


def execute(filters=None):
	filters = frappe._dict(filters or {})
	return columns(), data(filters)


def columns():
	return [
		{"fieldname": "doctype", "label": _("Type"), "fieldtype": "Data", "width": 110},
		{"fieldname": "name", "label": _("Document"), "fieldtype": "Dynamic Link", "options": "doctype", "width": 120},
		{"fieldname": "posting_date", "label": _("Date"), "fieldtype": "Date", "width": 95},
		{"fieldname": "customer_name", "label": _("Customer"), "fieldtype": "Data", "width": 170},
		{"fieldname": "awb", "label": _("AWB"), "fieldtype": "Data", "width": 130},
		{"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 95},
		{"fieldname": "last_event", "label": _("Last Event"), "fieldtype": "Data", "width": 170},
		{"fieldname": "last_event_at", "label": _("Last Event At"), "fieldtype": "Datetime", "width": 150},
		{"fieldname": "product", "label": _("Product"), "fieldtype": "Data", "width": 130},
		{"fieldname": "booked_on", "label": _("Booked On"), "fieldtype": "Datetime", "width": 150},
		{"fieldname": "delivered_on", "label": _("Delivered On"), "fieldtype": "Datetime", "width": 150},
		{"fieldname": "days", "label": _("Days"), "fieldtype": "Int", "width": 60},
		{"fieldname": "charge", "label": _("India Post Charge"), "fieldtype": "Currency", "width": 120},
		{"fieldname": "grand_total", "label": _("Invoice Total"), "fieldtype": "Currency", "width": 120},
	]


def data(filters):
	from_date = getdate(filters.from_date) if filters.from_date else None
	to_date = getdate(filters.to_date) if filters.to_date else None
	rows = []
	for doctype in DOCTYPES:
		if filters.document_type and filters.document_type != doctype:
			continue
		conditions = [["docstatus", "=", 1], ["custom_ip_awb", "is", "set"]]
		if from_date:
			conditions.append(["custom_ip_booked_on", ">=", from_date])
		if to_date:
			conditions.append(["custom_ip_booked_on", "<", add_days(to_date, 1)])
		if filters.status:
			conditions.append(["custom_ip_status", "=", filters.status])
		if filters.customer:
			conditions.append(["customer", "=", filters.customer])

		for r in frappe.get_list(
			doctype,
			filters=conditions,
			fields=[
				"name", "posting_date", "customer_name", "grand_total",
				"custom_ip_awb as awb", "custom_ip_status as status", "custom_ip_last_event as last_event",
				"custom_ip_last_event_at as last_event_at", "custom_ip_product as product",
				"custom_ip_booked_on as booked_on", "custom_ip_delivered_on as delivered_on",
				"custom_ip_charge as charge",
			],
			order_by="custom_ip_booked_on desc",
			limit_page_length=0,
		):
			r.doctype = doctype
			end = r.delivered_on or (frappe.utils.now_datetime() if r.status in ("Booked", "In Transit") else None)
			r.days = (getdate(end) - getdate(r.booked_on)).days if (end and r.booked_on) else None
			rows.append(r)

	rows.sort(key=lambda r: r.booked_on or frappe.utils.get_datetime("1900-01-01"), reverse=True)
	return rows
