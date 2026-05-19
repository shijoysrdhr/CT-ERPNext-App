"""Authenticated storefront account endpoints.

All methods require a valid `X-Storefront-Token` header — set by the
storefront after a successful OTP login. The header is resolved to a
Customer via `auth.require_customer()`.
"""

import frappe
from frappe import _

from calicut_textiles.api.storefront.auth import require_customer


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
