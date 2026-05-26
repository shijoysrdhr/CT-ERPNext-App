"""Add the `custom_is_standard` Check custom field on Website Item.

A "standard" Website Item represents a continuously-stocked design (dhoti,
uniform, towel) where each purchase creates a new ERPNext Batch but the
storefront shows a single aggregated card — stock summed across all open
batches, FIFO allocation on sale. Unchecked = the existing one-off-per-batch
model (sarees, wedding pieces). Idempotent.
"""

from frappe.custom.doctype.custom_field.custom_field import create_custom_field


def execute():
	create_custom_field(
		"Website Item",
		{
			"fieldname": "custom_is_standard",
			"label": "Is Standard Item",
			"fieldtype": "Check",
			"default": 0,
			"insert_after": "custom_batch_no",
			"description": (
				"When checked, this card aggregates stock across all batches of the underlying Item "
				"(useful for evergreen SKUs like dhoti, uniforms). Customer doesn't see batch numbers; "
				"sales auto-allocate the oldest batch. Leave unchecked for batch-as-product (sarees)."
			),
		},
	)
