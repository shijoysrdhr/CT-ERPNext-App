"""Storefront order placement.

Single entry point `place_order` does, in this order:

  1. Verify the Razorpay payment signature (so we trust the payment).
  2. Look up or create a Customer by email + phone.
  3. Create / reuse an Address record linked to the Customer.
  4. Create a submitted Sales Invoice with the cart items, applying the
     default tax template and shipping line from Storefront Settings.
  5. Record a Payment Entry against the Sales Invoice (mode = Razorpay).
  6. Return invoice name + customer name for the storefront's thank-you
     page.

All ERPNext writes happen with `ignore_permissions=True` because guest
checkout means there's no user context — guarding the public surface is
the signature verification + idempotency by Razorpay payment id.
"""

import json
import frappe
from frappe import _

from calicut_textiles.goshop.doctype.storefront_settings.storefront_settings import (
	get_settings,
)
from calicut_textiles.goshop.doctype.storefront_shipping_zone.storefront_shipping_zone import (
	resolve_rate,
)
from calicut_textiles.api.storefront.payments import verify_signature


def _aggregate_item_stock(item_code):
	"""Sum `actual_qty` across all Bin rows (warehouse × item) for the given
	Item — stock-on-hand at the Item level, ignoring batch. Mirrors the helper
	in `products` / `quote`; kept here to avoid circular imports."""
	if not item_code:
		return 0
	rows = frappe.get_all(
		"Bin",
		filters={"item_code": item_code},
		fields=["actual_qty"],
	)
	return int(sum(float(r.actual_qty or 0) for r in rows))


@frappe.whitelist(allow_guest=True, methods=["POST"])
def place_order(items, contact, address, payment):
	"""Place a guest storefront order.

	Args (all JSON strings or dicts):
	    items: [{ productName, qty }]
	    contact: { name, email, phone }
	    address: { line1, line2, city, state, pincode, country }
	    payment: { razorpayOrderId, razorpayPaymentId, razorpaySignature }
	"""
	parsed = _parse(items, contact, address, payment)

	verify_signature(
		payment_id=parsed["payment"]["razorpayPaymentId"],
		order_id=parsed["payment"]["razorpayOrderId"],
		signature=parsed["payment"]["razorpaySignature"],
	)

	# Idempotency — if we've already processed this Razorpay payment, return
	# the invoice it created. Uses Payment Entry's built-in `reference_no`
	# rather than a custom field, so no extra schema is needed.
	existing_pe = frappe.db.get_value(
		"Payment Entry",
		{"reference_no": parsed["payment"]["razorpayPaymentId"], "docstatus": 1},
		"name",
	)
	if existing_pe:
		linked = frappe.db.get_value(
			"Payment Entry Reference",
			{"parent": existing_pe, "reference_doctype": "Sales Invoice"},
			"reference_name",
		)
		if linked:
			si_row = frappe.db.get_value(
				"Sales Invoice", linked, ["name", "customer", "grand_total"], as_dict=True
			)
			if si_row:
				return {
					"invoiceName": si_row.name,
					"customerName": si_row.customer,
					"grandTotal": si_row.grand_total,
				}

	settings = get_settings()
	customer = _get_or_create_customer(parsed["contact"], settings)
	_attach_address(customer, parsed["contact"], parsed["address"])

	invoice = _create_sales_invoice(
		customer=customer,
		lines=parsed["items"],
		address=parsed["address"],
		contact=parsed["contact"],
		payment=parsed["payment"],
		settings=settings,
	)

	return {
		"invoiceName": invoice.name,
		"customerName": invoice.customer,
		"grandTotal": invoice.grand_total,
	}


def _parse(items, contact, address, payment):
	def _maybe(v):
		return json.loads(v) if isinstance(v, str) else v
	items = _maybe(items) or []
	contact = _maybe(contact) or {}
	address = _maybe(address) or {}
	payment = _maybe(payment) or {}

	if not items:
		frappe.throw(_("Cart is empty"))
	if not contact.get("name") or not contact.get("phone"):
		frappe.throw(_("Customer name and phone are required"))
	for key in ("razorpayOrderId", "razorpayPaymentId", "razorpaySignature"):
		if not payment.get(key):
			frappe.throw(_("Missing payment.{0}").format(key))

	return {"items": items, "contact": contact, "address": address, "payment": payment}


def _get_or_create_customer(contact, settings):
	"""Look up by email or phone — if both miss, create a new Customer."""
	email = (contact.get("email") or "").strip().lower()
	phone = (contact.get("phone") or "").strip()

	if email:
		existing = frappe.db.get_value("Customer", {"email_id": email}, "name")
		if existing:
			return existing
	if phone:
		existing = frappe.db.get_value("Customer", {"mobile_no": phone}, "name")
		if existing:
			return existing

	doc = frappe.new_doc("Customer")
	doc.customer_name = contact.get("name") or settings.fallback_customer_name or "Storefront Customer"
	doc.customer_type = "Individual"
	doc.customer_group = settings.default_customer_group
	doc.territory = settings.default_territory
	doc.email_id = email or None
	doc.mobile_no = phone or None
	doc.insert(ignore_permissions=True)
	return doc.name


def _attach_address(customer, contact, address):
	"""Create + link an Address. We don't bother deduping addresses for now —
	repeat customers will accumulate copies. Fine for v1."""
	if not address.get("line1"):
		return

	doc = frappe.new_doc("Address")
	doc.address_title = contact.get("name") or customer
	doc.address_type = "Shipping"
	doc.address_line1 = address.get("line1")
	doc.address_line2 = address.get("line2") or ""
	doc.city = address.get("city") or ""
	doc.state = address.get("state") or ""
	doc.pincode = address.get("pincode") or ""
	doc.country = address.get("country") or "India"
	doc.phone = contact.get("phone") or ""
	doc.email_id = contact.get("email") or ""
	doc.append("links", {"link_doctype": "Customer", "link_name": customer})
	doc.insert(ignore_permissions=True)


def _create_sales_invoice(customer, lines, address, contact, payment, settings):
	"""Build a submitted Sales Invoice with cart items, tax template, shipping
	as an Actual tax row, and a Payment Entry posting to the configured
	bank/cash account."""
	si = frappe.new_doc("Sales Invoice")
	si.customer = customer
	si.company = settings.company
	si.set_posting_time = 1
	si.currency = settings.default_currency or "INR"
	si.selling_price_list = settings.default_price_list
	si.remarks = (
		f"Storefront order. Razorpay order: {payment.get('razorpayOrderId')}, "
		f"payment: {payment.get('razorpayPaymentId')}"
	)

	subtotal = 0.0
	for entry in lines:
		row = frappe.db.get_value(
			"Website Item",
			{"name": entry.get("productName"), "published": 1},
			[
				"item_code",
				"custom_batch_no",
				"custom_is_standard",
				"custom_webshop_price",
				"custom_current_batch_qty",
				"web_item_name",
			],
			as_dict=True,
		)
		if not row:
			frappe.throw(_("Product not available: {0}").format(entry.get("productName")))
		qty = int(entry.get("qty") or 0)
		# Standard items: stock aggregated across all batches; ERPNext picks
		# the actual batch on submit via Auto Batch Selection (FIFO).
		if row.custom_is_standard:
			available = _aggregate_item_stock(row.item_code)
			batch_no = None
		else:
			available = int(row.custom_current_batch_qty or 0)
			batch_no = row.custom_batch_no
		if qty <= 0 or qty > available:
			frappe.throw(_("Not enough stock for {0}").format(row.web_item_name or row.item_code))
		rate = float(row.custom_webshop_price or 0)
		si.append("items", {
			"item_code": row.item_code,
			"item_name": row.web_item_name,
			"qty": qty,
			"rate": rate,
			"batch_no": batch_no,
			"warehouse": settings.default_warehouse,
		})
		subtotal += rate * qty

	# Default tax template.
	if settings.sales_taxes_and_charges_template:
		si.taxes_and_charges = settings.sales_taxes_and_charges_template
		tmpl = frappe.get_cached_doc(
			"Sales Taxes and Charges Template", settings.sales_taxes_and_charges_template
		)
		for tax in tmpl.taxes or []:
			si.append("taxes", {
				"charge_type": tax.charge_type,
				"account_head": tax.account_head,
				"description": tax.description,
				"rate": tax.rate,
				"tax_amount": tax.tax_amount,
				"cost_center": tax.cost_center,
			})

	# Shipping as an Actual-type taxes row so it adds to grand_total without
	# distorting item-level tax calculations.
	pincode = address.get("pincode")
	rate, zone = resolve_rate(pincode, subtotal=subtotal)
	if rate > 0 and settings.shipping_account:
		si.append("taxes", {
			"charge_type": "Actual",
			"account_head": settings.shipping_account,
			"description": f"Shipping{f' ({zone})' if zone else ''}",
			"tax_amount": rate,
			"add_deduct_tax": "Add",
		})

	si.insert(ignore_permissions=True)
	si.submit()

	_create_payment_entry(si, payment, settings)

	return si


def _create_payment_entry(invoice, payment, settings):
	"""Post a Payment Entry covering the full grand_total of the invoice and
	allocate it against the SI."""
	if not settings.default_payment_account:
		# Skip — admin hasn't configured a bank account; SI stays Unpaid.
		return

	pe = frappe.new_doc("Payment Entry")
	pe.payment_type = "Receive"
	pe.party_type = "Customer"
	pe.party = invoice.customer
	pe.company = invoice.company
	pe.paid_amount = invoice.grand_total
	pe.received_amount = invoice.grand_total
	pe.paid_to = settings.default_payment_account
	pe.mode_of_payment = settings.default_payment_mode_of_payment or "Razorpay"
	pe.reference_no = payment.get("razorpayPaymentId")
	pe.reference_date = frappe.utils.nowdate()
	pe.append("references", {
		"reference_doctype": "Sales Invoice",
		"reference_name": invoice.name,
		"total_amount": invoice.grand_total,
		"outstanding_amount": invoice.outstanding_amount,
		"allocated_amount": invoice.grand_total,
	})
	pe.insert(ignore_permissions=True)
	pe.submit()
