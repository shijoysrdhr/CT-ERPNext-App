"""India Post shipping from Sales Invoice / Delivery Note.

ERPNext never talks to India Post directly. It calls the India Post bridge
(Node service, ~/Desktop/Projects/Indiapost-integration) which validates,
books, prints labels and tracks. The bridge holds no ERPNext credentials:
every call goes ERPNext -> bridge, and tracking status is pulled back on a
schedule.

site_config.json:
    "india_post_bridge_url":   "https://ship.calicuttextiles.com"
    "india_post_bridge_token": "<same value as ERP_BRIDGE_TOKEN on the bridge>"
    "india_post_users":        ["a@x.com", ...]   # optional: only these users see it
                                                  # (pilot); omit to show everyone
"""

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import frappe
import requests
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.utils import cint, flt, get_system_timezone

DOCTYPES = ("Sales Invoice", "Delivery Note")

# Bridge status -> what ERPNext shows.
STATUS_LABELS = {
	"booked": "Booked",
	"in_transit": "In Transit",
	"delivered": "Delivered",
	"returned": "Returned",
	"failed": "Failed",
	"cancelled": "Cancelled",
}
OPEN_STATUSES = ("Booked", "In Transit")
# Statuses after which "Book India Post" is offered again.
REBOOKABLE = ("", None, "Failed", "Cancelled")

REQUEST_TIMEOUT = 150  # booking + label can take a while on India Post's side


# ---------------------------------------------------------------------------
# Custom fields (created on every migrate — idempotent)
# ---------------------------------------------------------------------------

def _fields(insert_after):
	common = {"read_only": 1, "allow_on_submit": 1, "no_copy": 1, "print_hide": 1}
	return [
		{"fieldname": "custom_india_post_section", "fieldtype": "Section Break", "label": "India Post",
		 "collapsible": 1, "insert_after": insert_after, "print_hide": 1},
		{"fieldname": "custom_ip_awb", "fieldtype": "Data", "label": "India Post AWB",
		 "insert_after": "custom_india_post_section", "search_index": 1, "in_standard_filter": 1, **common},
		{"fieldname": "custom_ip_status", "fieldtype": "Select", "label": "Shipping Status",
		 "options": "\nBooked\nIn Transit\nDelivered\nReturned\nFailed\nCancelled",
		 "insert_after": "custom_ip_awb", "in_standard_filter": 1, **common},
		{"fieldname": "custom_ip_product", "fieldtype": "Data", "label": "India Post Product",
		 "insert_after": "custom_ip_status", **common},
		{"fieldname": "custom_ip_charge", "fieldtype": "Currency", "label": "India Post Charge",
		 "insert_after": "custom_ip_product", **common},
		{"fieldname": "custom_ip_label", "fieldtype": "Attach", "label": "Shipping Label",
		 "insert_after": "custom_ip_charge", **common},
		{"fieldname": "custom_ip_column_break", "fieldtype": "Column Break",
		 "insert_after": "custom_ip_label"},
		{"fieldname": "custom_ip_booked_on", "fieldtype": "Datetime", "label": "Booked On",
		 "insert_after": "custom_ip_column_break", **common},
		{"fieldname": "custom_ip_last_event", "fieldtype": "Data", "label": "Last Tracking Event",
		 "insert_after": "custom_ip_booked_on", **common},
		{"fieldname": "custom_ip_last_event_at", "fieldtype": "Datetime", "label": "Last Event At",
		 "insert_after": "custom_ip_last_event", **common},
		{"fieldname": "custom_ip_delivered_on", "fieldtype": "Datetime", "label": "Delivered On",
		 "insert_after": "custom_ip_last_event_at", **common},
	]


def setup_custom_fields():
	"""after_migrate hook."""
	create_custom_fields(
		{
			"Sales Invoice": _fields("remarks"),
			"Delivery Note": _fields("instructions"),
		},
		update=True,
	)


# ---------------------------------------------------------------------------
# Bridge client
# ---------------------------------------------------------------------------

def _bridge():
	url = (frappe.conf.get("india_post_bridge_url") or "").rstrip("/")
	token = frappe.conf.get("india_post_bridge_token")
	if not url or not token:
		frappe.throw(_("India Post is not set up on this site yet (india_post_bridge_url / india_post_bridge_token)."))
	return url, token


def _call(method, path, payload=None, params=None, raw=False):
	url, token = _bridge()
	try:
		r = requests.request(
			method,
			f"{url}/api/erp{path}",
			json=payload,
			params=params,
			headers={"Authorization": f"Bearer {token}"},
			timeout=REQUEST_TIMEOUT,
		)
	except requests.RequestException as e:
		frappe.throw(_("Could not reach the India Post service: {0}").format(e))

	if raw and r.ok:
		return r.content
	try:
		body = r.json()
	except ValueError:
		body = {"error": r.text[:300]}
	if not r.ok:
		frappe.throw(_("India Post service error ({0}): {1}").format(r.status_code, body.get("error") or body))
	return body


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load(doctype, name, ptype):
	if doctype not in DOCTYPES:
		frappe.throw(_("India Post shipping is only available on {0}").format(", ".join(DOCTYPES)))
	doc = frappe.get_doc(doctype, name)
	doc.check_permission(ptype)
	return doc


def _parse(value):
	if isinstance(value, str):
		return json.loads(value) if value else {}
	return value or {}


def _local(iso):
	"""Bridge timestamps are ISO/UTC; ERPNext stores naive system-timezone datetimes."""
	if not iso:
		return None
	try:
		dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
	except ValueError:
		return None
	if dt.tzinfo is None:
		return dt
	return dt.astimezone(ZoneInfo(get_system_timezone())).replace(tzinfo=None)


def _values(s):
	return {
		"custom_ip_awb": s.get("awb"),
		"custom_ip_status": STATUS_LABELS.get(s.get("status"), ""),
		"custom_ip_product": s.get("articleType"),
		"custom_ip_charge": flt(s.get("charge")) if s.get("charge") is not None else None,
		"custom_ip_booked_on": _local(s.get("bookedAt")),
		"custom_ip_last_event": (s.get("lastEvent") or "")[:140] or None,
		"custom_ip_last_event_at": _local(s.get("lastEventAt")),
		"custom_ip_delivered_on": _local(s.get("deliveredAt")),
	}


def _apply(doctype, name, shipment):
	"""Write bridge status onto the document; returns True when something changed."""
	values = _values(shipment)
	current = frappe.db.get_value(doctype, name, list(values), as_dict=True) or {}
	changed = {k: v for k, v in values.items() if str(current.get(k) or "") != str(v or "")}
	if changed:
		frappe.db.set_value(doctype, name, changed, update_modified=False)
	return bool(changed)


def _attach_label(doctype, name, awb):
	file_name = f"India Post label {awb}.pdf"
	existing = frappe.db.get_value(
		"File",
		{"attached_to_doctype": doctype, "attached_to_name": name, "file_name": file_name},
		"file_url",
	)
	if existing:
		url = existing
	else:
		pdf = _call("GET", "/label", params={"doctype": doctype, "name": name}, raw=True)
		f = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": file_name,
				"attached_to_doctype": doctype,
				"attached_to_name": name,
				"attached_to_field": "custom_ip_label",
				"is_private": 1,
				"content": pdf,
			}
		)
		f.insert(ignore_permissions=True)
		url = f.file_url
	frappe.db.set_value(doctype, name, "custom_ip_label", url, update_modified=False)
	return url


def _receiver_from(doc):
	address_name = doc.get("shipping_address_name") or doc.get("customer_address")
	a = frappe.db.get_value(
		"Address",
		address_name,
		["address_line1", "address_line2", "city", "state", "pincode", "phone", "email_id"],
		as_dict=True,
	) if address_name else None
	a = a or {}
	return {
		"name": doc.get("customer_name") or doc.get("customer"),
		"addressLine1": a.get("address_line1") or "",
		"addressLine2": a.get("address_line2") or "",
		"addressLine3": "",
		"city": a.get("city") or "",
		"state": a.get("state") or "",
		"pincode": (a.get("pincode") or "").strip(),
		"mobile": doc.get("contact_mobile") or a.get("phone") or "",
		"email": doc.get("contact_email") or a.get("email_id") or "",
		"addressName": address_name,
	}


# ---------------------------------------------------------------------------
# Whitelisted API (called from public/js/india_post.js)
# ---------------------------------------------------------------------------

@frappe.whitelist()
def get_booking_defaults(doctype, name):
	doc = _load(doctype, name, "read")
	return {
		"receiver": _receiver_from(doc),
		"orderAmount": flt(doc.get("grand_total")),
		"outstanding": flt(doc.get("outstanding_amount")),
	}


@frappe.whitelist()
def get_rates(doctype, name, receiver, parcel):
	_load(doctype, name, "read")
	return _call("POST", "/quote", {"receiver": _parse(receiver), "parcel": _parse(parcel)})


@frappe.whitelist()
def book(doctype, name, receiver, parcel):
	if not _enabled_for():
		frappe.throw(_("India Post booking is not enabled for you yet."))
	doc = _load(doctype, name, "write")
	if doc.docstatus != 1:
		frappe.throw(_("Submit the document before booking India Post."))
	if cint(doc.get("is_return")):
		frappe.throw(_("Returns cannot be shipped."))
	if doc.get("custom_ip_awb") and doc.get("custom_ip_status") not in REBOOKABLE:
		frappe.throw(_("Already booked with India Post — AWB {0}.").format(doc.custom_ip_awb))

	receiver = _parse(receiver)
	receiver.pop("addressName", None)
	result = _call(
		"POST",
		"/book",
		{
			"source": {"doctype": doctype, "name": name},
			"customerName": doc.get("customer_name"),
			"receiver": receiver,
			"parcel": _parse(parcel),
			"user": frappe.session.user,
		},
	)

	shipment = result.get("shipment") or {}
	if not result.get("ok"):
		# A booking India Post rejected is worth recording; a local validation
		# failure is just shown in the dialog for the user to fix.
		if result.get("stage") == "booking":
			frappe.db.set_value(doctype, name, "custom_ip_status", "Failed", update_modified=False)
			doc.add_comment("Info", _("India Post booking failed: {0}").format("; ".join(result.get("errors") or [])))
		return result

	_apply(doctype, name, shipment)
	label_url = None
	if shipment.get("hasLabel") and shipment.get("awb"):
		try:
			label_url = _attach_label(doctype, name, shipment["awb"])
		except Exception:
			frappe.log_error(title="India Post label attach failed", message=frappe.get_traceback())
	if not result.get("alreadyBooked"):
		doc.add_comment(
			"Info",
			_("Booked with India Post — AWB {0} ({1}{2})").format(
				shipment.get("awb"),
				shipment.get("articleType"),
				f", ₹{flt(shipment.get('charge')):.2f}" if shipment.get("charge") is not None else "",
			),
		)
	result["labelUrl"] = label_url
	return result


@frappe.whitelist()
def get_label(doctype, name):
	doc = _load(doctype, name, "read")
	if not doc.get("custom_ip_awb"):
		frappe.throw(_("This document has not been booked with India Post."))
	return _attach_label(doctype, name, doc.custom_ip_awb)


@frappe.whitelist()
def refresh_status(doctype, name):
	_load(doctype, name, "read")
	rows = _call("POST", "/status", {"refs": [{"doctype": doctype, "name": name}]})
	if rows and rows[0].get("status"):
		_apply(doctype, name, rows[0])
	return rows[0] if rows else None


def _enabled_for(user=None):
	"""Configured, and (during a pilot) the user is on india_post_users."""
	if not (frappe.conf.get("india_post_bridge_url") and frappe.conf.get("india_post_bridge_token")):
		return False
	allowed = frappe.conf.get("india_post_users")
	return not allowed or (user or frappe.session.user) in allowed


def boot_session(bootinfo):
	"""Tells the form script whether to show the India Post buttons."""
	bootinfo.india_post_enabled = _enabled_for()


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

def sync_open_shipments():
	"""Pull tracking status for every parcel still on its way (cron, every 30 min)."""
	if not (frappe.conf.get("india_post_bridge_url") and frappe.conf.get("india_post_bridge_token")):
		return
	refs = []
	for doctype in DOCTYPES:
		for name in frappe.get_all(
			doctype,
			filters={"docstatus": 1, "custom_ip_status": ["in", OPEN_STATUSES], "custom_ip_awb": ["is", "set"]},
			pluck="name",
		):
			refs.append({"doctype": doctype, "name": name})

	changed = 0
	for i in range(0, len(refs), 500):
		try:
			rows = _call("POST", "/status", {"refs": refs[i : i + 500]})
		except Exception:
			frappe.log_error(title="India Post status sync failed", message=frappe.get_traceback())
			return
		for row in rows:
			src = row.get("source") or {}
			if row.get("status") and src.get("doctype") in DOCTYPES:
				changed += _apply(src["doctype"], src["name"], row)
	if changed:
		frappe.db.commit()
