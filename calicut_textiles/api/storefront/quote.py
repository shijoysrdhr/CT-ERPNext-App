"""Storefront quote API.

Called from the Next.js storefront via
`/api/method/calicut_textiles.api.storefront.quote.create` when the customer
enters their pincode on the checkout page. Returns a priced quote without
creating any docs — purely a calculation.

Response shape matches the `Quote` type in `src/lib/api/types.ts`.
"""

import json
import frappe
from frappe import _

from calicut_textiles.goshop.doctype.storefront_shipping_zone.storefront_shipping_zone import (
	resolve_rate,
)
from calicut_textiles.goshop.doctype.storefront_settings.storefront_settings import (
	get_settings,
)


@frappe.whitelist(allow_guest=True)
def create(items, pincode=None):
	"""Price a cart for the given pincode.

	Args:
	    items: JSON-encoded list of `{ productName: <Website Item>, qty: <int> }`
	    pincode: customer pincode used to look up the shipping zone

	Returns:
	    Quote dict — see types.ts.
	"""
	parsed = _parse_items(items)
	if not parsed:
		frappe.throw(_("Cart is empty"))

	lines, subtotal = _price_lines(parsed)
	shipping_rate, zone = resolve_rate(pincode, subtotal=subtotal)
	tax_estimate, tax_breakdown = _estimate_tax(subtotal + shipping_rate)

	return {
		"lines": lines,
		"subtotal": subtotal,
		"shipping": shipping_rate,
		"shippingZone": zone,
		"taxEstimate": tax_estimate,
		"taxBreakdown": tax_breakdown,
		"total": subtotal + shipping_rate + tax_estimate,
		"currency": _currency(),
	}


def _parse_items(items):
	"""Normalise the items arg — accepts dict or JSON string."""
	if isinstance(items, str):
		items = json.loads(items)
	if not isinstance(items, list):
		frappe.throw(_("items must be a list"))
	out = []
	for entry in items:
		if not isinstance(entry, dict):
			continue
		product_name = entry.get("productName") or entry.get("product_name")
		qty = int(entry.get("qty") or 0)
		if not product_name or qty <= 0:
			continue
		out.append({"productName": product_name, "qty": qty})
	return out


def _price_lines(parsed):
	"""Fetch live price + stock per item, build line dicts, sum subtotal."""
	names = [p["productName"] for p in parsed]
	rows = frappe.get_all(
		"Website Item",
		filters={"name": ("in", names), "published": 1},
		fields=[
			"name",
			"item_code",
			"custom_batch_no",
			"custom_is_standard",
			"web_item_name",
			"custom_webshop_price",
			"custom_current_batch_qty",
			"website_image",
		],
	)
	by_name = {r.name: r for r in rows}

	lines = []
	subtotal = 0.0
	for p in parsed:
		row = by_name.get(p["productName"])
		if not row:
			frappe.throw(_("Product is no longer available: {0}").format(p["productName"]))
		# Standard items aggregate stock across all batches of the underlying
		# Item; one-off items stay scoped to their single batch.
		if row.get("custom_is_standard"):
			available = _aggregate_item_stock(row.item_code)
			batch_no = ""
		else:
			available = float(row.custom_current_batch_qty or 0)
			batch_no = row.custom_batch_no or ""
		qty = min(p["qty"], int(available))
		if qty <= 0:
			frappe.throw(_("Out of stock: {0}").format(row.web_item_name or row.name))
		price = float(row.custom_webshop_price or 0)
		line_total = price * qty
		lines.append({
			"productName": row.name,
			"itemCode": row.item_code or "",
			"batchNo": batch_no,
			"title": row.web_item_name or row.name,
			"qty": qty,
			"price": price,
			"lineTotal": line_total,
			"imageUrl": row.website_image or None,
			"stockQty": available,
		})
		subtotal += line_total

	return lines, subtotal


def _aggregate_item_stock(item_code):
	"""Sum stock across all Bin rows (warehouse × item) for the given Item.
	Mirrors `products._aggregate_item_stock` — kept inline to avoid a circular
	import between the two storefront modules."""
	if not item_code:
		return 0.0
	rows = frappe.get_all(
		"Bin",
		filters={"item_code": item_code},
		fields=["actual_qty"],
	)
	return sum(float(r.actual_qty or 0) for r in rows)


def _estimate_tax(taxable_amount):
	"""Sum percentage-type rows from the configured Sales Taxes template and
	apply them to `taxable_amount`. Actual/Flat rows are ignored in the
	estimate — they'll show up on the real Sales Invoice at order time."""
	settings = get_settings()
	template_name = settings.sales_taxes_and_charges_template
	if not template_name:
		return 0.0, []

	template = frappe.get_cached_doc("Sales Taxes and Charges Template", template_name)
	estimate = 0.0
	breakdown = []
	for row in template.taxes or []:
		if row.charge_type in ("On Net Total", "On Previous Row Total"):
			rate = float(row.rate or 0)
			amount = round(taxable_amount * rate / 100.0, 2)
			estimate += amount
			breakdown.append({
				"description": row.description or row.account_head,
				"rate": rate,
				"amount": amount,
			})
	return round(estimate, 2), breakdown


def _currency():
	try:
		return get_settings().default_currency or "INR"
	except frappe.DoesNotExistError:
		return "INR"
