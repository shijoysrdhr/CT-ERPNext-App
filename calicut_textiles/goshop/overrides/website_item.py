"""Override Webshop's Website Item to allow one Website Item per batch
instead of one per Item.

Calicut Textiles models each batch (`custom_batch_no`) as its own storefront
product — different price, qty, and gallery — so the stock uniqueness
constraint on `item_code` blocks legitimate new batches. We relax it to a
uniqueness constraint on (item_code, custom_batch_no) instead.
"""

import frappe
from frappe import _
from webshop.webshop.doctype.website_item.website_item import WebsiteItem


class CTWebsiteItem(WebsiteItem):
	def validate_duplicate_website_item(self):
		batch_no = self.get("custom_batch_no")
		if not batch_no:
			# Fall back to stock behaviour when the batch field is empty —
			# better to keep some uniqueness than none at all.
			return super().validate_duplicate_website_item()

		existing = frappe.db.exists(
			"Website Item",
			{
				"item_code": self.item_code,
				"custom_batch_no": batch_no,
				"name": ("!=", self.name),
			},
		)
		if existing:
			frappe.throw(
				_("Website Item already exists for Item {0} with Batch {1}").format(
					frappe.bold(self.item_code), frappe.bold(batch_no)
				),
				title=_("Already Published"),
			)

	def update_template_item(self):
		# Webshop auto-creates / republishes a Website Item for the parent
		# Template whenever a Variant is published. The CT storefront is
		# batch-driven, not variant-driven — that orphan template row leaks
		# into the catalog as a phantom product with no batch and zero stock.
		# Skip it.
		return
