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
	"custom_is_standard",
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

	# `has_variants = 0` excludes Template Website Items (auto-created by
	# webshop when a Variant is published) — they have no batch / stock and
	# would otherwise appear as phantom products in the catalog.
	filters = {"published": 1, "has_variants": 0}

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

	# Stock filter: standard items pull qty from Bin at serialize time, so the
	# per-batch column doesn't gate them — combine via OR with a search-style
	# `or_filters` list. (When `search` is also present, the search OR wins and
	# the stock gate is dropped — the UI's StockBadge shows "Sold out" per card.)
	stock_or = None
	if _truthy(in_stock_only):
		stock_or = [
			["custom_is_standard", "=", 1],
			["custom_current_batch_qty", ">", 0],
		]

	or_filters = None
	if search:
		needle = f"%{search.strip()}%"
		or_filters = [
			["web_item_name", "like", needle],
			["custom_batch_no", "like", needle],
			["item_code", "like", needle],
		]
	elif stock_or:
		or_filters = stock_or

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
		{"route": route, "published": 1, "has_variants": 0},
		_PRODUCT_FIELDS + ["slideshow", "web_long_description", "description", "brand"],
		as_dict=True,
	)
	if not row:
		frappe.throw(_("Product not found: {0}").format(route), frappe.DoesNotExistError)

	product = _serialize_product(row)
	product["description"] = row.web_long_description or row.description or ""
	product["images"] = _gallery_images(row.name, row.slideshow, fallback=row.website_image)
	product["attributes"] = _build_attributes(row)
	product["relatedBatches"] = _related_batches(row.item_code, exclude_name=row.name)
	return product


@frappe.whitelist(allow_guest=True)
def list_featured(limit=8):
	"""Return up to `limit` items for the home page "NEW ARRIVAL" section.

	Strategy: editor-curated first (items flagged `custom_show_in_new_arrival`),
	newest-in-stock as fallback when fewer than `limit` are ticked — so the
	section never goes empty during normal merchandising.

	Stock filter is standard-aware: items with `custom_is_standard=1` aggregate
	their stock from Bin at serialize time, so they pass the gate regardless of
	`custom_current_batch_qty`.
	"""
	limit = max(1, min(50, int(limit or 8)))

	base_filters = {"published": 1, "has_variants": 0}
	stock_or = [
		["custom_is_standard", "=", 1],
		["custom_current_batch_qty", ">", 0],
	]

	# Pass 1 — editor-curated picks.
	curated = frappe.get_all(
		"Website Item",
		filters={**base_filters, "custom_show_in_new_arrival": 1},
		or_filters=stock_or,
		fields=_PRODUCT_FIELDS,
		order_by="creation DESC",
		page_length=limit,
	)

	if len(curated) >= limit:
		return [_serialize_product(row) for row in curated[:limit]]

	# Pass 2 — top up with newest in-stock items not already curated.
	exclude = [r.name for r in curated]
	fallback_filters = {**base_filters}
	if exclude:
		fallback_filters["name"] = ("not in", exclude)
	fallback = frappe.get_all(
		"Website Item",
		filters=fallback_filters,
		or_filters=stock_or,
		fields=_PRODUCT_FIELDS,
		order_by="creation DESC",
		page_length=limit - len(curated),
	)
	return [_serialize_product(row) for row in (curated + fallback)]


def _serialize_product(row):
	# Storefront category takes precedence — falls back to internal item_group
	# if the field isn't set yet on a given Website Item (transition period).
	storefront_category = row.get("custom_storefront_category") or row.item_group or ""
	is_standard = bool(row.get("custom_is_standard"))
	# Standard items show stock aggregated across all batches of the underlying
	# Item (continuous-supply SKUs); one-off batches stay batch-scoped.
	if is_standard:
		stock_qty = _aggregate_item_stock(row.item_code)
		batch_no = ""
	else:
		stock_qty = float(row.custom_current_batch_qty or 0)
		batch_no = row.custom_batch_no or ""
	return {
		"name": row.name,
		"itemCode": row.item_code or "",
		"batchNo": batch_no,
		"isStandard": is_standard,
		"title": row.web_item_name or row.name,
		"shortDescription": row.short_description or None,
		"itemGroup": storefront_category,
		"route": row.route or "",
		"price": float(row.custom_webshop_price or 0),
		"uom": _stock_uom_for(row.item_code),
		"stockQty": stock_qty,
		"imageUrl": row.website_image or None,
	}


_AGGREGATE_STOCK_CACHE = {}


def _aggregate_item_stock(item_code):
	"""Sum `actual_qty` across all Bin rows (warehouse × batch) for the given
	Item — i.e. stock-on-hand at the Item level, irrespective of batch. Cached
	per-request to keep a 24-item listing from firing 24 SUMs."""
	if not item_code:
		return 0.0
	if item_code in _AGGREGATE_STOCK_CACHE:
		return _AGGREGATE_STOCK_CACHE[item_code]
	rows = frappe.get_all(
		"Bin",
		filters={"item_code": item_code},
		fields=["actual_qty"],
	)
	qty = sum(float(r.actual_qty or 0) for r in rows)
	_AGGREGATE_STOCK_CACHE[item_code] = qty
	return qty


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


def _gallery_images(website_item_name, slideshow_name, fallback=None):
	"""Build the product gallery as a list of `{url, alt}` dicts, deduped by url.
	Priority order:
	1. `website_image` (Thumbnail) — matches the listing card the customer
	   just clicked. No alt — UI falls back to the product title.
	2. Rows from the `custom_images` child table — the new editor-friendly
	   way to add extra photos, with optional per-image alt text.
	3. Legacy Website Slideshow items — `heading` reused as alt."""
	images = []
	seen = set()

	def push(url, alt=None):
		if not url or url in seen:
			return
		seen.add(url)
		images.append({"url": url, "alt": alt or None})

	push(fallback)
	if website_item_name:
		rows = frappe.get_all(
			"Website Item Image",
			filters={"parent": website_item_name, "parenttype": "Website Item"},
			fields=["image", "alt_text"],
			order_by="idx ASC",
		)
		for r in rows:
			push(r.image, r.alt_text)
	if slideshow_name:
		rows = frappe.get_all(
			"Website Slideshow Item",
			filters={"parent": slideshow_name},
			fields=["image", "heading"],
			order_by="idx ASC",
		)
		for r in rows:
			push(r.image, r.heading)
	return images


def _build_attributes(row):
	"""Start small — just the obvious spec fields. Enrich later from
	Item Attribute Values if/when the storefront needs filterable specs.
	`uom` is already surfaced on the page next to the stock count, so we
	don't include it in the spec table to avoid duplication."""
	attrs = {}
	if row.brand:
		attrs["Brand"] = row.brand
	category = row.get("custom_storefront_category") or row.item_group
	if category:
		attrs["Category"] = category
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
			"has_variants": 0,
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


def _category_descendants(category):
	"""Return the category and all its descendant Storefront Categories so
	filtering by 'Sarees' also returns products in 'Sarees > Silk Sarees',
	'Sarees > Cotton Sarees', etc."""
	try:
		descendants = frappe.get_all(
			"Storefront Category",
			filters={"name": ("descendants of", category)},
			pluck="name",
		)
	except Exception:
		descendants = []
	return [category, *descendants] if descendants else [category]


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
