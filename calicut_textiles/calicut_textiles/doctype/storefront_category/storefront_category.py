# Copyright (c) 2026, sammish and contributors
# For license information, please see license.txt

import frappe
from frappe.utils.nestedset import NestedSet
from frappe.website.utils import clear_cache


class StorefrontCategory(NestedSet):
	nsm_parent_field = "parent_storefront_category"

	def autoname(self):
		# Autoname via field:storefront_category_name; nothing extra to do here.
		pass

	def validate(self):
		if not self.route:
			self.route = self._derive_route()

	def on_update(self):
		NestedSet.on_update(self)
		clear_cache()

	def on_trash(self):
		NestedSet.on_trash(self)
		clear_cache()

	def _derive_route(self):
		"""URL slug — kebab-case from the name. Prefixed with parent slug
		when the parent has a route, so nested categories get
		`sarees/silk-sarees` style paths."""
		slug = frappe.scrub(self.storefront_category_name).replace("_", "-")
		if self.parent_storefront_category:
			parent_route = frappe.db.get_value(
				"Storefront Category", self.parent_storefront_category, "route"
			)
			if parent_route:
				return f"{parent_route}/{slug}"
		return slug
