"""Override Webshop's Website Item to allow one Website Item per batch
instead of one per Item.

Calicut Textiles models each batch (`custom_batch_no`) as its own storefront
product — different price, qty, and gallery — so the stock uniqueness
constraint on `item_code` blocks legitimate new batches. We relax it to a
uniqueness constraint on (item_code, custom_batch_no) instead.
"""

import frappe
from frappe import _
from frappe.utils import random_string
from webshop.webshop.doctype.website_item.website_item import WebsiteItem


class CTWebsiteItem(WebsiteItem):
	def make_route(self):
		"""Force every Website Item under `/products/<slug>` so the Next.js
		storefront's `/products/[batch]` route matches. Webshop's default uses
		the Item Group's `route` as the prefix — fine when Item Groups have
		that set, broken otherwise (e.g. new "Banarasi Saree R" group). We
		ignore Item Group route entirely and slug from the display name + a
		random suffix to keep routes unique across reorders."""
		if self.route:
			return None
		base = self.web_item_name or self.item_name or self.item_code or self.name
		return "products/" + self.scrub(f"{base}-{random_string(5)}")

	def onload(self):
		# Populate the virtual `custom_aggregate_stock_qty` display field
		# when the form is opened — sums `Bin.actual_qty` across all batches
		# and warehouses of this Item. Only meaningful for standard items;
		# we always compute (cheap, single query) so the field reflects the
		# live value the moment the editor toggles "Is Standard Item".
		super().onload() if hasattr(super(), "onload") else None
		if self.item_code:
			rows = frappe.get_all(
				"Bin",
				filters={"item_code": self.item_code},
				fields=["actual_qty"],
			)
			self.custom_aggregate_stock_qty = sum(
				float(r.actual_qty or 0) for r in rows
			)
		else:
			self.custom_aggregate_stock_qty = 0

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
