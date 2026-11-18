app_name = "modified_connector"
app_title = "Modified Connector"
app_publisher = "hotfix"
app_description = "Connector for modified eCommerce shops"
app_email = "info@hotfix.example"
app_license = "GPLv3"

required_apps = ["frappe/erpnext"]

after_install = "modified_connector.install.after_install"
after_migrate = "modified_connector.install.after_install"

scheduler_events = {
	"cron": {"*/5 * * * *": ["modified_connector.tasks.import_all_shops"]},
	"daily": ["modified_connector.tasks.full_stock_sync_all_shops"],
}

doc_events = {
	"Sales Invoice": {"on_submit": "modified_connector.sync.order_import.on_invoice_submit"},
	"Stock Ledger Entry": {"on_submit": "modified_connector.sync.stock.on_stock_change"},
}
