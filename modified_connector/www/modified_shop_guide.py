"""/modified-shop-guide: the German setup guide (docs/setup-guide-de.md), for logged-in users only."""

from pathlib import Path
from urllib.parse import urlencode

import frappe
from frappe.utils import md_to_html

ROUTE = "/modified-shop-guide"
no_cache = 1


def guide_path() -> Path:
	# not get_app_path(..., "setup-guide-de.md"): it would scrub the hyphens of the file name
	return Path(frappe.get_app_path("modified_connector")) / "docs" / "setup-guide-de.md"


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.redirect(f"/login?{urlencode({'redirect-to': ROUTE})}")

	markdown = guide_path().read_text(encoding="utf-8")
	title, _sep, body = markdown.partition("\n")
	context.title = title.lstrip("# ").strip()
	context.body = md_to_html(body)
	context.no_cache = 1
	context.show_sidebar = False
	return context
