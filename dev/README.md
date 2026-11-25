# Development helpers

## `seed_testshop.py`

Fills a **non-productive** modified eCommerce test shop with demo data for end-to-end tests of the
connector. Python 3 standard library only; run it on the host, not in the bench container.

```bash
export MODIFIED_TEST_URL=https://test.example.com   # with or without /api/v1
export MODIFIED_TEST_USER=api-user@example.com
export MODIFIED_TEST_PASS=...
python dev/seed_testshop.py            # create what is missing
python dev/seed_testshop.py --reset    # delete what the script created
```

Never point it at a live shop: it creates customers and orders.

The script is idempotent. It finds the manufacturer and category by name, products by `products_model`,
customers by e-mail and orders by the marker `[seed-testshop:<scenario>]` in the order comment. An order that
was left incomplete by an aborted run is deleted and created again. Existing products are not changed, so
stock pushed by ERPNext is kept.

It creates:

- tax classes: looked up from the shop's tax rates (19 % and 7 %); the script stops if one is missing
- manufacturer `DEMO Spielwaren GmbH`, category `DEMO Spielzeug`
- products `DEMO-19-A`, `DEMO-19-B`, `DEMO-7-A`, `DEMO-7-B`, `DEMO-DROP-1` (19 %, for a drop-shipped item in
  ERPNext) and `DEMO-19-STOCK`, with EAN, quantity and a net price derived from a round gross price
- customers Erika Musterfrau and Max Schmidt (account, Max with a second address "Kita Sonnenschein, Petra
  Schmidt") and Jonas Gast (guest)
- orders, with totals as a real gross-price order (`ot_subtotal`, `ot_shipping`, `ot_coupon`, one `ot_tax` per
  rate, `ot_gv`, `ot_total`; `final_price` is the gross line total):

| Scenario | Content | Status | Expected in ERPNext (import status ids 1,2) |
| --- | --- | --- | --- |
| A | 1 x 19 %, shipping, `paypal` | 1 | invoice; Payment Entry when paypal is *Paid at Checkout* |
| B | 2 x 19 % + 1 x 7 %, shipping, coupon, `moneyorder` | 2 | invoice, shipping split over both rates, no payment |
| C | guest, parcel to another recipient at the billing street, `invoice` | 1 | invoice with its own shipping address |
| D | 1 x 7 %, then cancelled | 4 | not imported |
| E | stock item + `DEMO-DROP-1`, delivery to the second address | 2 | invoice without stock update; Delivery Note on submit |
| F | gift voucher `ot_gv` | 1 | rejected, error log |
| G | 1 x 7 % | 3 | "Waiting for import status" log |

Order dates are fixed (2026-11-20 to 2026-11-23); set *Import From* of the shop record before them.

API quirks the script works around:

- `POST /customers` with a nested `address_book` answers 500 after the customer was inserted. The script
  creates the customer alone, adds addresses with `POST /customers/{id}/address_book` and sets
  `customers_default_address_id` with a flat `PUT /customers/{id}`.
- `customers_newsletter` must be sent (`"0"`), otherwise the insert fails with a length error.
- Order products and totals are added one by one (`POST /orders/{id}/products`, `/total`); the shop takes the
  values as sent and does not change product stock.
- Listings answer 404 when empty.
