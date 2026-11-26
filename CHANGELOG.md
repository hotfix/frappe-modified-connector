# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Order import: shop orders are imported as Sales Invoices every 5 minutes (or with **Import Orders Now**),
  with customer, contact and addresses created or reused. Gross prices, Item Tax Templates from the Tax Map,
  shipping split over the tax rates, coupons and discounts as a discount on the grand total, and a check of
  the grand total against the shop's order total. *Import From* and a retry window limit what is fetched;
  every order is imported once, also with concurrent runs.
- Payments: payment classes with *Paid at Checkout* create a Payment Entry when the invoice is submitted.
- Dropshipping: items delivered by the supplier are marked as drop-shipped; on submit a Delivery Note is
  created for the stock items of such an order.
- Stock sync: stock changes of the shop warehouse are pushed to the shop by a background job per item, plus a
  daily full sync and **Sync Stock Now**.
- Tracking: tracking numbers are sent to the shop with a history entry (customer notified) and the shipped
  status, through the `on_shipping_label_created` hook or the **Send Tracking Number** button on the Sales
  Invoice.
- Shop data: **Load Shop Data** loads order statuses, carriers, payment classes and tax rates from the shop,
  so they are picked by name instead of by id.
- Items from the shop: **Take Over Missing Items** creates Items for shop products without one, and
  *Create Missing Items on Import* does the same during the order import (name, EAN barcode, Item Tax
  Template, gross price in the default selling price list; no opening stock).
- Customers: the Territory of a new customer follows the billing country.
- Modified Sync Log with **Retry** for failed orders and orders waiting for an import status.
- Optional Integration Log support (hook `integration_logs`): errors and waiting orders are mirrored with the
  shop as area, with retry.
- Home screen tile *modified Shop*, workspace and sidebar *modified Shops* (`/desk/modified-shops`) and a
  Desktop Icon.
- German setup guide, also served on the site at `/modified-shop-guide`, and German translation.
- CI: ruff and the server test suite on every push and pull request.
