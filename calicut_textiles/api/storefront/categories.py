"""Storefront category API.

Called from the Next.js storefront via `/api/method/calicut_textiles.api.storefront.categories.<verb>`.
Response shapes must match the `Category` type in `src/lib/api/types.ts`.
"""

import frappe
from frappe import _


@frappe.whitelist(allow_guest=True)
def list(parent=None):
	"""Return Item Groups that are flagged `show_in_website`.

	If `parent` is provided, returns its direct children. Otherwise returns the
	top-level (root) website-visible groups. Each entry includes `hasChildren`
	so the storefront can render expandable menus without a second round-trip.
	"""
	filters = {"show_in_website": 1}
	if parent:
		filters["parent_item_group"] = parent
	else:
		# Root level: parent_item_group is the All Item Groups root, or empty
		root = frappe.db.get_value("Item Group", {"is_group": 1, "parent_item_group": ("in", ["", None])}, "name")
		if root:
			filters["parent_item_group"] = root

	rows = frappe.get_all(
		"Item Group",
		filters=filters,
		fields=["name", "parent_item_group", "route", "image", "is_group"],
		order_by="weightage DESC, name ASC",
	)
	return [_serialize(row) for row in rows]


@frappe.whitelist(allow_guest=True)
def get(route):
	"""Return a single Category by its route slug."""
	if not route:
		frappe.throw(_("route is required"))

	row = frappe.db.get_value(
		"Item Group",
		{"route": route, "show_in_website": 1},
		["name", "parent_item_group", "route", "image", "is_group"],
		as_dict=True,
	)
	if not row:
		frappe.throw(_("Category not found: {0}").format(route), frappe.DoesNotExistError)
	return _serialize(row)


def _serialize(row):
	return {
		"name": row.name,
		"parentName": row.parent_item_group or None,
		"route": row.route or "",
		"imageUrl": row.image or None,
		"hasChildren": bool(row.is_group),
	}
