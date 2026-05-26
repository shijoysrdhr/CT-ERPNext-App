"""Storefront search / autocomplete API.

Called from the Next.js storefront header search bar via
`/api/method/calicut_textiles.api.storefront.search.autocomplete`. Response
shape must match `SearchAutocompleteResponse` in `src/lib/api/types.ts`.
"""

import frappe
from frappe import _

from calicut_textiles.api.storefront.products import _PRODUCT_FIELDS, _serialize_product


@frappe.whitelist(allow_guest=True)
def autocomplete(q, limit=5):
	"""Return top product + category matches for the search dropdown.

	Empty / whitespace-only `q` returns an empty payload so the caller can
	render its "popular categories" empty state without a second round-trip.
	"""
	needle = (q or "").strip()
	if not needle:
		return {"products": [], "categories": [], "totalProducts": 0}

	limit = max(1, min(20, int(limit or 5)))
	like = f"%{needle}%"

	product_filters = {"published": 1, "has_variants": 0}
	product_or = [
		["web_item_name", "like", like],
		["custom_batch_no", "like", like],
		["item_code", "like", like],
		["short_description", "like", like],
	]

	# Count all matches so the dropdown can show "View all 47 results" — keep
	# the SELECT light by only pulling `name` for the count pass.
	total_products = len(
		frappe.get_all(
			"Website Item",
			filters=product_filters,
			or_filters=product_or,
			fields=["name"],
			limit_page_length=0,
		)
	)

	# Rank in-stock items above out-of-stock, then newest first. The product
	# detail / catalog endpoints already use this ordering for consistency.
	product_rows = frappe.get_all(
		"Website Item",
		filters=product_filters,
		or_filters=product_or,
		fields=_PRODUCT_FIELDS,
		order_by="custom_current_batch_qty DESC, creation DESC",
		page_length=limit,
	)
	products = [_serialize_product(row) for row in product_rows]

	# Categories — match against the human label, not the doc name slug.
	category_rows = frappe.get_all(
		"Storefront Category",
		filters={"storefront_category_name": ("like", like)},
		fields=["name", "parent_storefront_category", "route", "image"],
		order_by="weightage DESC, storefront_category_name ASC",
		page_length=max(3, limit // 2 + 1),
	)
	categories = [
		{
			"name": r.name,
			"parentName": r.parent_storefront_category or None,
			"route": r.route or "",
			"imageUrl": r.image or None,
			"hasChildren": False,
		}
		for r in category_rows
	]

	return {
		"products": products,
		"categories": categories,
		"totalProducts": total_products,
	}
