import frappe
from frappe import _


@frappe.whitelist()
def get_batch_qty_for_website_items(names):
	"""Given a list of Website Item names, return {name: batch_qty} for each.
	Computes from Serial and Batch Entry — always live, no caching."""
	if isinstance(names, str):
		import json

		names = json.loads(names)
	if not names:
		return {}

	rows = frappe.db.sql(
		"""
		SELECT wi.name AS website_item, COALESCE(SUM(e.qty), 0) AS qty
		FROM `tabWebsite Item` wi
		LEFT JOIN `tabStock Ledger Entry` sle
		    ON sle.is_cancelled = 0
		    AND sle.docstatus < 2
		    AND sle.warehouse = wi.website_warehouse
		LEFT JOIN `tabSerial and Batch Entry` e
		    ON e.parent = sle.serial_and_batch_bundle
		    AND e.batch_no = wi.custom_batch_no
		WHERE wi.name IN %(names)s
		  AND wi.custom_batch_no IS NOT NULL
		GROUP BY wi.name
		""",
		{"names": tuple(names)},
		as_dict=True,
	)
	return {r.website_item: float(r.qty or 0) for r in rows}


@frappe.whitelist()
def get_batch_details(batch_no):
	"""Return item, price, warehouse for a scanned batch — used by the Website Item form
	to auto-populate fields when a batch number (barcode) is entered."""
	if not batch_no:
		return {}

	batch = frappe.db.get_value(
		"Batch",
		batch_no,
		["item", "item_name", "disabled"],
		as_dict=True,
	)
	if not batch:
		frappe.throw(_("Batch {0} not found").format(batch_no))
	if batch.disabled:
		frappe.throw(_("Batch {0} is disabled").format(batch_no))

	price = (
		frappe.db.get_value(
			"Item Price",
			{"item_code": batch.item, "price_list": "Retail Price", "batch_no": batch_no},
			"price_list_rate",
		)
		or frappe.db.get_value(
			"Item Price",
			{"item_code": batch.item, "price_list": "Retail Price", "batch_no": ["in", ["", None]]},
			"price_list_rate",
		)
		or 0
	)

	warehouse_row = frappe.db.sql(
		"""
		SELECT sle.warehouse, SUM(e.qty) AS qty
		FROM `tabStock Ledger Entry` sle
		INNER JOIN `tabSerial and Batch Entry` e ON e.parent = sle.serial_and_batch_bundle
		WHERE e.batch_no = %(batch)s
		  AND sle.is_cancelled = 0
		  AND sle.docstatus < 2
		GROUP BY sle.warehouse
		HAVING qty > 0
		ORDER BY qty DESC
		LIMIT 1
		""",
		{"batch": batch_no},
		as_dict=True,
	)

	return {
		"item_code": batch.item,
		"item_name": batch.item_name,
		"price": price,
		"warehouse": warehouse_row[0].warehouse if warehouse_row else "",
		"qty": warehouse_row[0].qty if warehouse_row else 0,
	}
