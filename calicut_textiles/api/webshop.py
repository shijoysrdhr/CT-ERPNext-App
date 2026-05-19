import frappe
from frappe import _


def refresh_website_item_batch_qty(sle, method=None):
	"""Hook: when a Stock Ledger Entry is created/cancelled, recompute
	custom_current_batch_qty on every Website Item linked to the touched batch.

	Wired via hooks.py doc_events on Stock Ledger Entry.
	"""
	if not sle or sle.is_cancelled:
		# Cancellation: still need to refresh — read batch numbers from bundle anyway
		pass

	batch_nos = set()
	if sle.batch_no:
		batch_nos.add(sle.batch_no)
	if sle.serial_and_batch_bundle:
		rows = frappe.db.get_all(
			"Serial and Batch Entry",
			filters={"parent": sle.serial_and_batch_bundle},
			fields=["batch_no"],
		)
		for r in rows:
			if r.batch_no:
				batch_nos.add(r.batch_no)

	if not batch_nos:
		return

	website_items = frappe.db.get_all(
		"Website Item",
		filters={"custom_batch_no": ("in", list(batch_nos))},
		fields=["name", "custom_batch_no", "website_warehouse"],
	)
	for wi in website_items:
		qty = _compute_batch_qty(wi.custom_batch_no, wi.website_warehouse)
		frappe.db.set_value(
			"Website Item", wi.name, "custom_current_batch_qty", qty, update_modified=False
		)


def _compute_batch_qty(batch_no, warehouse):
	if not batch_no or not warehouse:
		return 0
	row = frappe.db.sql(
		"""
		SELECT COALESCE(SUM(e.qty), 0) AS qty
		FROM `tabStock Ledger Entry` sle
		INNER JOIN `tabSerial and Batch Entry` e ON e.parent = sle.serial_and_batch_bundle
		WHERE e.batch_no = %(batch)s
		  AND sle.warehouse = %(wh)s
		  AND sle.is_cancelled = 0
		  AND sle.docstatus < 2
		""",
		{"batch": batch_no, "wh": warehouse},
	)
	return float(row[0][0] or 0) if row else 0


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
