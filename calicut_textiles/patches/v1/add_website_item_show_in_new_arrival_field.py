"""Add the `custom_show_in_new_arrival` Check field on Website Item.

Editor-curated flag for the storefront home page's "NEW ARRIVAL" section.
When ticked, the item is featured there; when no items are ticked, the API
falls back to newest-in-stock so the section never goes empty. Idempotent.
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
			"fieldname": "custom_show_in_new_arrival",
			"label": "Show in New Arrival",
			"fieldtype": "Check",
			"default": 0,
			"insert_after": "custom_is_standard",
			"description": (
				"Tick to feature this item in the home page \"NEW ARRIVAL\" section. "
				"Falls back to newest-in-stock items when nothing is ticked, so the home page never goes empty."
			),
		},
	)
