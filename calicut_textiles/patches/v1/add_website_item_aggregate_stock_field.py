"""Add the `custom_aggregate_stock_qty` virtual Float field on Website Item.

A read-only display companion to `custom_is_standard`: when ticked, the
form shows the live stock summed across all batches of the underlying Item
(value populated by `CTWebsiteItem.onload`). Virtual = no DB column, no
fixture/migration drift. Idempotent.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_field


def execute():
	# Website Item ships with the `webshop` app; skip where it isn't installed.
	if not frappe.db.exists("DocType", "Website Item"):
		return

	create_custom_field(
		"Website Item",
		{
			"fieldname": "custom_aggregate_stock_qty",
			"label": "Aggregate Stock (across batches)",
			"fieldtype": "Float",
			"is_virtual": 1,
			"read_only": 1,
			"insert_after": "custom_is_standard",
			"depends_on": "eval:doc.custom_is_standard",
			"description": (
				"Live total of `Bin.actual_qty` for this Item across all warehouses & batches. "
				"Only meaningful when Is Standard Item is checked. Read-only — driven by stock movements."
			),
		},
	)
