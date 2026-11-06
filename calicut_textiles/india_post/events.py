"""Tracking event catalogue (Bulk Tracking + Webhook event sheets) -> stage -> status."""

import re

EVENTS = {
	# Pickup lifecycle — only for articles booked with a pickup request
	"Unassigned": "pickup",
	"Assigned": "pickup",
	"Cancelled": "exception",
	"Pickedup": "pickup",
	"Inducted": "pickup",
	# Main flow
	"ITEM_BOOK": "booked",
	"ITEM_BAG": "in_transit",
	"BAG_CLOSE": "in_transit",
	"BAG_DISPATCH": "in_transit",
	"ITEM_DISPATCH": "in_transit",
	"BAG_RECEIVE": "in_transit",
	"BAG_OPEN": "in_transit",
	"ITEM_RECEIVE": "in_transit",
	"ITEM_INVOICE": "out_for_delivery",
	"ITEM_TOBO": "out_for_delivery",
	"BEAT_DISPATCH": "out_for_delivery",
	"ITEM_ONHOLD": "exception",
	"ITEM_REDIRECT": "exception",
	"ITEM_RETURN": "returned",
	"ITEM_DELIVERY": "delivered",
}

# Bulk tracking returns descriptions, not codes.
DESCRIPTION_TO_STAGE = {
	"pickup request raised": "pickup",
	"pickup assigned": "pickup",
	"item pickup cancelled": "exception",
	"item pickedup": "pickup",
	"item inducted": "pickup",
	"item booked": "booked",
	"item booking": "booked",
	"item bagged": "in_transit",
	"bag close": "in_transit",
	"bag dispatch": "in_transit",
	"item dispatched": "in_transit",
	"item dispatch": "in_transit",
	"bag received": "in_transit",
	"item received": "in_transit",
	"item receive": "in_transit",
	"item received at destination": "in_transit",
	"item invoiced": "out_for_delivery",
	"item invoiced to bo": "out_for_delivery",
	"beat dispatch": "out_for_delivery",
	"taken out for delivery": "out_for_delivery",
	"item kept on hold": "exception",
	# Names used by the developer portal's webhook event picker (Nov 2026).
	"bag opened": "in_transit",
	"item hold": "exception",
	"item on hold": "exception",
	"item not delivered": "exception",
	"item redirected": "exception",
	"item returned to sender": "returned",
	"item returned": "returned",
	"item return": "returned",
	"item delivered": "delivered",
	"item delivery": "delivered",
}

STAGE_TO_STATUS = {
	"pickup": "Booked",
	"booked": "Booked",
	"in_transit": "In Transit",
	"out_for_delivery": "In Transit",
	"exception": "In Transit",
	"returned": "Returned",
	"delivered": "Delivered",
}

# Status only moves forward (an out-of-order webhook must not walk a delivered
# parcel back to in transit).
STATUS_RANK = {"Draft": 0, "Failed": 1, "Booked": 2, "In Transit": 3, "Returned": 4, "Delivered": 5, "Cancelled": 6}


def stage_for(event_code, event_description):
	lower = str(event_description or "").strip().lower()
	# "Item Delivered(Sender)" is an RTS completion, not a delivery to the customer.
	if "delivered" in lower and "sender" in lower:
		return "returned"
	# A failed attempt may share the delivery event code; never count it as delivered.
	if "not delivered" in lower:
		return "exception"
	code = str(event_code or "").strip()
	if code in EVENTS:
		return EVENTS[code]
	stripped = re.sub(r"\(.*?\)", "", lower).strip()
	return DESCRIPTION_TO_STAGE.get(stripped) or DESCRIPTION_TO_STAGE.get(lower) or "in_transit"


def status_for(event_code, event_description):
	return STAGE_TO_STATUS.get(stage_for(event_code, event_description), "In Transit")


def status_from_events(events, declared=None):
	"""Shipment status from a full event history (oldest first).

	events: [{"stage": ...}, ...]; declared: bulk tracking's del_status.
	After "Item Returned to Sender" the return leg reuses ordinary events, so any
	returned stage wins; del_status "delivered" on a returned article means
	delivered back to us.
	"""
	if any(e.get("stage") == "returned" for e in events):
		return "Returned"
	if str(declared or "").lower() == "delivered":
		return "Delivered"
	if not events:
		return "Booked"
	return STAGE_TO_STATUS.get(events[-1].get("stage"), "In Transit")


def forward_only(current, proposed):
	return proposed if STATUS_RANK.get(proposed, 0) > STATUS_RANK.get(current, 0) else current
