app_name = "modified_connector"
app_title = "Modified Connector"
app_publisher = "hotfix"
app_description = "Connector for modified eCommerce shops"
app_email = "info@hotfix.example"
app_license = "GPLv3"

required_apps = ["frappe/erpnext"]

# Tile on the apps screen (default "Apps" desktop mode). The "Desktop Icons" mode uses
# desktop_icon/modified_ecommerce.json.
add_to_apps_screen = [
	{
		"name": "modified_connector",
		"logo": "/assets/modified_connector/images/logo.svg",
		"title": "modified eCommerce",
		"route": "/desk/modified-ecommerce",
		"has_permission": "modified_connector.install.has_app_permission",
	}
]

after_install = "modified_connector.install.after_install"
after_migrate = "modified_connector.install.after_install"
before_uninstall = "modified_connector.install.before_uninstall"

scheduler_events = {
	"cron": {"*/5 * * * *": ["modified_connector.tasks.import_all_shops"]},
	"daily": ["modified_connector.tasks.full_stock_sync_all_shops"],
}

doc_events = {
	"Sales Invoice": {"on_submit": "modified_connector.sync.order_import.on_invoice_submit"},
	"Stock Ledger Entry": {"on_submit": "modified_connector.sync.stock.on_stock_change"},
}

# Optional: mirrors errors and waiting orders into the central log app "integration_log" (if installed).
integration_logs = [
	{
		"doctype": "Modified Sync Log",
		"source": "Modified Shop",
		"area_field": "shop",
		"status_field": "status",
		"severity_map": {"Error": "error", "Skipped": "info", "Success": "info"},
		"severity_method": "modified_connector.integration.severity",
		"message_field": "message",
		"context_fields": ["shop", "operation", "reference"],
		"group_fields": ["shop", "operation", "reference"],
		"link_fields": ["ref_doctype", "ref_name"],
		"retry_method": "modified_connector.integration.retry",
		"can_retry_method": "modified_connector.integration.can_retry",
	}
]

doctype_js = {"Sales Invoice": "public/js/sales_invoice.js"}

# Custom hook: label apps call these after storing a label (see README, "Integration hooks").
on_shipping_label_created = ["modified_connector.sync.shipping.on_label_created"]
