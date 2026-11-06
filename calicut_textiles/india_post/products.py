"""Product catalogue: dimension/weight limits, tariff routing, contract mapping."""

import math

PRODUCTS = {
	"SP_INLAND_DOC": {
		"label": "Speed Post — Document",
		"tariff": "speed-post",
		"tariff_product_code": "SP",
		"shape": "DOC",
		"is_document": True,
		"weight": (1, 500),
		"length": (1, 42),
		"breadth": (1, 29),
		"height": (1, 2),
		"supports": {"pod": True, "insurance": True, "cod": False, "reg": False, "otp": False, "ack": False},
		"needs_office_ids": False,
		"volumetric": False,
	},
	"SP_INLAND_PARCEL": {
		"label": "Speed Post — Parcel",
		"tariff": "speed-post",
		"tariff_product_code": "SP",
		"shape": "NROL",
		"is_document": False,
		"weight": (501, 35000),
		"length": (14, 150),
		"breadth": (9, 150),
		"height": (1, 150),
		"supports": {"pod": True, "insurance": True, "cod": False, "reg": False, "otp": False, "ack": False},
		"needs_office_ids": False,
		"volumetric": True,
	},
	"BUSINESS_PARCEL": {
		"label": "Business Parcel",
		"tariff": "business-parcel",
		"tariff_product_code": "BP",
		"shape": "NROL",
		"is_document": False,
		"weight": (1, 35000),
		"length": (14, 150),
		"breadth": (9, 150),
		"height": (1, 150),
		"supports": {"pod": False, "insurance": True, "cod": True, "reg": False, "otp": False, "ack": False},
		"needs_office_ids": False,
		"volumetric": True,
	},
	"24_SPEEDPOST_DOC": {
		"label": "24-hour Speed Post — Document (NDD)",
		"tariff": "ddd-ndd",
		"tariff_product_code": "24_SPEEDPOST_DOC",
		"shape": "DOC",
		"is_document": True,
		"weight": (1, 500),
		"length": (1, 42),
		"breadth": (1, 29),
		"height": (1, 2),
		"supports": {"pod": False, "insurance": True, "cod": False, "reg": True, "otp": True, "ack": True},
		"needs_office_ids": True,
		"volumetric": False,
	},
	"48_SPEEDPOST_DOC": {
		"label": "48-hour Speed Post — Document (NDD)",
		"tariff": "ddd-ndd",
		"tariff_product_code": "48_SPEEDPOST_DOC",
		"shape": "DOC",
		"is_document": True,
		"weight": (1, 500),
		"length": (1, 42),
		"breadth": (1, 29),
		"height": (1, 2),
		"supports": {"pod": False, "insurance": True, "cod": False, "reg": True, "otp": True, "ack": True},
		"needs_office_ids": True,
		"volumetric": False,
	},
	"24_SPP_PARSPL": {
		"label": "24-hour Speed Post — Parcel Special (NDD)",
		"tariff": "parspl",
		"tariff_product_code": "24_SPP_PARSPL",
		"shape": "NROL",
		"is_document": False,
		"weight": (1, 5000),
		"length": (14, 150),
		"breadth": (9, 150),
		"height": (1, 150),
		"supports": {"pod": False, "insurance": True, "cod": True, "reg": True, "otp": True, "ack": True},
		# 30.07.2026: "OTP is mandatory for 24_SPP_PARSPL booking".
		"requires_otp": True,
		"needs_office_ids": True,
		"volumetric": True,
	},
}

# What Calicut Textiles actually offers. India Post's UAT says the 24/48-hour
# products are not bookable from Kozhikode (673001), so they are kept above for
# validation completeness but never offered or rate-compared.
OFFERED = ("SP_INLAND_DOC", "SP_INLAND_PARCEL", "BUSINESS_PARCEL")

# UAT-8 (14.10.2026): one test contract covers every product.
UAT_CONTRACT = "41585456"

ALIASES = {"SP": "SP_INLAND_DOC", "BP": "BUSINESS_PARCEL"}


def normalize_type(article_type):
	t = str(article_type or "").strip().upper()
	return ALIASES.get(t, t)


def get_product(article_type):
	return PRODUCTS.get(normalize_type(article_type))


def resolve_speed_post_type(weight_grams):
	"""Spec: below 500 g is SP_INLAND_DOC, above 500 g SP_INLAND_PARCEL.

	Exactly 500 g sits inside the document weight range (1-500), so it is a
	document — the parcel range starts at 501.
	"""
	return "SP_INLAND_DOC" if float(weight_grams or 0) <= 500 else "SP_INLAND_PARCEL"


def concrete_type(article_type, weight_grams):
	""""SP" picks document vs parcel by weight; anything else is taken as given."""
	if str(article_type or "").strip().upper() == "SP":
		return resolve_speed_post_type(weight_grams)
	return normalize_type(article_type)


def volumetric_weight(length, breadth, height):
	"""Grams. Confirmed against the 24_SPP_PARSPL sample: 14x14x28 cm -> 1098 g."""
	try:
		v = float(length) * float(breadth) * float(height)
	except (TypeError, ValueError):
		return 0
	if v <= 0:
		return 0
	return math.ceil(v / 5)


def chargeable_weight(article_type, physical_weight, length, breadth, height):
	p = get_product(article_type)
	phys = float(physical_weight or 0)
	if not p or not p["volumetric"]:
		return phys
	return max(phys, volumetric_weight(length, breadth, height))


def default_shape(article_type):
	p = get_product(article_type)
	return p["shape"] if p else "NROL"


def fits(article_type, weight, length=0, breadth=0, height=0):
	"""Does a parcel physically qualify for this product? Zero dimensions are ignored."""
	p = get_product(article_type)
	if not p:
		return False
	w = float(weight or 0)
	if w < p["weight"][0] or w > p["weight"][1]:
		return False
	for key, v in (("length", length), ("breadth", breadth), ("height", height)):
		v = float(v or 0)
		if v > 0 and (v < p[key][0] or v > p[key][1]):
			return False
	return True
