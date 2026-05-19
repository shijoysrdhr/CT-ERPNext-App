"""Storefront category API.

Called from the Next.js storefront via `/api/method/calicut_textiles.api.storefront.categories.<verb>`.
Backed by the `Website Item Group` doctype (a separate tree from the internal
`Item Group` used for stock/accounting). Response shapes must match the
`Category` type in `src/lib/api/types.ts`.
"""

import frappe
from frappe import _


@frappe.whitelist(allow_guest=True)
def list(parent=None):
	"""Return Website Item Groups, optionally filtered to direct children of
	`parent`. Without `parent`, returns the top-level (root) groups. Each
	entry includes `hasChildren` so the storefront can render expandable
	menus without a second round-trip.
	"""
	filters = {}
	if parent:
		filters["parent_website_item_group"] = parent
	else:
		filters["parent_website_item_group"] = ("in", ["", None])

	rows = frappe.get_all(
		"Website Item Group",
		filters=filters,
		fields=[
			"name",
			"parent_website_item_group",
			"route",
			"image",
			"is_group",
		],
		order_by="weightage DESC, website_item_group_name ASC",
	)
	return [_serialize(row) for row in rows]


@frappe.whitelist(allow_guest=True)
def get(route):
	"""Return a single Website Item Group by its route slug, or by name as a
	fallback (so URLs like /category/Sarees resolve even when no explicit
	route is set)."""
	if not route:
		frappe.throw(_("route is required"))

	row = frappe.db.get_value(
		"Website Item Group",
		{"route": route},
		[
			"name",
			"parent_website_item_group",
			"route",
			"image",
			"is_group",
		],
		as_dict=True,
	)
	if not row and frappe.db.exists("Website Item Group", route):
		row = frappe.db.get_value(
			"Website Item Group",
			route,
			[
				"name",
				"parent_website_item_group",
				"route",
				"image",
				"is_group",
			],
			as_dict=True,
		)
	if not row:
		frappe.throw(_("Category not found: {0}").format(route), frappe.DoesNotExistError)
	return _serialize(row)


def _serialize(row):
	return {
		"name": row.name,
		"parentName": row.parent_website_item_group or None,
		"route": row.route or "",
		"imageUrl": row.image or None,
		"hasChildren": bool(row.is_group),
	}
