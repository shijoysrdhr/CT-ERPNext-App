"""Mirror of India Post's Bulk Booking validation table.

Catching a bad article here costs nothing; catching it after submission burns
an AWB number. validate_article() takes the flat article dict sent inside
`articles[]` and returns (errors, warnings).
"""

import re

from .barcode import is_valid_barcode
from .products import get_product, normalize_type

DELIVERY_INSTRUCTIONS = ("ND", "OD", "SD")
DELIVERY_SLOTS = ("9am-2pm", "2pm-5pm", "5pm-8pm")
RTS_INSTRUCTIONS = ("RTS", "RTA")
PREPAYMENT_CODES = ("QR", "SQ")
PICKUP_SLOTS = ("10:00-13:00", "13:00-16:00")
SHAPES = ("ROLL", "NROL", "DOC")
ADDRESS_TOTAL_MAX = 240


def _blank(v):
	return v is None or str(v).strip() == ""


def _is_true(v):
	return str(v).strip().upper() in ("TRUE", "1", "YES")


def _number(v):
	try:
		return float(v)
	except (TypeError, ValueError):
		return None


def _text(errors, value, label, required=False, lo=3, hi=80):
	"""Optional free-text fields are "between 3 and 80 characters" *when supplied*."""
	if _blank(value):
		if required:
			errors.append(f"{label} is required")
		return
	n = len(str(value).strip())
	if n < lo or n > hi:
		errors.append(f"{label} must be between {lo} and {hi} characters (got {n})")


def _pincode(errors, value, label, required=True):
	if _blank(value):
		if required:
			errors.append(f"{label} is required")
		return
	if not re.fullmatch(r"\d{6}", str(value).strip()):
		errors.append(f"{label} must be exactly 6 digits")


def _mobile(errors, value, label, required=True):
	if _blank(value) or str(value).strip() == "0":
		if required:
			errors.append(f"{label} is required")
		return
	v = str(value).strip()
	if not re.fullmatch(r"\d{10}", v):
		errors.append(f"{label} must be exactly 10 digits")
	# 30.07.2026: mobile numbers must start with 6, 7, 8 or 9.
	elif v[0] not in "6789":
		errors.append(f"{label} must start with 6, 7, 8 or 9")


def _address(errors, lines, label, required=True):
	# 30.07.2026: three lines, each 3-80 when supplied, together <= 240.
	l1, l2, l3 = lines
	_text(errors, l1, f"{label} address line 1", required=required)
	_text(errors, l2, f"{label} address line 2")
	_text(errors, l3, f"{label} address line 3")
	total = sum(0 if _blank(x) else len(str(x).strip()) for x in lines)
	if total > ADDRESS_TOTAL_MAX:
		errors.append(f"{label} address lines together must not exceed {ADDRESS_TOTAL_MAX} characters (got {total})")


def _enum(errors, value, label, allowed, required=False):
	if _blank(value):
		if required:
			errors.append(f"{label} is required")
		return
	v = str(value).strip()
	if v not in allowed:
		errors.append(f'{label} must be one of [{", ".join(allowed)}] (got "{v}")')


def _dimension(errors, value, label, rng, product_label):
	if _blank(value):
		errors.append(f"{label} is required")
		return
	n = _number(value)
	if n is None:
		errors.append(f"{label} must be a number")
		return
	if n < rng[0] or n > rng[1]:
		errors.append(f"{label} must be between {rng[0]} and {rng[1]} cm for {product_label} (got {n:g})")


def validate_article(a):
	errors, warnings = [], []
	a = a or {}

	# --- identity ---
	if _blank(a.get("bulk_customer_id")):
		errors.append("Bulk customer id is required")
	elif not re.fullmatch(r"\d{10}", str(a["bulk_customer_id"]).strip()):
		errors.append("Bulk customer id must be a 10-digit number")

	if _blank(a.get("contract_id")):
		errors.append("Contract id is required")
	elif not re.fullmatch(r"\d{8}", str(a["contract_id"]).strip()):
		errors.append("Contract id must be an 8-digit number")

	if _blank(a.get("barcode_no")):
		errors.append("Barcode number is required")
	else:
		b = str(a["barcode_no"]).strip().upper()
		if len(b) != 13:
			errors.append(f"Barcode number must be 13 characters (got {len(b)})")
		elif not is_valid_barcode(b):
			errors.append(f"Barcode {b} has an invalid check digit")

	# --- product & physical ---
	product = get_product(a.get("article_type"))
	if _blank(a.get("article_type")):
		errors.append("Article type is required")
	elif not product:
		errors.append(f'Article type "{a.get("article_type")}" is not a supported product')

	weight = _number(a.get("physical_weight"))
	if _blank(a.get("physical_weight")):
		errors.append("Physical weight is required")
	elif weight is None:
		errors.append("Physical weight must be a number")
	elif weight != int(weight):
		errors.append("Physical weight must be a whole number of grams (decimals are not permitted)")
	elif product:
		lo, hi = product["weight"]
		if weight < lo or weight > hi:
			errors.append(f"Physical weight must be between {lo} and {hi} g for {product['label']} (got {weight:g})")
	elif weight < 1 or weight > 35000:
		errors.append("Physical weight must be a whole number between 1 and 35000 grams")

	if product:
		_enum(errors, a.get("shape_of_article"), "Shape of article", SHAPES, required=True)
		shape = str(a.get("shape_of_article") or "").strip().upper()
		if shape:
			if product["is_document"] and shape != "DOC":
				errors.append(f"Shape of article must be DOC for {product['label']}")
			elif not product["is_document"] and shape == "DOC":
				errors.append(f"Shape of article must be ROLL or NROL for {product['label']}")
		_dimension(errors, a.get("length"), "Length", product["length"], product["label"])
		_dimension(errors, a.get("breadth_diameter"), "Breadth/Diameter", product["breadth"], product["label"])
		_dimension(errors, a.get("height"), "Height", product["height"], product["label"])

	# --- pickup / dropoff ---
	pod = str(a.get("pickup_or_dropoff") or "").strip().upper()
	if not pod:
		errors.append("Pickup or dropoff is required")
	elif pod not in ("PICKUP", "DROPOFF"):
		errors.append("Pickup or dropoff must be PICKUP or DROPOFF")
	if pod in ("PICKUP", "DROPOFF"):
		office_label = "Pickup Officeid" if pod == "PICKUP" else "Dropoff Officeid"
		office = a.get("pickup_dropoff_office_id")
		if _blank(office) or str(office) == "0":
			errors.append(f"{office_label} is required when pickup_or_dropoff is {pod}")
		elif not re.fullmatch(r"\d{8}", str(office).strip()):
			errors.append(f"{office_label} must be exactly 8 digits")

	# --- sender ---
	_text(errors, a.get("sender_name"), "Sender name", required=True)
	_text(errors, a.get("sender_company"), "Sender company")
	_address(errors, [a.get("sender_add_line_1"), a.get("sender_add_line_2"), a.get("sender_add_line_3")], "Sender")
	_text(errors, a.get("sender_city"), "Sender city", required=True)
	_text(errors, a.get("sender_state"), "Sender state")
	_pincode(errors, a.get("sender_pincode"), "Sender pincode")
	_text(errors, a.get("sender_emailid"), "Sender email id")
	_mobile(errors, a.get("sender_mobile_no"), "Sender mobile number")

	# --- receiver ---
	_text(errors, a.get("receiver_name"), "Receiver name", required=True)
	_text(errors, a.get("receiver_company"), "Receiver company")
	_address(errors, [a.get("receiver_add_line_1"), a.get("receiver_add_line_2"), a.get("receiver_add_line_3")], "Receiver")
	_text(errors, a.get("receiver_city"), "Receiver city", required=True)
	_text(errors, a.get("receiver_state"), "Receiver state")
	_pincode(errors, a.get("receiver_pincode"), "Receiver pincode")
	_text(errors, a.get("receiver_emailid"), "Receiver email id")
	_mobile(errors, a.get("receiver_mobile_no"), "Receiver mobile number")

	# --- optional services ---
	_enum(errors, a.get("delivery_instruction"), "Delivery instruction", DELIVERY_INSTRUCTIONS)
	_enum(errors, a.get("delivery_slot"), "Delivery slot", DELIVERY_SLOTS)
	_enum(errors, a.get("instruction_rts"), "Instruction RTS", RTS_INSTRUCTIONS)
	_enum(errors, a.get("prepayment_code"), "Prepayment code", PREPAYMENT_CODES)
	cod = str(a.get("codr_cod") or "").strip().upper()
	if cod and cod != "COD":
		errors.append('COD flag must be "COD" or blank')
	if not _blank(a.get("insurance_type")) and str(a["insurance_type"]).strip().upper() != "DOP":
		errors.append('Insurance type must be "DOP" or blank')
	if cod == "COD" and (_number(a.get("value_for_codr_cod")) or 0) <= 0:
		errors.append("COD value must be greater than 0 when the article is marked COD")
	if product:
		sup = product["supports"]
		if not sup["cod"] and cod == "COD":
			warnings.append(f"{product['label']} is not documented as supporting COD")
		if not sup["reg"] and _is_true(a.get("reg")):
			warnings.append(f"{product['label']} does not document REG")
		if not sup["otp"] and _is_true(a.get("otp")):
			warnings.append(f"{product['label']} does not document OTP")
		if not sup["ack"] and _is_true(a.get("ack")):
			warnings.append(f"{product['label']} does not document ACK")
		if product.get("requires_otp") and not _is_true(a.get("otp")):
			errors.append(f"OTP must be TRUE for {product['label']}")
	if not _blank(a.get("bulk_reference")) and len(str(a["bulk_reference"]).strip()) > 50:
		errors.append("Bulk reference must be at most 50 characters")

	# --- conditional address blocks ---
	if _is_true(a.get("pickup_address_flag")) or pod == "PICKUP":
		_text(errors, a.get("pickup_addressee_name"), "Pickup addressee name", required=True)
		_text(errors, a.get("pickup_company_name"), "Pickup company name", required=True)
		_address(errors, [a.get("pickup_address_line1"), a.get("pickup_address_line2"), a.get("pickup_address_line3")], "Pickup")
		_text(errors, a.get("pickup_city"), "Pickup city", required=True)
		_text(errors, a.get("pickup_state"), "Pickup state")
		_pincode(errors, a.get("pickup_pincode"), "Pickup pincode")
		_text(errors, a.get("pickup_email_id"), "Pickup email id")
		_mobile(errors, a.get("pickup_mobile_no"), "Pickup mobile number")
		_enum(errors, a.get("pickup_schedule_slot"), "Pickup schedule slot", PICKUP_SLOTS, required=True)
		date = a.get("pickup_schedule_date")
		if _blank(date):
			errors.append("Pickup schedule date is required when pickup_or_dropoff is PICKUP")
		elif not re.fullmatch(r"\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2} (AM|PM)", str(date).strip(), re.I):
			errors.append("Pickup schedule date must be formatted MM/DD/YYYY HH:MM:SS AM/PM")

	if _is_true(a.get("alt_address_flag")):
		_text(errors, a.get("alt_addressee_name"), "Alt addressee name", required=True)
		_text(errors, a.get("alt_company_name"), "Alt company name", required=True)
		_text(errors, a.get("alt_address_line1"), "Alt address line1", required=True)
		_text(errors, a.get("alt_address_line2"), "Alt address line2")
		_text(errors, a.get("alt_address_line3"), "Alt address line3")
		_text(errors, a.get("alt_city"), "Alt city", required=True)
		_text(errors, a.get("alt_state"), "Alt state")
		_pincode(errors, a.get("alt_pincode"), "Alt pincode")
		_text(errors, a.get("alt_email_id"), "Alt email id")
		_mobile(errors, a.get("alt_alternate_mobile_no"), "Alt alternate mobile number")

	if product and product["needs_office_ids"] and _blank(a.get("pickup_dropoff_office_id")):
		warnings.append(f"{product['label']} requires source and destination office ids for correct rating")

	return errors, warnings


def friendly_hint(article_type_requested, article):
	"""Plain-language advice for the most common counter mistake."""
	if str(article_type_requested or "").upper() == "SP" and normalize_type(article.get("article_type")) == "SP_INLAND_DOC":
		h = _number(article.get("height")) or 0
		l = _number(article.get("length")) or 0
		b = _number(article.get("breadth_diameter")) or 0
		if h > 2 or l > 42 or b > 29:
			return (
				"Speed Post items up to 500 g count as documents and must be at most 42 × 29 × 2 cm. "
				"For a thicker parcel this light, choose Business Parcel."
			)
	return None
