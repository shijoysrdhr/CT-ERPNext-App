"""Storefront category API.

Called from the Next.js storefront via `/api/method/calicut_textiles.api.storefront.categories.<verb>`.
Backed by the `Storefront Category` doctype (a separate tree from the internal
`Item Group` used for stock/accounting). Response shapes must match the
`Category` type in `src/lib/api/types.ts`.
"""

import frappe
from frappe import _


@frappe.whitelist(allow_guest=True)
def list(parent=None):
	"""Return Storefront Categories, optionally filtered to direct children of
	`parent`. Without `parent`, returns the top-level (root) categories. Each
	entry includes `hasChildren` so the storefront can render expandable
	menus without a second round-trip.
	"""
	filters = {}
	if parent:
		filters["parent_storefront_category"] = parent
	else:
		filters["parent_storefront_category"] = ("in", ["", None])

	rows = frappe.get_all(
		"Storefront Category",
		filters=filters,
		fields=[
			"name",
			"parent_storefront_category",
			"route",
			"image",
			"is_group",
		],
		order_by="weightage DESC, storefront_category_name ASC",
	)
	return [_serialize(row) for row in rows]


@frappe.whitelist(allow_guest=True)
def get(route):
	"""Return a single Storefront Category by its route slug, or by name as a
	fallback (so URLs like /category/Sarees resolve even when no explicit
	route is set)."""
	if not route:
		frappe.throw(_("route is required"))

	row = frappe.db.get_value(
		"Storefront Category",
		{"route": route},
		[
			"name",
			"parent_storefront_category",
			"route",
			"image",
			"is_group",
		],
		as_dict=True,
	)
	if not row and frappe.db.exists("Storefront Category", route):
		row = frappe.db.get_value(
			"Storefront Category",
			route,
			[
				"name",
				"parent_storefront_category",
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
		"parentName": row.parent_storefront_category or None,
		"route": row.route or "",
		"imageUrl": row.image or None,
		"hasChildren": bool(row.is_group),
	}
