"""India Post shipping from Sales Invoice / Delivery Note — entirely inside ERPNext.

ERPNext talks to India Post's Customer Integration APIs directly (the Node
"bridge" this replaced is retired). Pure rules — products, validation, the
79-field booking payload, label, tracking normalisation — live in
calicut_textiles/india_post/ and are unit-tested without a bench.

DocTypes
    India Post Settings   credentials, sender, booking office, pilot users
    India Post AWB        the AWB number stock (Available/Allocated/Used/Void)
    India Post Shipment   one per invoice: parcel, receiver, booking, label, events
The invoice keeps a summary in custom_ip_* fields (list views, report, print).

Rules that matter
    * An AWB is marked Used only on India Post's acceptance. A rejection puts
      it back — except "duplicate article", which voids it (used elsewhere).
    * India Post has no cancellation API. Cancelling detaches the AWB (kept on
      the shipment as never-reuse) and deletes the label, so a re-book gets a
      fresh AWB and label.
    * Status only moves forward; any "returned" event wins.
"""

import hashlib
import hmac
import json

import frappe
import requests
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.utils import cint, flt, now_datetime

from calicut_textiles.india_post import barcode as BC
from calicut_textiles.india_post import events as EV
from calicut_textiles.india_post import payloads as P
from calicut_textiles.india_post import products as PR
from calicut_textiles.india_post import validate as V

DOCTYPES = ("Sales Invoice", "Delivery Note")
SETTINGS = "India Post Settings"
SHIPMENT = "India Post Shipment"
AWB = "India Post AWB"
OPEN = ("Booked", "In Transit")
DONE = ("Booked", "In Transit", "Delivered", "Returned")
TOKEN_CACHE_KEY = "india_post_access_token"
PROBE_AWB = "ET214330016IN"  # placeholder for dry-run validation; never sent


class IndiaPostError(Exception):
	def __init__(self, message, status=None, body=None):
		super().__init__(message)
		self.status = status
		self.body = body


# ---------------------------------------------------------------------------
# Custom fields on the invoice (created on every migrate — idempotent)
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
	create_custom_fields({"Sales Invoice": _fields("remarks"), "Delivery Note": _fields("instructions")}, update=True)
	# One shipment per document, enforced by the database (two clicks racing).
	if not frappe.db.sql(f"show index from `tab{SHIPMENT}` where Key_name='unique_reference'"):
		frappe.db.add_unique(SHIPMENT, ["reference_doctype", "reference_name"], constraint_name="unique_reference")


# ---------------------------------------------------------------------------
# Settings and access
# ---------------------------------------------------------------------------

def _settings():
	return frappe.get_cached_doc(SETTINGS)


def _settings_dict(s=None):
	s = s or _settings()
	return {
		"customer_id": (s.customer_id or "").strip(),
		"booking_office_id": (s.booking_office_id or "").strip(),
		"booking_office_name": s.booking_office_name,
		"booking_office_pincode": s.booking_office_pincode,
		"label_size": s.label_size or "A6",
		"contracts": {
			"SP_INLAND_DOC": s.contract_sp_doc,
			"SP_INLAND_PARCEL": s.contract_sp_parcel,
			"BUSINESS_PARCEL": s.contract_business_parcel,
		},
		"sender": {
			"name": s.sender_name, "company": s.sender_company,
			"address_line1": s.sender_address_line1, "address_line2": s.sender_address_line2,
			"address_line3": s.sender_address_line3, "city": s.sender_city, "state": s.sender_state,
			"pincode": s.sender_pincode, "mobile": s.sender_mobile, "email": s.sender_email,
			"alt_contact": s.sender_alt_contact, "tax_reference": s.sender_gstin,
		},
	}


def _enabled_for(user=None):
	"""Enabled, and (during a pilot) the user is on the pilot list."""
	try:
		s = _settings()
	except Exception:
		return False
	if not cint(s.enabled):
		return False
	users = [u.strip().lower() for u in (s.pilot_users or "").splitlines() if u.strip()]
	return not users or (user or frappe.session.user).lower() in users


def _check_enabled():
	if not _enabled_for():
		frappe.throw(_("India Post shipping is not enabled for you yet."))


def boot_session(bootinfo):
	"""Tells the form script whether to show the India Post buttons."""
	bootinfo.india_post_enabled = _enabled_for()


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


# ---------------------------------------------------------------------------
# India Post HTTP client
# ---------------------------------------------------------------------------

def _api_base(s=None):
	s = s or _settings()
	return (s.base_url or "https://test.cept.gov.in").rstrip("/") + "/beextcustomer"


def clear_token():
	frappe.cache().delete_value(TOKEN_CACHE_KEY)


def _token(force=False):
	cache = frappe.cache()
	if not force:
		t = cache.get_value(TOKEN_CACHE_KEY)
		if t:
			return t
	s = _settings()
	password = s.get_password("password", raise_exception=False) if s.password else None
	if not s.username or not password:
		raise IndiaPostError(_("India Post login is not set up (India Post Settings → API username/password)."))
	try:
		r = requests.post(
			_api_base(s) + "/v1/access/login",
			json={"username": s.username.strip(), "password": password},
			timeout=30,
		)
		body = r.json()
	except (requests.RequestException, ValueError) as e:
		raise IndiaPostError(_("Could not reach India Post to log in: {0}").format(e)) from e
	if not isinstance(body, dict):
		body = {}
	data = body.get("data") or {}
	if not r.ok or body.get("success") is False or not data.get("access_token"):
		raise IndiaPostError(_("India Post login failed: {0}").format(P.error_text(body, r.status_code)), r.status_code, body)
	# Tokens live 15 minutes (30.07.2026); never trust a longer stated lifetime.
	ttl = min(cint(data.get("expires_in")) or 900, 900)
	cache.set_value(TOKEN_CACHE_KEY, data["access_token"], expires_in_sec=max(ttl - 60, 60))
	return data["access_token"]


def _request(method, path, params=None, json_body=None, timeout=60, raw=False):
	"""Authenticated call. Retries once on 401 (tokens can be revoked early).
	Raises IndiaPostError carrying India Post's own reason for any 4xx/5xx."""
	url = _api_base() + path
	r = None
	for attempt in (0, 1):
		token = _token(force=attempt == 1)
		try:
			r = requests.request(
				method, url, params=params, json=json_body, timeout=timeout,
				headers={"Authorization": f"Bearer {token}"},
			)
		except requests.RequestException as e:
			raise IndiaPostError(_("Could not reach India Post: {0}").format(e)) from e
		if r.status_code != 401:
			break
	if raw and r.ok:
		return r
	try:
		body = r.json()
	except ValueError:
		body = r.text[:500]
	if r.status_code >= 400:
		raise IndiaPostError(P.error_text(body, r.status_code), r.status_code, body)
	return body


def _offices(pincode):
	"""Post offices under a PIN (cached 12 h — reference data)."""
	pin = str(pincode or "").strip()
	key = f"india_post_pincode:{pin}"
	hit = frappe.cache().get_value(key)
	if hit is not None:
		return hit
	body = _request("GET", "/v1/pincode-search", params={"pincode": pin, "office-type": "post"}, timeout=30)
	offices = body if isinstance(body, list) else (body.get("data") if isinstance(body, dict) else None) or []
	frappe.cache().set_value(key, offices, expires_in_sec=12 * 3600)
	return offices


# ---------------------------------------------------------------------------
# Shipment helpers
# ---------------------------------------------------------------------------

def _shipment_for(doctype, name):
	sh = frappe.db.get_value(SHIPMENT, {"reference_doctype": doctype, "reference_name": name}, "name")
	return frappe.get_doc(SHIPMENT, sh) if sh else None


def _shipment_dict(sh):
	return {
		"article_type": sh.article_type, "contract_id": sh.contract_id, "awb": sh.awb,
		"physical_weight": sh.physical_weight, "length": sh.length, "breadth": sh.breadth, "height": sh.height,
		"cod_value": sh.cod_value, "insurance_value": sh.insurance_value,
		"reference_name": sh.reference_name, "booked_on": sh.booked_on, "charge": sh.charge,
		"receiver": {
			"name": sh.receiver_name, "address_line1": sh.receiver_address_line1,
			"address_line2": sh.receiver_address_line2, "address_line3": sh.receiver_address_line3,
			"city": sh.receiver_city, "state": sh.receiver_state, "pincode": sh.receiver_pincode,
			"mobile": sh.receiver_mobile, "email": sh.receiver_email,
		},
	}


def _summary(sh):
	if not sh:
		return None
	return {
		"name": sh.name, "status": sh.status, "awb": sh.awb, "article_type": sh.article_type,
		"product": (PR.get_product(sh.article_type) or {}).get("label"), "charge": flt(sh.charge),
		"booked_on": sh.booked_on, "last_event": sh.last_event, "label": sh.label,
		"errors": [e for e in (sh.errors or "").split("\n") if e],
	}


def _sync_document(sh):
	"""Copy the shipment's summary onto the invoice (custom_ip_* fields)."""
	values = {
		"custom_ip_awb": sh.awb or None,
		"custom_ip_status": "" if sh.status == "Draft" else sh.status,
		"custom_ip_product": sh.article_type if sh.awb else None,
		"custom_ip_charge": flt(sh.charge),  # Currency columns are NOT NULL
		"custom_ip_booked_on": sh.booked_on,
		"custom_ip_last_event": (sh.last_event or "")[:140] or None,
		"custom_ip_last_event_at": sh.last_event_at,
		"custom_ip_delivered_on": sh.delivered_on,
		"custom_ip_label": sh.label or None,
	}
	current = frappe.db.get_value(sh.reference_doctype, sh.reference_name, list(values), as_dict=True) or {}
	changed = {k: v for k, v in values.items() if str(current.get(k) or "") != str(v or "")}
	if changed:
		frappe.db.set_value(sh.reference_doctype, sh.reference_name, changed, update_modified=False)
	return bool(changed)


def _receiver_from(doc):
	address_name = doc.get("shipping_address_name") or doc.get("customer_address")
	a = frappe.db.get_value(
		"Address", address_name,
		["address_line1", "address_line2", "city", "state", "pincode", "phone", "email_id"], as_dict=True,
	) if address_name else None
	a = a or {}
	return {
		"name": doc.get("customer_name") or doc.get("customer"),
		"address_line1": a.get("address_line1") or "",
		"address_line2": a.get("address_line2") or "",
		"address_line3": "",
		"city": a.get("city") or "",
		"state": a.get("state") or "",
		"pincode": (a.get("pincode") or "").strip(),
		"mobile": doc.get("contact_mobile") or a.get("phone") or "",
		"email": doc.get("contact_email") or a.get("email_id") or "",
		"address_name": address_name,
	}


# ---------------------------------------------------------------------------
# AWB stock
# ---------------------------------------------------------------------------

def _allocate_awb(shipment, reference):
	"""Take the lowest available AWB. SKIP LOCKED lets two bookings run at once
	without ever handing out the same number."""
	row = frappe.db.sql(
		f"select name from `tab{AWB}` where status='Available' order by serial limit 1 for update skip locked"
	)
	if not row:
		frappe.throw(_("No India Post AWB numbers left. Add the series India Post issued in India Post Settings."))
	awb = row[0][0]
	frappe.db.set_value(AWB, awb, {"status": "Allocated", "shipment": shipment, "reference_name": reference}, update_modified=False)
	frappe.db.commit()
	return awb


def _set_awb(awb, status, note=None, **extra):
	if not awb or not frappe.db.exists(AWB, awb):
		return
	values = {"status": status, **extra}
	if status == "Available":
		values.update(shipment=None, reference_name=None)
	if note:
		old = frappe.db.get_value(AWB, awb, "note")
		values["note"] = f"{old}\n{note}" if old else note
	frappe.db.set_value(AWB, awb, values, update_modified=False)


def awb_stock():
	counts = dict(frappe.db.sql(f"select status, count(*) from `tab{AWB}` group by status"))
	threshold = cint(_settings().low_stock_alert)
	available = cint(counts.get("Available"))
	return {
		"available": available, "allocated": cint(counts.get("Allocated")), "used": cint(counts.get("Used")),
		"void": cint(counts.get("Void")), "threshold": threshold, "low": available < threshold,
	}


@frappe.whitelist()
def get_awb_stock():
	frappe.only_for("System Manager")
	return awb_stock()


@frappe.whitelist()
def add_awb_series(from_awb, to_awb, note=None):
	"""Load a range India Post issued, e.g. ET21433001XIN .. ET21434000XIN."""
	frappe.only_for("System Manager")
	try:
		prefix, _suffix, rows = BC.expand_series(from_awb, to_awb)
	except ValueError as e:
		frappe.throw(str(e))
	existing = set()
	names = [r[1] for r in rows]
	for i in range(0, len(names), 1000):
		existing.update(frappe.get_all(AWB, filters={"name": ["in", names[i:i + 1000]]}, pluck="name"))
	now, user = now_datetime(), frappe.session.user
	values = [
		(awb, now, now, user, user, 0, 0, awb, "Available", prefix, serial, note or f"Series {rows[0][1]}–{rows[-1][1]}")
		for serial, awb in rows if awb not in existing
	]
	if values:
		frappe.db.bulk_insert(
			AWB,
			fields=["name", "creation", "modified", "owner", "modified_by", "docstatus", "idx",
			        "awb", "status", "prefix", "serial", "note"],
			values=values,
		)
	return {"added": len(values), "already_there": len(existing), "first": rows[0][1], "last": rows[-1][1]}


# ---------------------------------------------------------------------------
# Booking dialog API (public/js/india_post.js)
# ---------------------------------------------------------------------------

@frappe.whitelist()
def get_booking_defaults(doctype, name):
	_check_enabled()
	doc = _load(doctype, name, "read")
	return {
		"receiver": _receiver_from(doc),
		"order_amount": flt(doc.get("grand_total")),
		"outstanding": flt(doc.get("outstanding_amount")),
	}


@frappe.whitelist()
def get_rates(doctype, name, receiver, parcel):
	_check_enabled()
	_load(doctype, name, "read")
	rec = P.normalize_receiver(_parse(receiver))
	parcel = _parse(parcel)
	s = _settings()
	q = {
		"weight": cint(parcel.get("weight")), "source_pincode": s.sender_pincode,
		"destination_pincode": rec["pincode"], "length": flt(parcel.get("length")),
		"breadth": flt(parcel.get("breadth")), "height": flt(parcel.get("height")),
		"insurance_value": flt(parcel.get("insurance_value")), "cod_value": flt(parcel.get("cod_value")),
	}
	quotes, failures = [], []
	candidates = [t for t in PR.OFFERED if PR.fits(t, q["weight"], q["length"], q["breadth"], q["height"])]
	if not candidates:
		failures.append({"article_type": "", "error": _("This weight and size fits neither Speed Post nor Business Parcel.")})
	for t in candidates:
		product = PR.PRODUCTS[t]
		try:
			body = _request("GET", P.TARIFF_ENDPOINTS[product["tariff"]], params=P.tariff_params(t, q), timeout=45)
			if isinstance(body, dict) and body.get("success") is False:
				raise IndiaPostError(P.error_text(body))
			quotes.append(P.normalize_tariff(t, body))
		except IndiaPostError as e:
			failures.append({"article_type": t, "label": product["label"], "error": str(e)})
	quotes.sort(key=lambda x: x["final_amount"])
	return {"quotes": quotes, "failures": failures}


@frappe.whitelist()
def book(doctype, name, receiver, parcel):
	_check_enabled()
	doc = _load(doctype, name, "write")
	if doc.docstatus != 1:
		frappe.throw(_("Submit the document before booking India Post."))
	if cint(doc.get("is_return")):
		frappe.throw(_("Returns cannot be shipped."))

	sh = _shipment_for(doctype, name)
	if sh and sh.status in DONE:
		_sync_document(sh)
		return {"ok": True, "already_booked": True, "shipment": _summary(sh)}

	parcel = _parse(parcel)
	rec = P.normalize_receiver(_parse(receiver))
	requested = parcel.get("article_type") or "SP"
	weight = cint(parcel.get("weight"))
	article_type = PR.concrete_type(requested, weight)
	if article_type not in PR.OFFERED:
		frappe.throw(_("Choose Speed Post or Business Parcel."))
	settings = _settings_dict()

	if not sh:
		sh = frappe.new_doc(SHIPMENT)
		sh.reference_doctype, sh.reference_name = doctype, name
	if not sh.awb:
		sh.set("events", [])  # a fresh booking; old events belonged to a cancelled AWB
	sh.update({
		"customer": doc.get("customer"), "customer_name": doc.get("customer_name"),
		"article_type": article_type,
		"contract_id": P.contract_for(article_type, settings),
		"physical_weight": weight, "length": flt(parcel.get("length")), "breadth": flt(parcel.get("breadth")),
		"height": flt(parcel.get("height")), "cod_value": flt(parcel.get("cod_value")),
		"insurance_value": flt(parcel.get("insurance_value")), "order_amount": flt(doc.get("grand_total")),
		"receiver_name": rec["name"], "receiver_mobile": rec["mobile"], "receiver_email": rec["email"],
		"receiver_address_line1": rec["address_line1"], "receiver_address_line2": rec["address_line2"],
		"receiver_address_line3": rec["address_line3"], "receiver_city": rec["city"],
		"receiver_state": rec["state"], "receiver_pincode": rec["pincode"],
	})

	# Dry run first: a rejection here costs nothing; after submission it costs an AWB.
	probe = _shipment_dict(sh)
	probe["awb"] = sh.awb or PROBE_AWB
	article = P.build_article(probe, settings)
	errors, warnings = V.validate_article(article)
	if not sh.awb:
		errors = [e for e in errors if not e.startswith("Barcode")]
	if errors:
		hint = V.friendly_hint(requested, article)
		sh.status = "Draft" if sh.status != "Failed" else "Failed"
		sh.errors = "\n".join(errors)
		sh.save(ignore_permissions=True)
		frappe.db.commit()
		return {"ok": False, "stage": "validation", "errors": ([hint] if hint else []) + errors}

	if sh.is_new():
		sh.status = "Draft"
		sh.insert(ignore_permissions=True)
	else:
		sh.save(ignore_permissions=True)
	frappe.db.commit()

	result = _submit(sh, doc, settings)
	if not result["ok"]:
		return result

	label_url, label_error = None, None
	try:
		label_url = _make_label(sh)
	except Exception as e:
		label_error = str(e)
		frappe.log_error(title="India Post label failed", message=frappe.get_traceback())
	_sync_document(sh)
	doc.add_comment("Info", _("Booked with India Post — AWB {0} ({1}, ₹{2:.2f})").format(
		sh.awb, PR.PRODUCTS[sh.article_type]["label"], flt(sh.charge)))
	frappe.db.commit()

	stock = awb_stock()
	if stock["low"]:
		warnings.append(_("Only {0} India Post AWB numbers left — ask India Post for a new series.").format(stock["available"]))
	return {"ok": True, "shipment": _summary(sh), "label_url": label_url, "label_error": label_error, "warnings": warnings}


def _fail(sh, doc, errors, keep_awb=False):
	sh.status = "Failed"
	sh.errors = "\n".join(errors)
	if not keep_awb:
		sh.awb = None
	sh.save(ignore_permissions=True)
	_sync_document(sh)
	doc.add_comment("Info", _("India Post booking failed: {0}").format("; ".join(errors)))
	frappe.db.commit()
	return {"ok": False, "stage": "booking", "errors": errors, "shipment": _summary(sh)}


def _submit(sh, doc, settings):
	"""Allocate an AWB and book. Retries with a fresh AWB when India Post says the
	number is already used elsewhere (shared UAT ranges hit this constantly)."""
	# An AWB still held from an earlier attempt whose outcome was unknown: a
	# "duplicate" now may mean that attempt actually went through, so never
	# silently swap it for a new number.
	held = bool(sh.awb)
	for attempt in range(5):
		if not sh.awb:
			sh.awb = _allocate_awb(sh.name, sh.reference_name)
		article = P.build_article(_shipment_dict(sh), settings)
		sh.booking_request = json.dumps({"articles": [article]}, indent=1)
		sh.save(ignore_permissions=True)
		frappe.db.commit()  # the AWB is spoken for before India Post sees it

		try:
			body = _request("POST", f"/process-articles/{settings['customer_id']}",
			                json_body={"articles": [article]}, timeout=180)
		except IndiaPostError as e:
			if isinstance(e.body, dict) and ("error_articles" in e.body or "valid_articles" in e.body):
				body = e.body  # per-article outcome inside an error status
			elif e.status and 400 <= e.status < 500:
				# The whole request was refused, so nothing was booked — free the number.
				_set_awb(sh.awb, "Available")
				return _fail(sh, doc, [str(e)])
			else:
				# Transport failure: the outcome is unknown. Keep the AWB on hold.
				return _fail(sh, doc, [_("India Post did not answer ({0}). Check with India Post before booking again — AWB {1} is kept on hold.").format(e, sh.awb)], keep_awb=True)

		res = P.normalize_booking_response(body, [sh.awb])
		valid = next((v for v in res["valid"] if v["awb"] == sh.awb), None)
		if valid:
			now = now_datetime()
			_set_awb(sh.awb, "Used", used_on=now)
			sh.update({
				"status": "Booked", "booked_on": now, "booked_by": frappe.session.user,
				"charge": flt(valid["calculated_tariff"]), "batch_id": res["batch_id"],
				"correlation_id": res["correlation_id"], "booking_response": json.dumps(body, indent=1, default=str),
				"errors": None, "last_event": "Item Booked", "last_event_at": now,
			})
			sh.save(ignore_permissions=True)
			frappe.db.commit()
			return {"ok": True}

		bad = next((v for v in res["errored"] if v["awb"] == sh.awb), None)
		if bad:
			sh.booking_response = json.dumps(body, indent=1, default=str)
			if P.is_duplicate_error(bad["errors"]) and held and attempt == 0:
				return _fail(sh, doc, [_("India Post says AWB {0} is already booked — the earlier attempt may have gone through. Check with India Post before booking again.").format(sh.awb)], keep_awb=True)
			if P.is_duplicate_error(bad["errors"]):
				_set_awb(sh.awb, "Void", note=_("India Post: already used elsewhere ({0})").format("; ".join(bad["errors"])))
				sh.awb = None
				continue
			_set_awb(sh.awb, "Available")
			return _fail(sh, doc, bad["errors"])

		return _fail(sh, doc, [_("India Post did not report an outcome for AWB {0}. Check with India Post before booking again.").format(sh.awb)], keep_awb=True)

	return _fail(sh, doc, [_("India Post rejected 5 AWB numbers in a row as already used. Check the AWB stock.")])


def _make_label(sh):
	office = ""
	try:
		o = P.pick_default_office(_offices(sh.receiver_pincode))
		office = (o or {}).get("office_name") or ""
	except Exception:
		pass  # the label must not fail because the office directory is down
	payload = [P.build_label(_shipment_dict(sh), _settings_dict(), office)]
	r = _request("POST", "/v1/label/create/domestic", json_body=payload, timeout=120, raw=True)
	content = r.content
	if not content.startswith(b"%PDF"):  # errors arrive as JSON with HTTP 200
		try:
			detail = P.error_text(r.json())
		except ValueError:
			detail = r.text[:300]
		raise IndiaPostError(_("The label service did not return a PDF: {0}").format(detail))
	_delete_label_files(sh, sh.awb)
	f = frappe.get_doc({
		"doctype": "File", "file_name": f"India Post label {sh.awb}.pdf",
		"attached_to_doctype": sh.reference_doctype, "attached_to_name": sh.reference_name,
		"attached_to_field": "custom_ip_label", "is_private": 1, "content": content,
	}).insert(ignore_permissions=True)
	sh.db_set("label", f.file_url, update_modified=False)
	return f.file_url


def _delete_label_files(sh, awb):
	for f in frappe.get_all("File", filters={
		"attached_to_doctype": sh.reference_doctype, "attached_to_name": sh.reference_name,
		"file_name": f"India Post label {awb}.pdf",
	}, pluck="name"):
		frappe.delete_doc("File", f, ignore_permissions=True)


@frappe.whitelist()
def get_label(doctype, name):
	_load(doctype, name, "read")
	sh = _shipment_for(doctype, name)
	if not sh or not sh.awb or sh.status not in DONE:
		frappe.throw(_("This document has not been booked with India Post."))
	if sh.label and frappe.db.exists("File", {"file_url": sh.label}):
		return sh.label
	url = _make_label(sh)
	_sync_document(sh)
	return url


@frappe.whitelist()
def cancel_booking(doctype, name, reason=None):
	"""India Post has no cancellation API. This detaches the AWB (never reused),
	deletes the label and lets the document be booked again. The invoice
	itself is not touched."""
	_check_enabled()
	doc = _load(doctype, name, "write")
	sh = _shipment_for(doctype, name)
	if not sh or sh.status != "Booked":
		frappe.throw(_("Only a booking that has not been handed over yet can be cancelled."))
	try:
		_track([sh])
		sh.reload()
	except Exception:
		pass
	if sh.status != "Booked":
		frappe.throw(_("India Post has already scanned this parcel ({0}). It can't be cancelled here — contact India Post with AWB {1}.").format(sh.last_event, sh.awb))

	old = sh.awb
	reason = (reason or "").strip() or _("no reason given")
	_set_awb(old, "Used", note=_("Booking cancelled {0} by {1}: {2} — never reuse").format(now_datetime(), frappe.session.user, reason))
	_delete_label_files(sh, old)
	sh.update({
		"status": "Cancelled", "awb": None, "label": None, "charge": 0, "booked_on": None,
		"cancelled_awbs": "\n".join(x for x in [sh.cancelled_awbs, old] if x),
		"last_event": _("Cancelled — AWB {0} not to be used").format(old), "last_event_at": now_datetime(),
	})
	sh.save(ignore_permissions=True)
	_sync_document(sh)
	doc.add_comment("Info", _("India Post booking cancelled — AWB {0} must not be used. Reason: {1}").format(old, reason))
	frappe.db.commit()
	return _summary(sh)


# ---------------------------------------------------------------------------
# Tracking: bulk sweep, manual refresh, webhook
# ---------------------------------------------------------------------------

def _apply_events(sh, events, source, declared_status=None):
	"""Merge new events (deduplicated) and move the status forward. Returns True if saved."""
	keys = {r.event_key for r in sh.events}
	added = False
	for e in events:
		k = P.event_key(sh.awb, e)
		if k in keys:
			continue
		keys.add(k)
		added = True
		sh.append("events", {
			"event_at": e.get("event_at"), "description": e.get("description") or e.get("event_code"),
			"event_code": e.get("event_code"), "office_name": e.get("office_name"),
			"office_id": e.get("office_id"), "remarks": (e.get("remarks") or "")[:140],
			"stage": e.get("stage"), "source": source, "event_key": k,
		})
	rows = sorted(sh.events, key=lambda r: str(r.event_at or ""))
	for i, r in enumerate(rows, 1):
		r.idx = i
	sh.events = rows
	proposed = declared_status or EV.status_from_events([{"stage": r.stage} for r in rows])
	status = EV.forward_only(sh.status, proposed)
	latest = rows[-1] if rows else None
	changed = added or status != sh.status
	if not changed:
		return False
	sh.status = status
	if latest:
		sh.last_event = latest.description
		sh.last_event_at = latest.event_at
	if status == "Delivered" and not sh.delivered_on:
		sh.delivered_on = (latest.event_at if latest else None) or now_datetime()
	sh.save(ignore_permissions=True)
	_sync_document(sh)
	return True


def _track(shipments):
	by_awb = {sh.awb.upper(): sh for sh in shipments if sh.awb}
	awbs = list(by_awb)
	changed = 0
	for i in range(0, len(awbs), 500):
		body = _request("POST", "/v1/tracking/bulk", json_body={"bulk": awbs[i:i + 500]}, timeout=120)
		for entry in (body.get("data") if isinstance(body, dict) else None) or []:
			art = P.normalize_tracking_article(entry)
			sh = by_awb.get(art["awb"])
			if sh and art["events"]:
				changed += _apply_events(sh, art["events"], "Tracking", art["status"])
	return changed


@frappe.whitelist()
def refresh_status(doctype, name):
	_load(doctype, name, "read")
	sh = _shipment_for(doctype, name)
	if not sh:
		return None
	if sh.awb and sh.status in OPEN:
		try:
			_track([sh])
		except IndiaPostError as e:
			frappe.throw(_("India Post tracking failed: {0}").format(e))
	_sync_document(sh)
	return _summary(sh)


def sync_open_shipments():
	"""Scheduler (every 30 min): pull tracking for every parcel still on its way."""
	if not cint(frappe.db.get_single_value(SETTINGS, "enabled")):
		return
	names = frappe.get_all(SHIPMENT, filters={"status": ["in", OPEN], "awb": ["is", "set"]}, pluck="name")
	for i in range(0, len(names), 500):
		try:
			_track([frappe.get_doc(SHIPMENT, n) for n in names[i:i + 500]])
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title="India Post tracking sync failed", message=frappe.get_traceback())


@frappe.whitelist(allow_guest=True, methods=["GET", "POST"])
def webhook():
	"""India Post's real-time events. Public by necessity (they authenticate by
	IP whitelist); verifies an HMAC signature when a secret is configured, and
	only ever touches shipments whose AWB we booked."""
	if frappe.request.method == "GET":
		return "ok"  # the developer portal's Test button
	raw = frappe.request.get_data() or b""
	secret = None
	try:
		s = frappe.get_cached_doc(SETTINGS)
		secret = s.get_password("webhook_secret", raise_exception=False) if s.webhook_secret else None
	except Exception:
		pass
	if secret:
		provided = (frappe.get_request_header("X-Indiapost-Signature") or frappe.get_request_header("X-Webhook-Signature")
		            or frappe.get_request_header("X-Signature") or "").replace("sha256=", "")
		expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
		if not hmac.compare_digest(provided, expected):
			frappe.local.response.http_status_code = 401
			return {"received": False}
	try:
		payload = json.loads(raw or b"{}")
	except ValueError:
		return {"received": False}
	items = payload if isinstance(payload, list) else [payload]
	for p in items:
		if not isinstance(p, dict) or not p.get("article_number"):
			continue
		try:
			e = P.normalize_webhook(p)
			name = frappe.db.get_value(SHIPMENT, {"awb": e["awb"]}, "name")
			if name:
				_apply_events(frappe.get_doc(SHIPMENT, name), [e], "Webhook")
		except Exception:
			frappe.log_error(title="India Post webhook event failed", message=f"{p}\n\n{frappe.get_traceback()}")
	frappe.db.commit()
	return {"received": True}


# ---------------------------------------------------------------------------
# Settings page helpers
# ---------------------------------------------------------------------------

@frappe.whitelist()
def test_connection():
	"""Settings → Test connection: log in and look up the booking office's PIN."""
	frappe.only_for("System Manager")
	clear_token()
	_token(force=True)
	s = _settings()
	offices = _offices(s.booking_office_pincode or s.sender_pincode)
	ids = {str(o.get("office_id")) for o in offices}
	return {
		"logged_in": True,
		"offices": [{"office_id": o.get("office_id"), "office_name": o.get("office_name"),
		             "bookable": o in P.bookable_offices(offices)} for o in offices],
		"booking_office_found": (s.booking_office_id or "") in ids,
	}
