"""Add the `custom_images` Table custom field on Website Item so editors can
attach multiple product photos in one place, without the multi-step Website
Slideshow flow. Idempotent — `create_custom_field` skips if it already exists.
"""

from frappe.custom.doctype.custom_field.custom_field import create_custom_field


def execute():
	create_custom_field(
		"Website Item",
		{
			"fieldname": "custom_images",
			"label": "Images",
			"fieldtype": "Table",
			"options": "Website Item Image",
			"insert_after": "website_image",
			"description": "Additional product photos shown in the storefront gallery, in order. The Thumbnail above is always first.",
		},
	)
