app_name = "modified_connector"
app_title = "Modified Connector"
app_publisher = "hotfix"
app_description = "Connector for modified eCommerce shops"
app_email = "info@hotfix.example"
app_license = "GPLv3"

required_apps = ["frappe/erpnext"]

after_install = "modified_connector.install.after_install"
after_migrate = "modified_connector.install.after_install"
