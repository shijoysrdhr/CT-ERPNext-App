"""Add the four core Website Item custom fields the storefront depends on.

These exist on local/prod via the `custom_field.json` fixture, but that fixture
is deliberately excluded from commits (kept drifting with india_compliance
auto-generated fields). Any fresh site — like the Frappe Cloud dev bench
restored from a backup that predates the fields, or a clean install — won't
have them, and every storefront API call 500's with
  OperationalError: Unknown column 'custom_batch_no' in 'SELECT'

This patch re-creates them via `create_custom_field` (idempotent: skips
fields that already exist). It runs early in `patches.txt` so subsequent
patches (e.g. `add_website_item_is_standard_field`, which uses
`insert_after: custom_batch_no`) position correctly on the form.
"""

from frappe.custom.doctype.custom_field.custom_field import create_custom_field


def execute():
	fields = [
		{
			"fieldname": "custom_batch_no",
			"label": "Batch No",
			"fieldtype": "Data",
			"insert_after": "item_code",
			"description": "Customer-facing batch identifier — one Website Item per batch.",
		},
		{
			"fieldname": "custom_current_batch_qty",
			"label": "Current Batch Qty",
			"fieldtype": "Float",
			"insert_after": "custom_batch_no",
			"description": "Stock available in this specific batch. Ignored for Standard Items (which aggregate from Bin).",
		},
		{
			"fieldname": "custom_webshop_price",
			"label": "Webshop Price",
			"fieldtype": "Currency",
			"insert_after": "custom_current_batch_qty",
			"description": "Selling price displayed in the storefront.",
		},
		{
			"fieldname": "custom_storefront_category",
			"label": "Storefront Category",
			"fieldtype": "Link",
			"options": "Storefront Category",
			"insert_after": "item_group",
			"description": "Customer-facing category. Independent of the internal Item Group.",
		},
	]
	for field in fields:
		create_custom_field("Website Item", field)
