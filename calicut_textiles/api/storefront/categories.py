"""Storefront category API.

Called from the Next.js storefront via `/api/method/calicut_textiles.api.storefront.categories.<verb>`.
Backed by the `Storefront Category` doctype (a separate tree from the internal
`Item Group` used for stock/accounting). Response shapes must match the
`Category` type in `src/lib/api/types.ts`.
"""

import frappe
from frappe import _


@frappe.whitelist(allow_guest=True)
def list(parent=None, include_children=None, top_nav_only=None):
	"""Return Storefront Categories, optionally filtered to direct children of
	`parent`. Without `parent`, returns the top-level (root) categories. Each
	entry includes `hasChildren` so the storefront can render expandable
	menus without a second round-trip.

	If `include_children` is truthy, each returned entry also gets a
	`children` array of its direct child categories (one level deep) — used
	by the navbar to render hover dropdowns without an extra API call per
	parent.

	If `top_nav_only` is truthy, the parent-based tree filter is dropped and
	the response includes every category flagged `show_in_top_nav=1`,
	regardless of tree depth. This lets the storefront promote a sub-category
	(e.g. "Sarees" living under "Women") to a top-level navbar button.
	"""
	filters = {}
	if _truthy(top_nav_only):
		filters["show_in_top_nav"] = 1
	elif parent:
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
	if not rows:
		return []

	parent_names = [r.name for r in rows]
	want_children = _truthy(include_children)

	# When the caller asks for children, fetch two levels deep (child rows +
	# grandchild rows). The navbar uses the second level to render a nested
	# "section header → links" group inside the dropdown — e.g. hovering
	# "Women" reveals Sarees as a header with Silk / Cotton / etc. listed
	# beneath it. Otherwise we only need to know IF children exist, which the
	# `parent_storefront_category` column alone tells us.
	if want_children:
		child_rows = frappe.get_all(
			"Storefront Category",
			filters={"parent_storefront_category": ("in", parent_names)},
			fields=fields,
			order_by="weightage DESC, storefront_category_name ASC",
		)
	else:
		child_rows = frappe.get_all(
			"Storefront Category",
			filters={"parent_storefront_category": ("in", parent_names)},
			fields=["parent_storefront_category"],
		)

	child_counts = {}
	for c in child_rows:
		child_counts[c.parent_storefront_category] = child_counts.get(c.parent_storefront_category, 0) + 1

	grandchild_rows = []
	great_grandchild_counts = {}
	if want_children and child_rows:
		grandchild_rows = frappe.get_all(
			"Storefront Category",
			filters={"parent_storefront_category": ("in", [c.name for c in child_rows])},
			fields=fields,
			order_by="weightage DESC, storefront_category_name ASC",
		)
		if grandchild_rows:
			for ggc in frappe.get_all(
				"Storefront Category",
				filters={"parent_storefront_category": ("in", [g.name for g in grandchild_rows])},
				fields=["parent_storefront_category"],
			):
				great_grandchild_counts[ggc.parent_storefront_category] = (
					great_grandchild_counts.get(ggc.parent_storefront_category, 0) + 1
				)

	grandchild_counts = {}
	for g in grandchild_rows:
		grandchild_counts[g.parent_storefront_category] = grandchild_counts.get(g.parent_storefront_category, 0) + 1

	serialized = [_serialize(r, child_counts.get(r.name, 0)) for r in rows]
	if want_children:
		grandchildren_by_parent = {}
		for g in grandchild_rows:
			grandchildren_by_parent.setdefault(g.parent_storefront_category, []).append(
				_serialize(g, great_grandchild_counts.get(g.name, 0))
			)
		children_by_parent = {}
		for c in child_rows:
			entry = _serialize(c, grandchild_counts.get(c.name, 0))
			entry["children"] = grandchildren_by_parent.get(c.name, [])
			children_by_parent.setdefault(c.parent_storefront_category, []).append(entry)
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
	child_count = frappe.db.count(
		"Storefront Category", {"parent_storefront_category": row.name}
	)
	return _serialize(row, child_count)


def _serialize(row, child_count=0):
	return {
		"name": row.name,
		"parentName": row.parent_storefront_category or None,
		"route": row.route or "",
		"imageUrl": row.image or None,
		"hasChildren": child_count > 0,
	}


def _truthy(value):
	if value is None:
		return False
	if isinstance(value, bool):
		return value
	if isinstance(value, (int, float)):
		return value != 0
	return str(value).strip().lower() in ("1", "true", "yes")
