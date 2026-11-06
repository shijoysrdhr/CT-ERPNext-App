"""India Post Customer Integration — pure rules, no Frappe imports.

Everything here is transcribed from India Post's "Domestic Customer Integration
approach document" (UAT-8, rev 14.10.2026) and is unit-tested without a bench:

    python3 -m unittest calicut_textiles.india_post.test_core   (from the app root)

The Frappe side (doctypes, HTTP calls, booking flow, webhook) lives in
calicut_textiles/calicut_textiles/india_post.py.

Ported in Nov 2026 from the Node "India Post bridge"
(github.com/shijoysrdhr/Indiapost-integration), which it replaces.
"""
