"""Spec conformance for the pure India Post rules — no bench needed.

Run from the app root:  python3 -m unittest calicut_textiles.india_post.test_core -v
Sample payloads are the ones printed in India Post's approach document.
"""

import unittest
from datetime import datetime

from .barcode import build_barcode, check_digit, expand_series, is_valid_barcode
from .events import forward_only, stage_for
from .payloads import (
	build_article,
	build_label,
	event_key,
	is_duplicate_error,
	normalize_booking_response,
	normalize_receiver,
	normalize_tracking_article,
	normalize_webhook,
	pick_default_office,
	split_address,
	tariff_params,
)
from .products import chargeable_weight, concrete_type, fits, resolve_speed_post_type, volumetric_weight
from .validate import friendly_hint, validate_article

SETTINGS = {
	"customer_id": "3000064781",
	"booking_office_id": "21260024",
	"booking_office_name": "Chennai HO",
	"booking_office_pincode": "600001",
	"label_size": "A6",
	"contracts": {"SP_INLAND_DOC": "41585456", "SP_INLAND_PARCEL": "41585456", "BUSINESS_PARCEL": "41367422"},
	"sender": {
		"name": "KRA CAMS", "company": "Calicut Textiles", "address_line1": "CHENNAI", "address_line2": "CHENNAI",
		"city": "CHENNAI", "state": "TAMIL NADU", "pincode": "600001", "mobile": "9234567890", "email": "",
	},
}

SHIPMENT = {
	"article_type": "SP_INLAND_DOC", "awb": "ET214330016IN", "physical_weight": 15,
	"length": 20, "breadth": 12, "height": 1, "cod_value": 0, "insurance_value": 0,
	"reference_name": "RT2622013",
	"receiver": {
		"name": "Saurabh Shukla", "address_line1": "NEW DELHI GPO", "address_line2": "NEW DELHI GPO",
		"city": "CENTRAL DELHI", "state": "CENTRAL DELHI", "pincode": "110001", "mobile": "9234567890",
	},
}

SPEC_KEYS = [
	"bulk_customer_id", "contract_id", "barcode_no", "pickup_or_dropoff", "pickup_dropoff_office_id",
	"article_type", "physical_weight", "shape_of_article", "length", "breadth_diameter", "height", "priority_flag",
	"delivery_instruction", "delivery_slot", "instruction_rts", "sender_name", "sender_company", "sender_add_line_1",
	"sender_add_line_2", "sender_add_line_3", "sender_city", "sender_state", "sender_pincode", "sender_emailid",
	"sender_alt_contact", "sender_kyc", "sender_tax_reference", "receiver_name", "receiver_company",
	"receiver_add_line_1", "receiver_add_line_2", "receiver_add_line_3", "receiver_city", "receiver_state",
	"receiver_pincode", "receiver_emailid", "receiver_alt_contact", "receiver_kyc", "receiver_tax_reference",
	"alt_address_flag", "pickup_address_flag", "drop_off_pincode", "sender_mobile_no", "receiver_mobile_no",
	"prepayment_code", "value_of_prepayment", "codr_cod", "value_for_codr_cod", "insurance_type",
	"value_of_insurance", "ack", "reg", "otp", "bulk_reference", "pickup_address_id", "pickup_addressee_name",
	"pickup_company_name", "pickup_address_line1", "pickup_address_line2", "pickup_address_line3", "pickup_city",
	"pickup_state", "pickup_pincode", "pickup_email_id", "pickup_alt_contact_no", "pickup_mobile_no",
	"pickup_schedule_slot", "pickup_schedule_date", "alt_addressee_name", "alt_company_name", "alt_address_line1",
	"alt_address_line2", "alt_address_line3", "alt_city", "alt_state", "alt_pincode", "alt_email_id",
	"alt_contact_no", "alt_alternate_mobile_no",
]

ARTICLE = build_article(SHIPMENT, SETTINGS)


def errors_of(changes):
	return validate_article({**ARTICLE, **changes})[0]


class TestBarcode(unittest.TestCase):
	def test_check_digits_match_the_uat_range(self):
		self.assertEqual(build_barcode("ET", 21433001), "ET214330016IN")
		self.assertEqual(build_barcode("ET", 21434000), "ET214340000IN")
		# AWBs India Post actually accepted in UAT on 5-6 Nov 2026.
		for awb in ("ET214335129IN", "ET214335132IN", "ET214335146IN", "ET214335163IN"):
			self.assertTrue(is_valid_barcode(awb), awb)

	def test_remainder_rules(self):
		self.assertIn(check_digit("00000000"), (5,))  # remainder 0 -> 5

	def test_expand_the_spec_test_series(self):
		prefix, suffix, rows = expand_series("ET21433001XIN", "ET21434000XIN")
		self.assertEqual((prefix, suffix, len(rows)), ("ET", "IN", 1000))
		self.assertEqual(rows[0], (21433001, "ET214330016IN"))

	def test_bad_check_digit(self):
		self.assertFalse(is_valid_barcode("ET214330017IN"))


class TestBookingPayload(unittest.TestCase):
	def test_field_set_and_order_match_the_spec(self):
		self.assertEqual(list(ARTICLE), SPEC_KEYS)
		self.assertEqual(len(ARTICLE), 79)

	def test_dropoff_defaults(self):
		self.assertEqual(ARTICLE["drop_off_pincode"], "600001")
		self.assertEqual(ARTICLE["pickup_mobile_no"], "0")
		self.assertEqual(ARTICLE["pickup_address_flag"], "FALSE")
		self.assertEqual(ARTICLE["pickup_dropoff_office_id"], "21260024")

	def test_non_cod_is_blank_not_false(self):
		self.assertEqual((ARTICLE["codr_cod"], ARTICLE["value_for_codr_cod"]), ("", ""))
		self.assertEqual((ARTICLE["insurance_type"], ARTICLE["value_of_insurance"]), ("", 0))

	def test_cod_and_insurance(self):
		a = build_article({**SHIPMENT, "article_type": "BUSINESS_PARCEL", "physical_weight": 1200, "length": 30,
		                   "breadth": 20, "height": 10, "cod_value": 2500, "insurance_value": 2500}, SETTINGS)
		self.assertEqual((a["codr_cod"], a["value_for_codr_cod"], a["insurance_type"]), ("COD", 2500, "DOP"))
		self.assertEqual(a["contract_id"], "41367422")
		self.assertEqual(a["shape_of_article"], "NROL")
		self.assertEqual(validate_article(a)[0], [])

	def test_bulk_reference_capped_at_50(self):
		self.assertEqual(len(build_article({**SHIPMENT, "reference_name": "x" * 70}, SETTINGS)["bulk_reference"]), 50)

	def test_clean_article_validates(self):
		self.assertEqual(validate_article(ARTICLE)[0], [])

	def test_parspl_always_sends_otp(self):
		a = build_article({**SHIPMENT, "article_type": "24_SPP_PARSPL", "physical_weight": 1000,
		                   "length": 14, "breadth": 14, "height": 28}, SETTINGS)
		self.assertEqual(a["otp"], "TRUE")
		self.assertTrue(any("OTP must be TRUE" in e for e in validate_article({**a, "otp": "FALSE"})[0]))

	def test_shape_follows_the_product(self):
		# The bridge kept a stale DOC shape when a document was re-booked as a parcel.
		a = build_article({**SHIPMENT, "article_type": "SP_INLAND_PARCEL", "physical_weight": 600,
		                   "length": 30, "breadth": 21, "height": 5}, SETTINGS)
		self.assertEqual(a["shape_of_article"], "NROL")
		self.assertEqual(validate_article(a)[0], [])


class TestValidation(unittest.TestCase):
	CASES = [
		({"pickup_dropoff_office_id": ""}, "Dropoff Officeid is required"),
		({"pickup_dropoff_office_id": "212600"}, "exactly 8 digits"),
		({"receiver_pincode": "11000"}, "Receiver pincode must be exactly 6 digits"),
		({"sender_name": ""}, "Sender name is required"),
		({"physical_weight": 15.5}, "whole number"),
		({"physical_weight": 900}, "between 1 and 500 g"),
		({"height": 5}, "Height must be between 1 and 2 cm"),
		({"barcode_no": "ET214330017IN"}, "invalid check digit"),
		({"barcode_no": "ET21433001IN"}, "must be 13 characters"),
		({"receiver_mobile_no": "12345"}, "exactly 10 digits"),
		({"delivery_slot": "8am-9am"}, "Delivery slot must be one of"),
		({"shape_of_article": "NROL"}, "must be DOC"),
		({"receiver_city": "XY"}, "between 3 and 80 characters"),
		({"receiver_mobile_no": "1234567890"}, "must start with 6, 7, 8 or 9"),
		({"sender_mobile_no": "5234567890"}, "Sender mobile number must start with"),
		({"sender_add_line_3": "XY"}, "Sender address line 3 must be between 3 and 80"),
		({"bulk_reference": "R" * 51}, "at most 50 characters"),
		({"receiver_add_line_1": "A" * 80, "receiver_add_line_2": "B" * 80, "receiver_add_line_3": "C" * 81},
		 "line 3 must be between"),
	]

	def test_documented_rejections(self):
		for changes, needle in self.CASES:
			with self.subTest(needle=needle):
				errs = errors_of(changes)
				self.assertTrue(any(needle in e for e in errs), errs)

	def test_three_full_80_char_lines_are_fine(self):
		self.assertEqual(errors_of({"receiver_add_line_1": "A" * 80, "receiver_add_line_2": "B" * 80,
		                            "receiver_add_line_3": "C" * 80}), [])

	def test_thick_light_parcel_gets_a_plain_hint(self):
		a = build_article({**SHIPMENT, "physical_weight": 450, "length": 30, "breadth": 21, "height": 5}, SETTINGS)
		self.assertIn("Business Parcel", friendly_hint("SP", a))
		self.assertIsNone(friendly_hint("SP", ARTICLE))


class TestWeights(unittest.TestCase):
	def test_volumetric_matches_the_parspl_sample(self):
		self.assertEqual(volumetric_weight(14, 14, 28), 1098)
		self.assertEqual(chargeable_weight("24_SPP_PARSPL", 1000, 14, 14, 28), 1098)
		self.assertEqual(chargeable_weight("SP_INLAND_DOC", 100, 40, 20, 2), 100)

	def test_speed_post_split(self):
		self.assertEqual(resolve_speed_post_type(499), "SP_INLAND_DOC")
		self.assertEqual(resolve_speed_post_type(500), "SP_INLAND_DOC")  # doc range is 1-500
		self.assertEqual(resolve_speed_post_type(501), "SP_INLAND_PARCEL")
		self.assertEqual(concrete_type("SP", 600), "SP_INLAND_PARCEL")
		self.assertEqual(concrete_type("BUSINESS_PARCEL", 100), "BUSINESS_PARCEL")

	def test_fits(self):
		self.assertTrue(fits("BUSINESS_PARCEL", 450, 30, 21, 5))
		self.assertFalse(fits("SP_INLAND_DOC", 450, 30, 21, 5))


class TestBookingResponse(unittest.TestCase):
	SPEC_RESPONSE = {
		"success": True, "batch_id": "batch_3000064781_1756664817431", "mail_booking_dom_id": 386807208610000,
		"correlation_id": "1000000444_386807208610000_1756664817432_906",
		"valid_articles": [{"barcode_no": "EB468827991IN", "index": 0, "offset_number": "3506", "block_number": 24,
		                    "calculated_tariff": 17, "currency": "INR"}],
		"error_articles": [{"barcode_no": "EB468790992IN", "index": 1, "errors": ["Dropoff pincode must be exactly 6 digits"]}],
	}

	def test_splits_valid_from_errored(self):
		n = normalize_booking_response(self.SPEC_RESPONSE, ["EB468827991IN", "EB468790992IN"])
		self.assertEqual(n["valid"][0]["calculated_tariff"], 17)
		self.assertEqual(n["errored"][0]["errors"], ["Dropoff pincode must be exactly 6 digits"])
		self.assertEqual(n["unaccounted"], [])

	def test_detects_an_unreported_article(self):
		n = normalize_booking_response(self.SPEC_RESPONSE, ["EB468827991IN", "EB468790992IN", "ET214330016IN"])
		self.assertEqual(n["unaccounted"], ["ET214330016IN"])

	def test_duplicate_detection(self):
		self.assertTrue(is_duplicate_error(["Duplicate article: Already booked today or yesterday"]))
		self.assertFalse(is_duplicate_error(["Receiver pincode must be exactly 6 digits"]))


class TestTracking(unittest.TestCase):
	def test_webhook_sample(self):
		e = normalize_webhook({
			"article_number": "AW784699994IN", "event_date": "2025-11-09", "event_time": "08:37:52",
			"event_office_facility_id": "21250003", "event_office_name": "KADUGODI BNPL CENTRE",
			"event_code": "BAG_CLOSE", "event_description": "Bag Close",
		})
		self.assertEqual((e["awb"], e["stage"]), ("AW784699994IN", "in_transit"))
		self.assertEqual(e["event_at"], datetime(2025, 11, 9, 8, 37, 52))  # IST, naive

	def test_delivered(self):
		t = normalize_tracking_article({
			"booking_details": {"article_number": "EB126023474IN"},
			"tracking_details": [
				{"date": "2025-08-12T00:00:00Z", "time": "20:51:17", "office": "Bengaluru GPO", "officeid": 21250001, "event": "Item Bagged"},
				{"date": "2025-08-13T00:00:00Z", "time": "15:31:52", "office": "Vijayanagar S.O", "officeid": 21660187, "event": "Item Delivered"},
			],
			"del_status": {"del_status": "delivered"},
		})
		self.assertEqual(t["status"], "Delivered")
		self.assertEqual(t["latest"]["description"], "Item Delivered")

	def test_new_format_sorted_oldest_first_and_read_as_ist(self):
		t = normalize_tracking_article({
			"booking_details": {"article_number": "RK775227016IN"},
			"tracking_details": [
				{"date": "2026-09-07T15:24:12Z", "time": "15:24:12", "officeid": "22660021", "event": "Item Kept on Hold", "remarks": "Intimation Delivered"},
				{"date": "2026-09-05T23:24:16.038Z", "time": "23:24:16", "officeid": "22360017", "event": "Item Booked"},
				{"date": "2026-09-05T11:46:07.883Z", "time": "11:46:07", "officeid": "22360017", "event": "Pickup Request Raised"},
			],
			"del_status": {"del_status": "not delivered"},
		})
		self.assertEqual(t["events"][0]["description"], "Pickup Request Raised")
		self.assertEqual(t["latest"]["remarks"], "Intimation Delivered")
		self.assertEqual(t["status"], "In Transit")
		self.assertEqual(t["events"][1]["event_at"], datetime(2026, 9, 5, 23, 24, 16))

	def test_return_leg_stays_returned(self):
		t = normalize_tracking_article({
			"booking_details": {"article_number": "EB2IN"},
			"tracking_details": [
				{"date": "2026-09-08", "time": "10:00:00", "event": "Item Returned to Sender"},
				{"date": "2026-09-09", "time": "09:00:00", "event": "Taken out for delivery"},
			],
			"del_status": {"del_status": "delivered"},
		})
		self.assertEqual(t["status"], "Returned")

	def test_rts_delivery_and_portal_names(self):
		self.assertEqual(stage_for(None, "Item Delivered(Sender)"), "returned")
		self.assertEqual(stage_for("ITEM_DELIVERY", "Item not Delivered"), "exception")
		self.assertEqual(stage_for(None, "Item Hold"), "exception")
		self.assertEqual(stage_for(None, "Bag Opened"), "in_transit")
		self.assertEqual(stage_for(None, "Item Returned"), "returned")

	def test_status_only_moves_forward(self):
		self.assertEqual(forward_only("Delivered", "In Transit"), "Delivered")
		self.assertEqual(forward_only("Booked", "In Transit"), "In Transit")

	def test_event_key_matches_across_sources(self):
		wh = normalize_webhook({"article_number": "et1in", "event_date": "2026-11-07", "event_time": "10:00:00",
		                        "event_description": "Item Booked", "event_office_facility_id": "22840008"})
		tr = normalize_tracking_article({"booking_details": {"article_number": "ET1IN"}, "tracking_details": [
			{"date": "2026-11-07T10:00:00Z", "time": "10:00:00", "event": "Item Booked", "officeid": 22840008}]})["events"][0]
		self.assertEqual(event_key("ET1IN", wh), event_key("ET1IN", tr))


class TestReceiverAndLabel(unittest.TestCase):
	def test_long_erpnext_address_is_reflowed(self):
		r = normalize_receiver({
			"name": "Ranju Menon", "mobile": "+91 81490 83630", "pincode": " 412101",
			"address_line1": "DEEPAM HOUSE NO.2/140, BEHIND MUNIMJI BUNGALOW,INDIRA COLONY,VIKAS NAGAR KIWALE , 412101 extra words to push it over",
		})
		self.assertEqual(r["mobile"], "8149083630")
		self.assertEqual(r["pincode"], "412101")
		self.assertTrue(all(len(r[k]) <= 80 for k in ("address_line1", "address_line2", "address_line3")))
		self.assertTrue(r["address_line2"])

	def test_split_address(self):
		self.assertEqual(split_address("a " * 10), ["a a a a a a a a a a"])

	def test_label_constants_and_amount(self):
		lab = build_label({**SHIPMENT, "charge": 57, "booked_on": datetime(2026, 11, 6, 10, 42, 52)}, SETTINGS, "Mudavoor SO")
		for k, v in {"channel_type": "E", "user_type": "R", "booking_type": "COMMERCIAL", "payment_mode": "CO",
		             "payment_status": "PC", "identifier": "Domestic", "service_type": "SP", "size": "A6"}.items():
			self.assertEqual(lab[k], v, k)
		self.assertEqual(lab["total_amount"], 57)  # postage, never the invoice value
		self.assertEqual(lab["bkg_ref_id"], "RT2622013")
		self.assertEqual(lab["booking_datetime"], "06-11-2026 10:42:52")
		self.assertEqual(build_label({**SHIPMENT, "article_type": "BUSINESS_PARCEL"}, SETTINGS)["service_type"], "BP")


class TestTariffAndOffices(unittest.TestCase):
	def test_speed_post_params(self):
		p = tariff_params("SP_INLAND_PARCEL", {"weight": 600, "source_pincode": "673004", "destination_pincode": "686669",
		                                       "length": 30, "breadth": 21, "height": 5})
		self.assertEqual(p, {"product-code": "SP", "weight": 600, "source-pincode": "673004",
		                     "destination-pincode": "686669", "length": 30, "width": 21, "height": 5})

	def test_business_parcel_insurance_is_lowercase(self):
		p = tariff_params("BUSINESS_PARCEL", {"weight": 550, "source_pincode": "141010", "destination_pincode": "110057",
		                                      "length": 10, "breadth": 5, "height": 2, "insurance_value": 1000})
		self.assertEqual((p["product-code"], p["ins"]), ("BP", 1000))

	def test_default_office_prefers_bookable(self):
		offices = [
			{"office_id": "22360035", "office_type_code": "HPO", "delivery_office_flag": False},
			{"office_id": "22840008", "office_type_code": "IDC", "delivery_office_flag": True, "is_rolled_out": True},
		]
		self.assertEqual(pick_default_office(offices)["office_id"], "22840008")


if __name__ == "__main__":
	unittest.main()
