"""Optional integration with the central log app `integration_log` (see hooks.integration_logs).

This module never imports integration_log at module level; the app is only called when installed.
"""

import frappe


def severity(doc) -> str:
	"""error for failed rows, warning for orders waiting for an import status, else info."""
	if doc.status == "Error":
		return "error"
	if doc.status == "Skipped" and not doc.ref_name:
		from modified_connector.sync import order_import

		if order_import.is_waiting_message(doc.message):
			return "warning"
	return "info"


def retry(log_name: str) -> str | None:
	from modified_connector.modified_connector.doctype.modified_sync_log import modified_sync_log

	return modified_sync_log.retry(log_name)


def can_retry(log_name: str) -> bool:
	from modified_connector.modified_connector.doctype.modified_sync_log import modified_sync_log

	return modified_sync_log.can_retry(frappe.get_doc("Modified Sync Log", log_name))


def notify_changed(log_name: str) -> None:
	"""Re-mirror a log row that was changed with frappe.db.set_value (no document events)."""
	if "integration_log" in frappe.get_installed_apps():
		frappe.get_attr("integration_log.mirror.sync")("Modified Sync Log", log_name)
