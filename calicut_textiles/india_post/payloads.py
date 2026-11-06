"""Request builders and response normalisers for every India Post API we use.

`shipment` dicts use these keys (an India Post Shipment doc's fields):
    article_type, contract_id, awb, physical_weight, length, breadth, height,
    cod_value, insurance_value, reference_name, booked_on (datetime, IST naive),
    charge, receiver{name, company, address_line1..3, city, state, pincode,
    mobile, email}
`settings` dicts: customer_id, booking_office_id, booking_office_name,
    booking_office_pincode, label_size, contracts{type: id},
    sender{name, company, address_line1..3, city, state, pincode, mobile, email,
    alt_contact, tax_reference}
"""

import re
from datetime import datetime, timedelta, timezone

from .events import stage_for, status_from_events
from .products import UAT_CONTRACT, chargeable_weight, default_shape, get_product, normalize_type, volumetric_weight

IST = timezone(timedelta(hours=5, minutes=30))


def _s(v):
	return "" if v is None else str(v).strip()


def _flag(v):
	return "TRUE" if v else "FALSE"


def _num(v):
	try:
		return float(v or 0)
	except (TypeError, ValueError):
		return 0.0


def _int_or_float(v):
	n = _num(v)
	return int(n) if n == int(n) else n


# ---------------------------------------------------------------------------
# Addresses
# ---------------------------------------------------------------------------

def normalize_mobile(phone):
	"""India Post wants the bare 10-digit number (drop +91 / 0 prefixes)."""
	digits = re.sub(r"\D", "", str(phone or ""))
	return digits[-10:] if len(digits) > 10 else digits


def split_address(text, width=80, lines=3):
	"""Re-flow one long address into up to three lines of <= 80 characters."""
	out, current = [], ""
	for w in str(text or "").split():
		if len(f"{current} {w}".strip()) > width:
			if current:
				out.append(current.strip())
			current = w
		else:
			current = f"{current} {w}".strip()
		if len(out) == lines:
			break
	if current and len(out) < lines:
		out.append(current.strip())
	return out[:lines]


def normalize_receiver(r):
	"""ERPNext addresses are often one long line; India Post wants 3 x 3-80 chars."""
	r = r or {}
	raw = [_s(r.get(k)) for k in ("address_line1", "address_line2", "address_line3")]
	raw = [x for x in raw if x]
	fits = len(raw) <= 3 and all(3 <= len(x) <= 80 for x in raw)
	lines = raw if fits else split_address(", ".join(raw))
	lines += [""] * (3 - len(lines))
	return {
		"name": _s(r.get("name"))[:80],
		"company": _s(r.get("company"))[:80],
		"address_line1": lines[0],
		"address_line2": lines[1],
		"address_line3": lines[2],
		"city": _s(r.get("city")),
		"state": _s(r.get("state")),
		"pincode": re.sub(r"\D", "", _s(r.get("pincode"))),
		"mobile": normalize_mobile(r.get("mobile")),
		"email": _s(r.get("email")),
	}


# ---------------------------------------------------------------------------
# Booking (POST /process-articles/:customId)
# ---------------------------------------------------------------------------

def contract_for(article_type, settings):
	t = normalize_type(article_type)
	return _s((settings.get("contracts") or {}).get(t)) or UAT_CONTRACT


def build_article(shipment, settings):
	"""The flat article India Post expects — all 79 documented fields, in spec order.

	India Post's validator wants optional fields present-but-blank, not absent.
	Only DROPOFF at our booking office is supported (that is how Calicut
	Textiles hands parcels over), so the pickup/alt blocks are always blank.
	"""
	t = normalize_type(shipment.get("article_type"))
	product = get_product(t)
	sender = settings.get("sender") or {}
	rec = shipment.get("receiver") or {}
	cod = _num(shipment.get("cod_value"))
	ins = _num(shipment.get("insurance_value"))
	supports = product["supports"] if product else {}
	return {
		"bulk_customer_id": _s(settings.get("customer_id")),
		"contract_id": _s(shipment.get("contract_id")) or contract_for(t, settings),
		"barcode_no": _s(shipment.get("awb")).upper(),
		"pickup_or_dropoff": "DROPOFF",
		"pickup_dropoff_office_id": _s(settings.get("booking_office_id")),
		"article_type": t,
		"physical_weight": int(round(_num(shipment.get("physical_weight")))),
		"shape_of_article": default_shape(t),
		"length": _int_or_float(shipment.get("length")) or "",
		"breadth_diameter": _int_or_float(shipment.get("breadth")) or "",
		"height": _int_or_float(shipment.get("height")) or "",
		"priority_flag": "",
		"delivery_instruction": "",
		"delivery_slot": "",
		"instruction_rts": "",
		"sender_name": _s(sender.get("name")),
		"sender_company": _s(sender.get("company")),
		"sender_add_line_1": _s(sender.get("address_line1")),
		"sender_add_line_2": _s(sender.get("address_line2")),
		"sender_add_line_3": _s(sender.get("address_line3")),
		"sender_city": _s(sender.get("city")),
		"sender_state": _s(sender.get("state")),
		"sender_pincode": _s(sender.get("pincode")),
		"sender_emailid": _s(sender.get("email")),
		"sender_alt_contact": _s(sender.get("alt_contact")),
		"sender_kyc": "",
		"sender_tax_reference": _s(sender.get("tax_reference")),
		"receiver_name": _s(rec.get("name")),
		"receiver_company": _s(rec.get("company")),
		"receiver_add_line_1": _s(rec.get("address_line1")),
		"receiver_add_line_2": _s(rec.get("address_line2")),
		"receiver_add_line_3": _s(rec.get("address_line3")),
		"receiver_city": _s(rec.get("city")),
		"receiver_state": _s(rec.get("state")),
		"receiver_pincode": _s(rec.get("pincode")),
		"receiver_emailid": _s(rec.get("email")),
		"receiver_alt_contact": "",
		"receiver_kyc": "",
		"receiver_tax_reference": "",
		"alt_address_flag": "FALSE",
		"pickup_address_flag": "FALSE",
		"drop_off_pincode": _s(sender.get("pincode")),
		"sender_mobile_no": _s(sender.get("mobile")),
		"receiver_mobile_no": _s(rec.get("mobile")),
		"prepayment_code": "",
		"value_of_prepayment": 0,
		# The spec is explicit: blank, not FALSE, for non-COD.
		"codr_cod": "COD" if cod > 0 else "",
		"value_for_codr_cod": _int_or_float(cod) if cod > 0 else "",
		"insurance_type": "DOP" if ins > 0 else "",
		"value_of_insurance": _int_or_float(ins),
		"ack": "FALSE",
		"reg": "FALSE",
		# OTP is mandatory for some products (24_SPP_PARSPL) regardless.
		"otp": _flag(product and product.get("requires_otp")),
		"bulk_reference": _s(shipment.get("reference_name"))[:50],
		"pickup_address_id": "",
		"pickup_addressee_name": "",
		"pickup_company_name": "",
		"pickup_address_line1": "",
		"pickup_address_line2": "",
		"pickup_address_line3": "",
		"pickup_city": "",
		"pickup_state": "",
		"pickup_pincode": "",
		"pickup_email_id": "",
		"pickup_alt_contact_no": "0",
		"pickup_mobile_no": "0",
		"pickup_schedule_slot": "",
		"pickup_schedule_date": "",
		"alt_addressee_name": "",
		"alt_company_name": "",
		"alt_address_line1": "",
		"alt_address_line2": "",
		"alt_address_line3": "",
		"alt_city": "",
		"alt_state": "",
		"alt_pincode": "",
		"alt_email_id": "",
		"alt_contact_no": "0",
		"alt_alternate_mobile_no": "",
	}


def normalize_booking_response(body, sent_barcodes):
	"""India Post reports per-article outcomes inside a 200 — split them.

	A barcode we sent that is in neither list is an unknown outcome and must
	never be treated as booked.
	"""
	body = body or {}
	valid = [
		{
			"awb": _s(v.get("barcode_no")).upper(),
			"offset_number": v.get("offset_number"),
			"block_number": v.get("block_number"),
			"calculated_tariff": None if v.get("calculated_tariff") is None else _num(v.get("calculated_tariff")),
			"timestamp": v.get("timestamp"),
		}
		for v in body.get("valid_articles") or []
	]
	errored = []
	for v in body.get("error_articles") or []:
		errs = v.get("errors")
		errored.append({
			"awb": _s(v.get("barcode_no")).upper(),
			"errors": [str(e) for e in errs] if isinstance(errs, list) else [str(errs or "Unknown error")],
		})
	accounted = {x["awb"] for x in valid} | {x["awb"] for x in errored}
	return {
		"success": body.get("success") is not False,
		"batch_id": body.get("batch_id"),
		"correlation_id": body.get("correlation_id"),
		"mail_booking_dom_id": body.get("mail_booking_dom_id"),
		"valid": valid,
		"errored": errored,
		"unaccounted": [b.upper() for b in sent_barcodes if b.upper() not in accounted],
		"timestamp": body.get("timestamp"),
	}


def is_duplicate_error(errors):
	"""India Post's "Duplicate article: Already booked today or yesterday" —
	the AWB is used elsewhere and must never be offered again."""
	return any("duplicate" in str(e).lower() for e in errors or [])


# ---------------------------------------------------------------------------
# Label (POST /v1/label/create/domestic)
# ---------------------------------------------------------------------------

def label_service_type(article_type):
	return "BP" if normalize_type(article_type) == "BUSINESS_PARCEL" else "SP"


def format_label_datetime(dt):
	"""DD-MM-YYYY HH:MM:SS (IST)."""
	return (dt or datetime.now(IST).replace(tzinfo=None)).strftime("%d-%m-%Y %H:%M:%S")


def build_label(shipment, settings, delivery_office_name=""):
	t = normalize_type(shipment.get("article_type"))
	product = get_product(t)
	rec = shipment.get("receiver") or {}
	sender = settings.get("sender") or {}
	physical = int(round(_num(shipment.get("physical_weight"))))
	vol = volumetric_weight(shipment.get("length"), shipment.get("breadth"), shipment.get("height")) if product and product["volumetric"] else 0
	charged = chargeable_weight(t, physical, shipment.get("length"), shipment.get("breadth"), shipment.get("height"))
	customer_id = int(_num(settings.get("customer_id")))
	return {
		"customer_id": customer_id,
		"delivery_office_name": _s(delivery_office_name),
		"destination_pin": _s(rec.get("pincode")),
		"booking_datetime": format_label_datetime(shipment.get("booked_on")),
		"channel_type": "E",  # External
		"user_type": "R",  # Registered
		"user_id": customer_id,
		"barcode_no": _s(shipment.get("awb")).upper(),
		"service_type": label_service_type(t),
		"booking_type": "COMMERCIAL",
		"article_length": _s(_int_or_float(shipment.get("length"))),
		"article_breadth": _s(_int_or_float(shipment.get("breadth"))),
		"article_height": _s(_int_or_float(shipment.get("height"))),
		"charged_weight": _int_or_float(charged),
		"physical_weight": physical,
		"volumetric_weight": vol,
		"insurance_flag": _num(shipment.get("insurance_value")) > 0,
		"insurance_value": _int_or_float(shipment.get("insurance_value")),
		"recipient_name": _s(rec.get("name")),
		"recipient_mobile": _s(rec.get("mobile")),
		"recipient_addressl1": _s(rec.get("address_line1")),
		"recipient_addressl2": _s(rec.get("address_line2")),
		"recipient_addressl3": _s(rec.get("address_line3")),
		"recipient_city": _s(rec.get("city")),
		"recipient_pin": _s(rec.get("pincode")),
		"recipient_state": _s(rec.get("state")),
		"sender_name": _s(sender.get("name")),
		"sender_mobile": _s(sender.get("mobile")),
		"sender_addressl1": _s(sender.get("address_line1")),
		"sender_addressl2": _s(sender.get("address_line2")),
		"sender_addressl3": _s(sender.get("address_line3")),
		"sender_city": _s(sender.get("city")),
		"sender_pin": _s(sender.get("pincode")),
		"sender_state": _s(sender.get("state")),
		"transmission_mode": "S",  # Surface
		"payment_mode": "CO",  # Contractual booking
		"routing_data": f"{_s(rec.get('pincode'))} - {_s(sender.get('pincode'))}".strip(" -"),
		"booking_office_name": _s(settings.get("booking_office_name") or sender.get("city")),
		"booking_office_pin": _s(settings.get("booking_office_pincode") or sender.get("pincode")),
		"size": _s(settings.get("label_size") or "A6"),
		# Postage actually charged — never the order value.
		"total_amount": _int_or_float(shipment.get("charge")),
		"payment_status": "PC",  # Payment Completed
		"value_added_services": "",
		"identifier": "Domestic",
		"bkg_ref_id": _s(shipment.get("reference_name")),
		"priority": False,
		"registered_flag": False,
	}


# ---------------------------------------------------------------------------
# Tariff
# ---------------------------------------------------------------------------

TARIFF_ENDPOINTS = {
	"speed-post": "/v1/speed-post/tariffs",
	"business-parcel": "/v1/business-parcel-tariff/calculate",
	"ddd-ndd": "/v1/ddd-ndd-tariff/calculate",
	"parspl": "/v1/parspl-tariff/calculate",
}


def tariff_params(article_type, q):
	"""Each family names its parameters differently (INS vs ins, office ids only on NDD)."""
	p = get_product(article_type)
	base = {
		"product-code": p["tariff_product_code"],
		"weight": int(round(_num(q.get("weight")))),
		"source-pincode": q.get("source_pincode"),
		"destination-pincode": q.get("destination_pincode"),
	}
	dims = {"length": _int_or_float(q.get("length")), "width": _int_or_float(q.get("breadth")), "height": _int_or_float(q.get("height"))}
	ins = _num(q.get("insurance_value"))
	fam = p["tariff"]
	if fam == "speed-post":
		return {**base, **dims, **({"INS": _int_or_float(ins)} if ins > 0 else {})}
	if fam == "business-parcel":
		return {**base, **dims, **({"ins": _int_or_float(ins)} if ins > 0 else {})}
	ndd = {
		"source-office-id": q.get("source_office_id"),
		"destination-office-id": q.get("destination_office_id"),
		**({"ins": _int_or_float(ins)} if ins > 0 else {}),
		"REG": "FALSE",
		"OTP": "TRUE" if p.get("requires_otp") else "FALSE",
		"ACK": "FALSE",
	}
	if fam == "ddd-ndd":
		return {**base, **ndd}
	cod = _num(q.get("cod_value"))
	return {**base, **dims, **ndd, "COD": "COD" if cod > 0 else "", "CODVALUE": _int_or_float(cod) if cod > 0 else ""}


def normalize_tariff(article_type, body):
	body = body or {}
	return {
		"article_type": normalize_type(article_type),
		"label": (get_product(article_type) or {}).get("label"),
		"chargeable_weight": _num(body.get("chargeable_weight") if body.get("chargeable_weight") is not None else body.get("weight")),
		"base_tariff": _num(body.get("base_tariff")),
		"total_tax": _num(body.get("total_tax")),
		"final_amount": _num(body.get("final_amount")),
		"delivery_type": body.get("delivery_type") or body.get("distance_type"),
		"assured_delivery_day": body.get("assured_delivery_day"),
	}


def error_text(body, status=None):
	"""India Post's own reason from an error body ({error} or {message} or {errors})."""
	if isinstance(body, dict):
		for k in ("error", "message"):
			if body.get(k):
				return str(body[k])
		if body.get("errors"):
			e = body["errors"]
			return "; ".join(str(x.get("message", x) if isinstance(x, dict) else x) for x in (e if isinstance(e, list) else [e]))
	if body:
		return str(body)[:300]
	return f"HTTP {status}" if status else "Unknown error"


# ---------------------------------------------------------------------------
# Pincode search
# ---------------------------------------------------------------------------

def bookable_offices(offices):
	"""Spec: a usable office is a delivery office and not a Branch Post Office."""
	return [
		o for o in offices or []
		if o.get("delivery_office_flag") is True and _s(o.get("office_type_code")).upper() != "BPO"
	]


def pick_default_office(offices):
	b = bookable_offices(offices)
	for test in (
		lambda o: _s(o.get("office_type_code")).upper() == "HPO" and o.get("is_rolled_out"),
		lambda o: _s(o.get("office_type_code")).upper() == "HPO",
		lambda o: o.get("is_rolled_out"),
	):
		for o in b:
			if test(o):
				return o
	return b[0] if b else None


# ---------------------------------------------------------------------------
# Tracking (POST /v1/tracking/bulk) and webhook
# ---------------------------------------------------------------------------

def _ist(date_str, time_str):
	"""Bulk tracking's date carries a misleading "Z" — it is IST. The calendar
	date plus `time`, read as IST, is correct. Returns a naive IST datetime."""
	d = _s(date_str)[:10]
	t = _s(time_str)
	if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
		return None
	if re.fullmatch(r"\d{2}:\d{2}", t):
		t += ":00"
	if not re.fullmatch(r"\d{2}:\d{2}:\d{2}", t):
		t = "00:00:00"
	try:
		return datetime.strptime(f"{d} {t}", "%Y-%m-%d %H:%M:%S")
	except ValueError:
		return None


def normalize_tracking_article(entry):
	booking = entry.get("booking_details") or {}
	events = []
	for e in entry.get("tracking_details") or []:
		events.append({
			"event_at": _ist(e.get("date"), e.get("time")),
			"event_code": e.get("event_code") or "",
			"description": _s(e.get("event")),
			"office_name": _s(e.get("office")),
			"office_id": _s(e.get("officeid")),
			"remarks": _s(e.get("remarks")),
			"stage": stage_for(e.get("event_code"), e.get("event")),
		})
	# Newest-first in the response; keep oldest-first.
	events.sort(key=lambda x: x["event_at"] or datetime.min)
	declared = _s((entry.get("del_status") or {}).get("del_status")).lower()
	return {
		"awb": _s(booking.get("article_number")).upper(),
		"events": events,
		"latest": events[-1] if events else None,
		"declared": declared,
		"status": status_from_events(events, declared),
	}


def normalize_webhook(p):
	"""The webhook posts one flat snake_case event (or a list of them)."""
	return {
		"awb": _s(p.get("article_number")).upper(),
		"event_at": _ist(p.get("event_date"), p.get("event_time")),
		"event_code": _s(p.get("event_code")),
		"description": _s(p.get("event_description")),
		"office_name": _s(p.get("event_office_name")),
		"office_id": _s(p.get("event_office_facility_id")),
		"remarks": _s(p.get("non_delivery_reason")),
		"stage": stage_for(p.get("event_code"), p.get("event_description")),
	}


def event_key(awb, e):
	"""Dedup key: the webhook and the tracking sweep report the same events."""
	at = e["event_at"].strftime("%Y-%m-%d %H:%M:%S") if e.get("event_at") else ""
	what = (e.get("description") or e.get("event_code") or "").strip().lower()
	return "|".join([awb.upper(), what, at, e.get("office_id") or ""])
