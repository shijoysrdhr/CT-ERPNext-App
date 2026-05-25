"""Storefront announcement (marquee) API.

Called from the Next.js storefront via
`/api/method/calicut_textiles.api.storefront.announcements.list`.
Backed by the `Storefront Announcement` doctype: one record per slot
(Header / Sale Bar), each with a child table of scrolling items.
"""

import frappe


SLOT_NORMALIZE = {
	"Header": "header",
	"Sale Bar": "sale-bar",
}


@frappe.whitelist(allow_guest=True)
def list():
	"""Return all enabled announcements, with their items, keyed by slot.

	Shape (matches the `Announcement` type in `src/lib/api/types.ts`):
	    [
	      { "slot": "header", "tone": "brand", "speed": "normal",
	        "items": ["text 1", "text 2"] },
	      ...
	    ]
	"""
	rows = frappe.get_all(
		"Storefront Announcement",
		filters={"enabled": 1},
		fields=["name", "slot", "tone", "speed"],
	)
	if not rows:
		return []

	# Pull items for all matching parents in one query, then group.
	parent_names = [r.name for r in rows]
	item_rows = frappe.get_all(
		"Storefront Announcement Item",
		filters={"parent": ("in", parent_names), "parenttype": "Storefront Announcement"},
		fields=["parent", "text", "idx"],
		order_by="parent ASC, idx ASC",
	)
	items_by_parent = {}
	for ir in item_rows:
		items_by_parent.setdefault(ir.parent, []).append(ir.text)

	return [
		{
			"slot": SLOT_NORMALIZE.get(r.slot, r.slot.lower().replace(" ", "-")),
			"tone": (r.tone or "Brand").lower(),
			"speed": (r.speed or "Normal").lower(),
			"items": items_by_parent.get(r.name, []),
		}
		for r in rows
	]
