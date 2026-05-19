"""Storefront category API.

Called from the Next.js storefront via `/api/method/calicut_textiles.api.storefront.categories.<verb>`.
Backed by the `Storefront Category` doctype (a separate tree from the internal
`Item Group` used for stock/accounting). Response shapes must match the
`Category` type in `src/lib/api/types.ts`.
"""

import frappe
from frappe import _


@frappe.whitelist(allow_guest=True)
def list(parent=None, include_children=None):
	"""Return Storefront Categories, optionally filtered to direct children of
	`parent`. Without `parent`, returns the top-level (root) categories. Each
	entry includes `hasChildren` so the storefront can render expandable
	menus without a second round-trip.

	If `include_children` is truthy, each returned entry also gets a
	`children` array of its direct child categories (one level deep) — used
	by the navbar to render hover dropdowns without an extra API call per
	parent.
	"""
	filters = {}
	if parent:
		filters["parent_storefront_category"] = parent
	else:
		filters["parent_storefront_category"] = ("in", ["", None])

	fields = [
		"name",
		"parent_storefront_category",
		"route",
		"image",
		"is_group",
	]
	rows = frappe.get_all(
		"Storefront Category",
		filters=filters,
		fields=fields,
		order_by="weightage DESC, storefront_category_name ASC",
	)
	serialized = [_serialize(row) for row in rows]

	if _truthy(include_children) and serialized:
		# Batch-fetch direct children of every entry we're about to return.
		parent_names = [s["name"] for s in serialized]
		child_rows = frappe.get_all(
			"Storefront Category",
			filters={"parent_storefront_category": ("in", parent_names)},
			fields=fields,
			order_by="weightage DESC, storefront_category_name ASC",
		)
		children_by_parent = {}
		for child in child_rows:
			children_by_parent.setdefault(
				child.parent_storefront_category, []
			).append(_serialize(child))
		for entry in serialized:
			entry["children"] = children_by_parent.get(entry["name"], [])

	return serialized


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


def _truthy(value):
	if value is None:
		return False
	if isinstance(value, bool):
		return value
	if isinstance(value, (int, float)):
		return value != 0
	return str(value).strip().lower() in ("1", "true", "yes")
