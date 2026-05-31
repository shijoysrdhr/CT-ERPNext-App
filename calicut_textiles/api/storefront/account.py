"""Authenticated storefront account endpoints.

All methods require a valid `X-Storefront-Token` header — set by the
storefront after a successful OTP login. The header is resolved to a
Customer via `auth.require_customer()`.
"""

import frappe
from frappe import _

from calicut_textiles.api.storefront.auth import backfill_customer_contact, require_customer


@frappe.whitelist(allow_guest=True)
def list_orders(limit=20, offset=0):
	"""Return submitted Sales Invoices for the signed-in customer, newest first."""
	customer = require_customer()
	limit = max(1, min(100, int(limit or 20)))
	offset = max(0, int(offset or 0))

	rows = frappe.get_all(
		"Sales Invoice",
		filters={"customer": customer, "docstatus": 1},
		fields=[
			"name",
			"posting_date",
			"grand_total",
			"currency",
			"status",
			"outstanding_amount",
		],
		order_by="posting_date DESC, creation DESC",
		limit_start=offset,
		limit_page_length=limit,
	)

	return [
		{
			"name": r.name,
			"postingDate": str(r.posting_date) if r.posting_date else None,
			"grandTotal": float(r.grand_total or 0),
			"currency": r.currency or "INR",
			"status": r.status,
			"outstandingAmount": float(r.outstanding_amount or 0),
		}
		for r in rows
	]


@frappe.whitelist(allow_guest=True)
def get_order(invoice):
	"""Return one Sales Invoice with line items, for the signed-in customer.
	Refuses to return an order that doesn't belong to the customer."""
	customer = require_customer()
	if not invoice:
		frappe.throw(_("invoice is required"))

	row = frappe.db.get_value(
		"Sales Invoice",
		invoice,
		[
			"name",
			"customer",
			"posting_date",
			"grand_total",
			"net_total",
			"total_taxes_and_charges",
			"currency",
			"status",
			"outstanding_amount",
			"customer_name",
		],
		as_dict=True,
	)
	if not row:
		frappe.throw(_("Order not found"), frappe.DoesNotExistError)
	if row.customer != customer:
		# Same 404 message — don't leak that the order exists for a different customer.
		frappe.throw(_("Order not found"), frappe.DoesNotExistError)

	items = frappe.get_all(
		"Sales Invoice Item",
		filters={"parent": invoice},
		fields=[
			"item_code",
			"item_name",
			"batch_no",
			"qty",
			"rate",
			"amount",
			"image",
		],
		order_by="idx ASC",
	)

	taxes = frappe.get_all(
		"Sales Taxes and Charges",
		filters={"parent": invoice},
		fields=["description", "rate", "tax_amount"],
		order_by="idx ASC",
	)

	return {
		"name": row.name,
		"customerName": row.customer_name,
		"postingDate": str(row.posting_date) if row.posting_date else None,
		"netTotal": float(row.net_total or 0),
		"totalTaxes": float(row.total_taxes_and_charges or 0),
		"grandTotal": float(row.grand_total or 0),
		"currency": row.currency or "INR",
		"status": row.status,
		"outstandingAmount": float(row.outstanding_amount or 0),
		"items": [
			{
				"itemCode": i.item_code or "",
				"title": i.item_name or i.item_code or "",
				"batchNo": i.batch_no or "",
				"qty": float(i.qty or 0),
				"rate": float(i.rate or 0),
				"amount": float(i.amount or 0),
				"imageUrl": i.image or None,
			}
			for i in items
		],
		"taxes": [
			{
				"description": t.description or "",
				"rate": float(t.rate or 0),
				"amount": float(t.tax_amount or 0),
			}
			for t in taxes
		],
	}


# ---------------------------------------------------------------------------
# Saved addresses (ERPNext Address, linked to the Customer via Dynamic Link)
# ---------------------------------------------------------------------------


@frappe.whitelist(allow_guest=True)
def list_addresses():
	"""Return the signed-in customer's saved addresses, default first."""
	customer = require_customer()
	rows = _customer_addresses(customer)
	return [_serialize_address(r) for r in rows]


@frappe.whitelist(allow_guest=True, methods=["POST"])
def save_address(
	name=None,
	full_name=None,
	line1=None,
	line2=None,
	city=None,
	district=None,
	state=None,
	pincode=None,
	phone=None,
	email=None,
	is_default=0,
):
	"""Create or update an address for the signed-in customer. Pass `name` to
	edit an existing one (ownership is enforced). `line2` carries the Post
	Office; `district` maps to the Address `county` field (CRM-aligned schema)."""
	customer = require_customer()
	line1 = (line1 or "").strip()
	city = (city or "").strip()
	if not line1 or not city:
		frappe.throw(_("Address and city are required"))

	if name:
		_assert_owns_address(name, customer)
		doc = frappe.get_doc("Address", name)
	else:
		doc = frappe.new_doc("Address")
		doc.address_type = "Shipping"
		doc.append("links", {"link_doctype": "Customer", "link_name": customer})

	doc.address_title = (full_name or "").strip() or _customer_title(customer)
	doc.address_line1 = line1
	doc.address_line2 = (line2 or "").strip()
	doc.city = city
	doc.county = (district or "").strip()
	doc.state = (state or "").strip()
	doc.pincode = (pincode or "").strip()
	doc.phone = (phone or "").strip()
	doc.email_id = (email or "").strip()
	if not doc.country:
		doc.country = frappe.db.get_default("country") or "India"
	doc.save(ignore_permissions=True)

	if _truthy(is_default):
		_set_default(doc.name, customer)
		# The default address represents the account holder, so keep the
		# Customer's mobile_no in sync with it (overwrite). Email stays
		# fill-if-blank — it's the login identity, not a per-address detail.
		if doc.phone:
			frappe.db.set_value("Customer", customer, "mobile_no", doc.phone)
		backfill_customer_contact(customer, email=doc.email_id)
	else:
		# Non-default address: only complete blank Customer contact fields.
		backfill_customer_contact(customer, phone=doc.phone, email=doc.email_id)

	return _serialize_address(_address_row(doc.name))


@frappe.whitelist(allow_guest=True, methods=["POST"])
def delete_address(name):
	"""Delete one of the signed-in customer's addresses."""
	customer = require_customer()
	_assert_owns_address(name, customer)
	try:
		frappe.delete_doc("Address", name, ignore_permissions=True)
	except frappe.LinkExistsError:
		frappe.throw(
			_("This address is used on an existing order and can't be deleted.")
		)
	return {"ok": True}


@frappe.whitelist(allow_guest=True, methods=["POST"])
def set_default_address(name):
	"""Mark one of the signed-in customer's addresses as the default, and sync
	the Customer's mobile_no to the new default's phone."""
	customer = require_customer()
	_assert_owns_address(name, customer)
	_set_default(name, customer)
	row = _address_row(name)
	if row and row.phone:
		frappe.db.set_value("Customer", customer, "mobile_no", row.phone)
	return {"ok": True}


# --- address internals -----------------------------------------------------

_ADDRESS_FIELDS = [
	"name",
	"address_title",
	"address_line1",
	"address_line2",
	"city",
	"county",
	"state",
	"pincode",
	"phone",
	"email_id",
	"is_primary_address",
]


def _customer_address_names(customer):
	"""Address names linked to this customer — read straight from the Dynamic
	Link table so no join is involved (avoids ambiguous-column errors)."""
	return frappe.get_all(
		"Dynamic Link",
		filters={
			"parenttype": "Address",
			"link_doctype": "Customer",
			"link_name": customer,
		},
		pluck="parent",
	)


def _customer_addresses(customer):
	names = _customer_address_names(customer)
	if not names:
		return []
	return frappe.get_all(
		"Address",
		filters={"name": ("in", names)},
		fields=_ADDRESS_FIELDS,
		order_by="is_primary_address desc, modified desc",
	)


def _address_row(name):
	rows = frappe.get_all("Address", filters={"name": name}, fields=_ADDRESS_FIELDS)
	return rows[0] if rows else None


def _assert_owns_address(name, customer):
	owns = frappe.db.exists(
		"Dynamic Link",
		{
			"parenttype": "Address",
			"parent": name,
			"link_doctype": "Customer",
			"link_name": customer,
		},
	)
	if not owns:
		frappe.throw(_("Address not found"), frappe.DoesNotExistError)


def _set_default(name, customer):
	for addr in _customer_address_names(customer):
		frappe.db.set_value(
			"Address", addr, "is_primary_address", 1 if addr == name else 0,
			update_modified=False,
		)


def _customer_title(customer):
	return frappe.db.get_value("Customer", customer, "customer_name") or customer


def _truthy(value):
	return str(value).strip().lower() in ("1", "true", "yes", "on")


def _serialize_address(r):
	if not r:
		return None
	return {
		"id": r.name,
		"fullName": r.address_title or "",
		"line1": r.address_line1 or "",
		"line2": r.address_line2 or "",
		"city": r.city or "",
		"district": r.county or "",
		"state": r.state or "",
		"pincode": r.pincode or "",
		"phone": r.phone or "",
		"email": r.email_id or "",
		"isDefault": bool(r.is_primary_address),
	}
