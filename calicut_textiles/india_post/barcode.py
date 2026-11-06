"""UPU S10 article identifier, per India Post's "Barcode Generation Logic" sheet.

positions 1-2 service prefix (ET, EK ...), 3-10 eight-digit serial,
11 check digit (weighted modulus 11), 12-13 country code (IN).
"""

import re

WEIGHTS = (8, 6, 4, 2, 3, 5, 9, 7)


def check_digit(serial8):
	digits = str(serial8).zfill(8)
	if not re.fullmatch(r"\d{8}", digits):
		raise ValueError(f'serial must be 8 digits, got "{serial8}"')
	remainder = sum(int(d) * w for d, w in zip(digits, WEIGHTS)) % 11
	if remainder == 0:
		return 5
	if remainder == 1:
		return 0
	return 11 - remainder


def build_barcode(prefix, serial, suffix="IN"):
	p, s = str(prefix).upper(), str(suffix).upper()
	if not re.fullmatch(r"[A-Z]{2}", p):
		raise ValueError(f'prefix must be 2 letters, got "{prefix}"')
	if not re.fullmatch(r"[A-Z]{2}", s):
		raise ValueError(f'suffix must be 2 letters, got "{suffix}"')
	digits = str(serial).zfill(8)
	return f"{p}{digits}{check_digit(digits)}{s}"


def is_valid_barcode(barcode):
	m = re.fullmatch(r"([A-Z]{2})(\d{8})(\d)([A-Z]{2})", str(barcode or "").upper())
	return bool(m) and check_digit(m.group(2)) == int(m.group(3))


def parse_endpoint(value):
	"""Accepts "ET21433001IN", "ET21433001XIN" (X = check-digit placeholder, as the
	spec writes test ranges), a full 13-char AWB, or a bare 8-digit serial."""
	v = str(value or "").strip().upper()
	if re.fullmatch(r"\d{8}", v):
		return {"prefix": None, "serial": int(v), "suffix": None}
	m = re.fullmatch(r"([A-Z]{2})(\d{8})[X\d]([A-Z]{2})", v) or re.fullmatch(r"([A-Z]{2})(\d{8})([A-Z]{2})", v)
	if m:
		return {"prefix": m.group(1), "serial": int(m.group(2)), "suffix": m.group(3)}
	raise ValueError(f'cannot parse "{value}" as an AWB number or 8-digit serial')


def expand_series(from_value, to_value, prefix=None, suffix="IN", max_size=100000):
	"""Every AWB in a range like ET21433001XIN .. ET21434000XIN -> [(serial, awb), ...]."""
	a, b = parse_endpoint(from_value), parse_endpoint(to_value)
	p = (prefix or a["prefix"] or b["prefix"] or "").upper()
	sfx = (a["suffix"] or b["suffix"] or suffix or "IN").upper()
	if not p:
		raise ValueError("A 2-letter service prefix is required (e.g. ET)")
	start, end = a["serial"], b["serial"]
	if end < start:
		raise ValueError("The end of the range is before the start")
	if end - start + 1 > max_size:
		raise ValueError(f"Range too large (max {max_size:,} numbers at a time)")
	return p, sfx, [(s, build_barcode(p, s, sfx)) for s in range(start, end + 1)]
