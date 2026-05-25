"""One-shot patch: seed the default Storefront Category tree on existing
sites that already had calicut_textiles installed before GOShop shipped the
seed. Idempotent — skips any category that already exists."""

from calicut_textiles.goshop.setup import seed_default_categories


def execute():
	seed_default_categories()
