from pathlib import Path

import frappe
from babel.messages.pofile import read_po
from frappe.tests import IntegrationTestCase

APP = "modified_connector"
CORE_APPS = ("frappe", "erpnext", "integration_log")


def po_messages(app: str, lang: str = "de") -> dict[str, str] | None:
	"""Translations without context of an app's <lang>.po, None if the app or file does not exist."""
	try:
		path = Path(frappe.get_app_path(app)) / "locale" / f"{lang}.po"
	except Exception:
		return None
	if not path.exists():
		return None
	with open(path, "rb") as f:
		return {
			m.id: m.string
			for m in read_po(f)
			if isinstance(m.id, str) and m.id and m.string and not m.context
		}


def effective_german() -> dict[str, str]:
	"""Merged de translations of the installed apps; a later app wins, as in frappe.translate."""
	merged: dict[str, str] = {}
	for app in frappe.get_installed_apps():
		merged.update(po_messages(app) or {})
	return merged


class TestTranslations(IntegrationTestCase):
	def test_no_override_of_core_translations(self):
		"""App translations apply site-wide: a msgid without context that frappe, erpnext or
		integration_log translate differently must not be in this app's de.po (use msgctxt where a
		different meaning is needed)."""
		ours = po_messages(APP)
		conflicts = []
		for core in CORE_APPS:
			for msgid, msgstr in (po_messages(core) or {}).items():
				if msgid in ours and ours[msgid] != msgstr:
					conflicts.append(f"{msgid!r}: {ours[msgid]!r} vs {core} {msgstr!r}")
		self.assertEqual(conflicts, [])
