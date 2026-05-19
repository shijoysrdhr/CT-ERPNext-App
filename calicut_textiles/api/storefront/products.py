"""Storefront product API.

Called from the Next.js storefront via `/api/method/calicut_textiles.api.storefront.products.<verb>`.
Response shapes must match `src/lib/api/types.ts` in the Calicut-Textiles-Storefront repo:
`Product`, `ProductDetail`, `ProductListResponse`.
"""

import frappe
from frappe import _


# Fields fetched from Website Item for catalog responses. `item_group` is the
# internal (stock/accounting) classification — `custom_storefront_category` is
# the customer-facing one, set independently per item. Storefront responses
# expose the storefront category as `itemGroup` (see _serialize_product).
_PRODUCT_FIELDS = [
	"name",
	"item_code",
	"custom_batch_no",
	"web_item_name",
	"short_description",
	"item_group",
	"custom_storefront_category",
	"route",
	"custom_webshop_price",
	"custom_current_batch_qty",
	"website_image",
]

_SORT_MAP = {
	"newest": "creation DESC",
	"price-asc": "custom_webshop_price ASC",
	"price-desc": "custom_webshop_price DESC",
	"name-asc": "web_item_name ASC",
}


@frappe.whitelist(allow_guest=True)
def list(
	category=None,
	search=None,
	page=1,
	page_size=24,
	sort="newest",
	min_price=None,
	max_price=None,
	in_stock_only=None,
):
	"""Return a paginated, filtered slice of published Website Items.

	Matches storefront `listProducts(params)` — returns `ProductListResponse`.
	"""
	page = max(1, int(page or 1))
	page_size = max(1, min(100, int(page_size or 24)))
	order_by = _SORT_MAP.get(sort, _SORT_MAP["newest"])

	filters = {"published": 1}

	if category:
		category_names = _category_descendants(category)
		# Filter on the storefront-facing category, not the internal item_group.
		filters["custom_storefront_category"] = (
			("in", category_names) if category_names else category
		)

	if min_price is not None:
		filters.setdefault("custom_webshop_price", [">=", float(min_price)])
	if max_price is not None:
		# If we already set a >= filter, replace with a between
		if "custom_webshop_price" in filters and filters["custom_webshop_price"][0] == ">=":
			lo = filters["custom_webshop_price"][1]
			filters["custom_webshop_price"] = ("between", [lo, float(max_price)])
		else:
			filters["custom_webshop_price"] = ("<=", float(max_price))

	if _truthy(in_stock_only):
		filters["custom_current_batch_qty"] = (">", 0)

	or_filters = None
	if search:
		needle = f"%{search.strip()}%"
		or_filters = [
			["web_item_name", "like", needle],
			["custom_batch_no", "like", needle],
			["item_code", "like", needle],
		]

	total = frappe.db.count("Website Item", filters=filters)
	# Frappe's count() ignores or_filters; apply the same OR manually when needed.
	if or_filters:
		total = _count_with_or("Website Item", filters, or_filters)

	rows = frappe.get_all(
		"Website Item",
		filters=filters,
		or_filters=or_filters,
		fields=_PRODUCT_FIELDS,
		order_by=order_by,
		start=(page - 1) * page_size,
		page_length=page_size,
	)

	items = [_serialize_product(row) for row in rows]

	return {
		"items": items,
		"total": total,
		"page": page,
		"pageSize": page_size,
	}


@frappe.whitelist(allow_guest=True)
def get(route):
	"""Return a single `ProductDetail` for the given route slug (e.g. `products/100043672`)."""
	if not route:
		frappe.throw(_("route is required"))

	row = frappe.db.get_value(
		"Website Item",
		{"route": route, "published": 1},
		_PRODUCT_FIELDS + ["slideshow", "web_long_description", "description", "brand"],
		as_dict=True,
	)
	if not row:
		frappe.throw(_("Product not found: {0}").format(route), frappe.DoesNotExistError)

	product = _serialize_product(row)
	product["description"] = row.web_long_description or row.description or ""
	product["images"] = _slideshow_images(row.slideshow, fallback=row.website_image)
	product["attributes"] = _build_attributes(row)
	product["relatedBatches"] = _related_batches(row.item_code, exclude_name=row.name)
	return product


@frappe.whitelist(allow_guest=True)
def list_featured(limit=8):
	"""Return up to `limit` featured Website Items — newest published items with
	stock on hand. (Website Item has no weightage column; if we want curated
	ordering later, add a `custom_featured` Check field and sort by it first.)"""
	limit = max(1, min(50, int(limit or 8)))

	rows = frappe.get_all(
		"Website Item",
		filters={
			"published": 1,
			"custom_current_batch_qty": (">", 0),
		},
		fields=_PRODUCT_FIELDS,
		order_by="creation DESC",
		page_length=limit,
	)
	return [_serialize_product(row) for row in rows]


def _serialize_product(row):
	# Storefront category takes precedence — falls back to internal item_group
	# if the field isn't set yet on a given Website Item (transition period).
	storefront_category = row.get("custom_storefront_category") or row.item_group or ""
	return {
		"name": row.name,
		"itemCode": row.item_code or "",
		"batchNo": row.custom_batch_no or "",
		"title": row.web_item_name or row.name,
		"shortDescription": row.short_description or None,
		"itemGroup": storefront_category,
		"route": row.route or "",
		"price": float(row.custom_webshop_price or 0),
		"uom": _stock_uom_for(row.item_code),
		"stockQty": float(row.custom_current_batch_qty or 0),
		"imageUrl": row.website_image or None,
	}


_UOM_CACHE = {}


def _stock_uom_for(item_code):
	"""Cache the per-request lookup so a 24-item page doesn't fire 24 SELECTs."""
	if not item_code:
		return ""
	if item_code in _UOM_CACHE:
		return _UOM_CACHE[item_code]
	uom = frappe.db.get_value("Item", item_code, "stock_uom") or ""
	_UOM_CACHE[item_code] = uom
	return uom


def _slideshow_images(slideshow_name, fallback=None):
	"""Build the product gallery: `website_image` first (matches the listing
	card customers just clicked), then any Website Slideshow items in order,
	deduped so the same file doesn't appear twice."""
	images = []
	if fallback:
		images.append(fallback)
	if slideshow_name:
		rows = frappe.get_all(
			"Website Slideshow Item",
			filters={"parent": slideshow_name},
			fields=["image"],
			order_by="idx ASC",
		)
		for r in rows:
			if r.image and r.image not in images:
				images.append(r.image)
	return images


def _build_attributes(row):
	"""Start small — just the obvious spec fields. Enrich later from
	Item Attribute Values if/when the storefront needs filterable specs."""
	attrs = {}
	if row.brand:
		attrs["Brand"] = row.brand
	category = row.get("custom_storefront_category") or row.item_group
	if category:
		attrs["Category"] = category
	uom = _stock_uom_for(row.item_code)
	if uom:
		attrs["Unit"] = uom
	return attrs


def _related_batches(item_code, exclude_name):
	"""Other published batches of the same Item — same shape as the
	`relatedBatches` array in `ProductDetail`."""
	if not item_code:
		return []
	rows = frappe.get_all(
		"Website Item",
		filters={
			"item_code": item_code,
			"published": 1,
			"name": ("!=", exclude_name),
		},
		fields=[
			"name",
			"custom_batch_no",
			"custom_webshop_price",
			"custom_current_batch_qty",
			"website_image",
		],
		order_by="custom_current_batch_qty DESC",
		page_length=8,
	)
	return [
		{
			"name": r.name,
			"batchNo": r.custom_batch_no or "",
			"price": float(r.custom_webshop_price or 0),
			"stockQty": float(r.custom_current_batch_qty or 0),
			"imageUrl": r.website_image or None,
		}
		for r in rows
	]


def _category_descendants(item_group):
	"""Return the category and all its descendant Item Groups so filtering by
	'Sarees' also returns products in 'Sarees > Silk', 'Sarees > Cotton', etc."""
	try:
		descendants = frappe.get_all(
			"Item Group",
			filters={"name": ("descendants of", item_group)},
			pluck="name",
		)
	except Exception:
		descendants = []
	return [item_group, *descendants] if descendants else [item_group]


def _count_with_or(doctype, filters, or_filters):
	"""frappe.db.count ignores or_filters; mimic it via get_all + len."""
	return len(
		frappe.get_all(
			doctype,
			filters=filters,
			or_filters=or_filters,
			fields=["name"],
			limit_page_length=0,
		)
	)


def _truthy(value):
	if value is None:
		return False
	if isinstance(value, bool):
		return value
	if isinstance(value, (int, float)):
		return value != 0
	return str(value).strip().lower() in ("1", "true", "yes")
