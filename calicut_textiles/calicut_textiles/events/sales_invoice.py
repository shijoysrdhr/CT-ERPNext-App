import json

import frappe
from frappe import _
from frappe.utils import flt
from erpnext.stock.utils import _update_item_info
from typing import Dict, Optional


BarcodeScanResult = Dict[str, Optional[str]]

@frappe.whitelist()
def scan_barcode(search_value: str) -> BarcodeScanResult:
	def set_cache(data: BarcodeScanResult):
		frappe.cache().set_value(f"erpnext:barcode_scan:{search_value}", data, expires_in_sec=120)

	def get_cache() -> BarcodeScanResult | None:
		if data := frappe.cache().get_value(f"erpnext:barcode_scan:{search_value}"):
			return data

	if scan_data := get_cache():
		return scan_data

	# search barcode no
	barcode_data = frappe.db.get_value(
		"Item Barcode",
		{"barcode": search_value},
		["barcode", "parent as item_code", "uom"],
		as_dict=True,
	)
	if barcode_data:
		_update_item_info(barcode_data)
		set_cache(barcode_data)
		return barcode_data

	# search serial no
	serial_no_data = frappe.db.get_value(
		"Serial No",
		search_value,
		["name as serial_no", "item_code", "batch_no"],
		as_dict=True,
	)
	if serial_no_data:
		_update_item_info(serial_no_data)
		set_cache(serial_no_data)
		return serial_no_data

	# search batch no
	batch_no_data = frappe.db.get_value(
		"Batch",
		search_value,
		["name as batch_no", "item as item_code", "batch_qty as qty"],
		as_dict=True,
	)
	if not batch_no_data:
		alt_batch_row = frappe.db.get_value(
			"Alternative Batch",
			{"alternative_batch": search_value},
			["parent"],
			as_dict=True
		)
		if alt_batch_row:
			batch_no_data = frappe.db.get_value(
				"Batch",
				{"name": alt_batch_row.parent},
				["name as batch_no", "item as item_code", "batch_qty as qty"],
				as_dict=True,
			)
	if batch_no_data:
		if frappe.get_cached_value("Item", batch_no_data.item_code, "has_serial_no"):
			frappe.throw(
				_(
					"Batch No {0} is linked with Item {1} which has serial no. Please scan serial no instead."
				).format(search_value, batch_no_data.item_code)
			)


		_update_item_info(batch_no_data)
		set_cache(batch_no_data)
		return batch_no_data

	return {}


def enforce_counter_rt_inclusive_tax(doc, method=None):
	"""Counter RT (retail counter) bills at GST-inclusive MRP.

	India Compliance rebuilds the taxes table by place of supply and the
	Client Script swaps the template name to its ``- Inc -`` twin, but a
	name-only swap doesn't re-fetch the child rows' ``included_in_print_rate``
	flag (see RT2607575: inclusive template, exclusive rows -> overcharged).

	Runs on before_validate (before the controller calculates totals and
	before IC derives per-item GST amounts) so the whole downstream pass is
	consistent: for any Counter RT invoice it forces the GST output-tax rows
	to inclusive, regardless of which template name or client timing won.
	"""
	if doc.get("pos_profile") != "Counter RT":
		return

	for tax in doc.get("taxes") or []:
		# GST output-tax rows only (SGST/CGST/IGST); leave Freight etc. alone.
		if tax.account_head and "GST" in tax.account_head:
			tax.included_in_print_rate = 1


def invoice_gst_rate(doc):
	"""Total GST rate the items carry, as a percentage, or None if unclear.

	Read off each item's own ``item_tax_rate`` map, summing only the accounts
	that actually appear on this invoice's tax table -- so an in-state bill adds
	SGST 2.5 + CGST 2.5 = 5, and an out-state one takes IGST 5 = 5. Freight has
	to be taxed at the same rate as the goods it carries.

	Returns None only when the items genuinely DISAGREE, rather than guessing:
	no counter invoice has ever mixed rates, so a mixed one means something is
	wrong and silently picking a rate would mis-tax the freight.

	When no item carries a tax template at all -- around 250 counter invoices a
	quarter -- fall back to the nominal rates on the invoice's own GST rows.
	That is the rate those goods are being taxed at anyway, and refusing here
	would block the sale outright.
	"""
	gst_rows = [
		t for t in doc.get("taxes") or [] if t.account_head and "GST" in t.account_head
	]
	if not gst_rows:
		return None

	gst_accounts = [t.account_head for t in gst_rows]

	rates = set()
	for item in doc.get("items") or []:
		if not item.get("item_tax_rate"):
			continue
		try:
			tax_map = json.loads(item.item_tax_rate)
		except (ValueError, TypeError):
			continue
		rate = sum(flt(tax_map.get(account)) for account in gst_accounts)
		if rate:
			rates.add(round(rate, 4))

	if len(rates) == 1:
		return rates.pop()
	if not rates:
		# no item-level rates -- use what the tax table itself charges
		fallback = sum(flt(t.rate) for t in gst_rows)
		return round(fallback, 4) or None
	return None


def apply_freight_charge(doc, method=None):
	"""Push the Freight field into the Freight Outward Charges tax row.

	The cashier types what the customer pays for freight -- GST included, the
	same way item rates are entered. An "Actual" charge cannot itself be
	inclusive (ERPNext rejects it), so on a ``- Inc`` template the row has to
	hold the value BEFORE tax; the GST rows then add the tax back, because they
	are "On Previous Row Total" pointing at the freight row. Entering 100 at 5%
	stores 95.24 and the customer pays exactly 100.

	Runs on before_validate, so it survives India Compliance's update_taxes()
	wiping the tax table client-side -- whatever the browser did, the value is
	restored from the field before totals are computed.

	On an exclusive template the entered amount is already the pre-tax figure
	and is used as-is.
	"""
	if not doc.meta.has_field("custom_freight_amount"):
		return

	freight_row = None
	for tax in doc.get("taxes") or []:
		if tax.charge_type == "Actual" and tax.account_head and "Freight" in tax.account_head:
			freight_row = tax
			break
	if not freight_row:
		return

	entered = flt(doc.get("custom_freight_amount"))
	if not entered:
		freight_row.tax_amount = 0
		return

	inclusive = str(doc.get("taxes_and_charges") or "").replace(" ", "").find("-Inc-") != -1
	if not inclusive:
		freight_row.tax_amount = entered
		return

	rate = invoice_gst_rate(doc)
	if rate is None:
		frappe.throw(
			_(
				"Freight cannot be split out because the items on this invoice carry "
				"different GST rates, so there is no single rate to tax freight at. "
				"Clear the Freight field and add freight as a line item instead."
			),
			title=_("Mixed GST rates"),
		)

	freight_row.tax_amount = flt(entered / (1 + rate / 100.0), 2)


@frappe.whitelist()
def set_user_and_customer_and_branch(user):
    settings = frappe.get_single("Calicut Textiles Settings")

    user_series = [
        doc.series for doc in settings.set_user_series if doc.user == user
    ]

    user_tax = [
        doc.sales_taxes_template for doc in settings.set_user_series if doc.user == user
    ]

    user_branch = [
        doc.branch for doc in settings.set_user_series if doc.user == user
    ]

    default_price = None
    for doc in settings.set_user_series:
        if doc.user == user:
            default_price = doc.price_list
            break

    return {
        "user_series": user_series if user_series else [],
        "default_tax": user_tax[0] if user_tax else None,
        "default_branch": user_branch[0] if user_branch else None,
        "default_price": default_price
    }
