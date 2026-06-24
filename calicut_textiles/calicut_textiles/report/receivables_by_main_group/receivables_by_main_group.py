# Copyright (c) 2026, Calicut Textiles and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import flt

from erpnext.accounts.report.accounts_receivable_summary.accounts_receivable_summary import (
	AccountsReceivableSummary,
)

# Top-level customer groups we split receivables across (in column order).
MAIN_GROUPS = ["Uniforms", "Textiles", "Uniforms & Textiles"]

# main group name -> output fieldname
FIELD_BY_GROUP = {
	"Uniforms": "uniforms",
	"Textiles": "textiles",
	"Uniforms & Textiles": "uniforms_textiles",
}


def execute(filters=None):
	filters = frappe._dict(filters or {})
	return get_columns(), get_data(filters)


def get_columns():
	return [
		{
			"label": _("Customer"),
			"fieldname": "customer",
			"fieldtype": "Link",
			"options": "Customer",
			"width": 240,
		},
		{
			"label": _("Customer Name"),
			"fieldname": "customer_name",
			"fieldtype": "Data",
			"width": 220,
		},
		{
			"label": _("Uniforms"),
			"fieldname": "uniforms",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 130,
		},
		{
			"label": _("Textiles"),
			"fieldname": "textiles",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 130,
		},
		{
			"label": _("Uniforms & Textiles"),
			"fieldname": "uniforms_textiles",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 160,
		},
		{
			"label": _("Total Outstanding"),
			"fieldname": "total",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 150,
		},
		{
			"label": _("Customer Group"),
			"fieldname": "customer_group",
			"fieldtype": "Link",
			"options": "Customer Group",
			"width": 160,
		},
		{
			"label": _("Currency"),
			"fieldname": "currency",
			"fieldtype": "Link",
			"options": "Currency",
			"width": 80,
			"hidden": 1,
		},
	]


def get_main_group_map():
	"""Map every customer group to the top-level main group it rolls up to.

	Uses the nested-set (lft/rgt) bounds: a group belongs to main group M when
	its lft/rgt fall inside M's range.
	"""
	groups = frappe.get_all("Customer Group", fields=["name", "lft", "rgt"])
	mains = [g for g in groups if g.name in MAIN_GROUPS]
	result = {}
	for g in groups:
		for m in mains:
			if m.lft <= g.lft and g.rgt <= m.rgt:
				result[g.name] = m.name
				break
	return result


def get_data(filters):
	args = {
		"account_type": "Receivable",
		"naming_by": ["Selling Settings", "cust_master_name"],
	}

	ar_filters = frappe._dict(
		{
			"company": filters.get("company"),
			"report_date": filters.get("report_date"),
			"account_type": "Receivable",
		}
	)

	# Reuse the standard Accounts Receivable Summary logic to get per-party
	# outstanding (already aggregated, with customer_group attached).
	receivables = AccountsReceivableSummary(ar_filters).run(args)[1]

	group_map = get_main_group_map()

	rows = {}
	for r in receivables:
		main = group_map.get(r.get("customer_group"))
		if main not in FIELD_BY_GROUP:
			# Customer not under Uniforms / Textiles / Uniforms & Textiles
			continue

		outstanding = flt(r.get("outstanding"))
		if not outstanding:
			continue

		party = r.get("party")
		row = rows.get(party)
		if row is None:
			row = frappe._dict(
				{
					"customer": party,
					"customer_name": r.get("party_name")
					or frappe.get_cached_value("Customer", party, "customer_name")
					or party,
					"customer_group": r.get("customer_group"),
					"uniforms": 0.0,
					"textiles": 0.0,
					"uniforms_textiles": 0.0,
					"total": 0.0,
					"currency": r.get("currency"),
				}
			)
			rows[party] = row

		row[FIELD_BY_GROUP[main]] += outstanding
		row.total += outstanding

	return sorted(rows.values(), key=lambda x: flt(x.total), reverse=True)
