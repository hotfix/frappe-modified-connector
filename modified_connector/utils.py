import functools
import re

import frappe
import frappe.translate


def log(
	shop: str,
	operation: str,
	reference: str,
	status: str,
	message: str = "",
	ref_doctype: str | None = None,
	ref_name: str | None = None,
) -> str:
	doc = frappe.get_doc(
		{
			"doctype": "Modified Sync Log",
			"shop": shop,
			"operation": operation,
			"reference": reference,
			"status": status,
			"message": message,
			"ref_doctype": ref_doctype,
			"ref_name": ref_name,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


def log_error_once(shop: str, operation: str, reference: str, message: str) -> bool:
	return log_once(shop, operation, reference, "Error", message)


def log_once(shop: str, operation: str, reference: str, status: str, message: str) -> bool:
	"""Log, unless the latest log for this reference has the same status and the same message (avoids a
	log per run). The messages are compared independent of their language: the scheduler and a user
	retry may run in different languages. Return True if a log was written."""
	last = frappe.get_all(
		"Modified Sync Log",
		filters={"shop": shop, "operation": operation, "reference": reference},
		fields=["status", "message"],
		order_by="creation desc",
		limit=1,
	)
	if last and last[0].status == status and same_message(last[0].message, message):
		return False
	log(shop, operation, reference, status, message)
	return True


def same_message(a: str | None, b: str | None) -> bool:
	"""Equal messages, or the same translatable message (and arguments) in two languages."""
	a, b = a or "", b or ""
	return a == b or _canonical(a) == _canonical(b)


MIN_LITERAL = 8  # a template with less fixed text ("{0}", "Item {0}") is too generic to recognise


def _canonical(message: str):
	"""(msgid, arguments) if the message is a formatted msgid or its translation, else the message."""
	for source, pattern in _patterns():
		match = pattern.fullmatch(message)
		if match:
			return source, match.groups()
	return message


_LANGUAGE_PATTERNS: dict[str, list] = {}  # per process; translations change only with a deploy


def _patterns() -> list:
	"""Compiled templates of every msgid and its translations in the enabled languages."""
	patterns = []
	for lang in frappe.get_all("Language", filters={"enabled": 1}, pluck="name"):
		if lang != "en":
			patterns.extend(_language_patterns(lang))
	return patterns


def _language_patterns(lang: str) -> list:
	if lang not in _LANGUAGE_PATTERNS:
		_LANGUAGE_PATTERNS[lang] = [
			(source, pattern)
			for source, translated in frappe.translate.get_all_translations(lang).items()
			if "{" in source and translated
			for pattern in map(_template_pattern, (source, translated))
			if pattern
		]
	return _LANGUAGE_PATTERNS[lang]


@functools.lru_cache(maxsize=4096)
def _template_pattern(template: str):
	parts = re.split(r"\{[^{}]*\}", template)
	if len(parts) < 2 or sum(len(p) for p in parts) < MIN_LITERAL:
		return None
	return re.compile("(.*?)".join(re.escape(p) for p in parts), re.DOTALL)
