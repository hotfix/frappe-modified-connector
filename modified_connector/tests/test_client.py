from datetime import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from frappe.tests import UnitTestCase

from modified_connector.api.client import ModifiedAPIError, ModifiedClient

BASE = "https://shop.example"
API = BASE + "/api/v1"
FAR_FUTURE = 4102444800


def resp(status=200, body=None, text=None):
	r = MagicMock()
	r.status_code = status
	r.json.return_value = body
	r.text = text if text is not None else ("" if body is None else "json")
	return r


def token(access="A1", refresh="R1", expires=FAR_FUTURE):
	return resp(
		200,
		{
			"access_token": access,
			"token_type": "Bearer",
			"expires": expires,
			"refresh_token": refresh,
			"refresh_expires": FAR_FUTURE,
		},
	)


def make_client():
	return ModifiedClient(BASE + "/", "user", "secret")


class TestModifiedClient(UnitTestCase):
	def test_login_on_first_call(self):
		with patch("requests.Session.request") as req:
			req.side_effect = [token(), resp(200, {"customers_id": "1"})]
			self.assertEqual(make_client().me(), {"customers_id": "1"})
		first, second = req.call_args_list
		self.assertEqual(first.args[:2], ("POST", API + "/oauth"))
		self.assertEqual(first.kwargs["data"], {"username": "user", "password": "secret"})
		self.assertEqual(second.args[:2], ("GET", API + "/me"))
		self.assertEqual(second.kwargs["headers"]["Authorization"], "Bearer A1")

	def test_401_triggers_refresh_and_retry(self):
		with patch("requests.Session.request") as req:
			req.side_effect = [
				token(),
				resp(401, None, "expired"),
				token("A2", "R2"),
				resp(200, {"ok": 1}),
			]
			self.assertEqual(make_client().request("GET", "/me"), {"ok": 1})
		calls = req.call_args_list
		self.assertEqual(calls[2].args[:2], ("POST", API + "/oauth/refresh"))
		self.assertEqual(calls[2].kwargs["data"], {"refresh_token": "R1"})
		self.assertEqual(calls[3].kwargs["headers"]["Authorization"], "Bearer A2")

	def test_refresh_401_falls_back_to_login(self):
		with patch("requests.Session.request") as req:
			req.side_effect = [
				token(),
				resp(401, None, "expired"),
				resp(401, None, "bad refresh"),
				token("A3", "R3"),
				resp(200, {"ok": 1}),
			]
			self.assertEqual(make_client().request("GET", "/me"), {"ok": 1})
		calls = req.call_args_list
		self.assertEqual(calls[3].args[:2], ("POST", API + "/oauth"))
		self.assertEqual(calls[4].kwargs["headers"]["Authorization"], "Bearer A3")

	def test_second_401_raises(self):
		with patch("requests.Session.request") as req:
			req.side_effect = [
				token(),
				resp(401, None, "no"),
				resp(401, None, "no"),
				token("A3", "R3"),
				resp(401, None, "still no"),
			]
			with self.assertRaises(ModifiedAPIError) as cm:
				make_client().request("GET", "/me")
		self.assertEqual(cm.exception.status, 401)

	def test_expired_token_refreshed_proactively(self):
		with patch("requests.Session.request") as req, patch("modified_connector.api.client.time") as t:
			t.time.return_value = 1000
			req.side_effect = [
				token(expires=1100),
				resp(200, {"n": 1}),
				token("A2", "R2"),
				resp(200, {"n": 2}),
			]
			client = make_client()
			client.request("GET", "/me")
			t.time.return_value = 1200
			client.request("GET", "/me")
		calls = req.call_args_list
		self.assertEqual(calls[2].args[:2], ("POST", API + "/oauth/refresh"))
		self.assertEqual(calls[3].kwargs["headers"]["Authorization"], "Bearer A2")

	def test_get_tracking_returns_rows_and_empty_on_404(self):
		rows = [{"tracking_id": "1", "parcel_id": "P1"}]
		with patch("requests.Session.request") as req:
			req.side_effect = [token(), resp(200, rows)]
			self.assertEqual(make_client().get_tracking("1000"), rows)
		self.assertEqual(req.call_args_list[1].args[:2], ("GET", API + "/orders/1000/tracking"))
		with patch("requests.Session.request") as req:
			req.side_effect = [token(), resp(404, None, "not found")]
			self.assertEqual(make_client().get_tracking("1000"), [])
		with patch("requests.Session.request") as req:
			req.side_effect = [token(), resp(500, None, "boom")]
			with self.assertRaises(ModifiedAPIError):
				make_client().get_tracking("1000")

	def test_get_order_requests_products_and_totals(self):
		with patch("requests.Session.request") as req:
			req.side_effect = [token(), resp(200, {"orders": {"orders_id": "7"}})]
			self.assertEqual(make_client().get_order("7"), {"orders": {"orders_id": "7"}})
		call = req.call_args_list[1]
		self.assertEqual(call.args[:2], ("GET", API + "/orders/7"))
		self.assertEqual(call.kwargs["params"], {"with": "products,total,history,tracking"})

	def test_iter_orders_follows_next(self):
		page1 = resp(
			200,
			{
				"paging": {"total": "3", "next": API + "/orders?page=2"},
				"data": [{"orders": {"orders_id": "1"}}, {"orders": {"orders_id": "2"}}],
			},
		)
		page2 = resp(200, {"paging": {"total": "3"}, "data": [{"orders": {"orders_id": "3"}}]})
		with patch("requests.Session.request") as req:
			req.side_effect = [token(), page1, page2]
			ids = [o["orders"]["orders_id"] for o in make_client().iter_orders(None)]
		self.assertEqual(ids, ["1", "2", "3"])
		self.assertEqual(len(req.call_args_list), 3)
		self.assertEqual(req.call_args_list[2].args[:2], ("GET", API + "/orders?page=2"))

	def test_iter_orders_since_format(self):
		berlin = datetime(2026, 10, 30, 13, 7, 5, tzinfo=ZoneInfo("Europe/Berlin"))
		with patch("requests.Session.request") as req:
			req.side_effect = [token(), resp(200, {"data": []})]
			list(make_client().iter_orders(berlin))
		params = req.call_args_list[1].kwargs["params"]
		# the shop expects a unix timestamp (verified against the live shop)
		self.assertEqual(params["from"], 1793362025)
		self.assertEqual(params["with"], "products,total,history,tracking")

	def test_iter_orders_naive_since_is_shop_local_time(self):
		with patch("requests.Session.request") as req:
			req.side_effect = [token(), resp(200, {"data": []})]
			list(make_client().iter_orders(datetime(2026, 10, 30, 13, 7, 5)))
		self.assertEqual(req.call_args_list[1].kwargs["params"]["from"], 1793362025)

	def test_iter_orders_404_means_empty(self):
		with patch("requests.Session.request") as req:
			req.side_effect = [token(), resp(404, None, '{"error":{"message":"no Order found"}}')]
			self.assertEqual(list(make_client().iter_orders(datetime(2030, 1, 1))), [])

	def test_http_error_raises(self):
		with patch("requests.Session.request") as req:
			req.side_effect = [token(), resp(500, None, "boom")]
			with self.assertRaises(ModifiedAPIError) as cm:
				make_client().request("GET", "/orders")
		self.assertEqual(cm.exception.status, 500)
		self.assertEqual(cm.exception.body, "boom")
		self.assertEqual(cm.exception.path, "/orders")

	def test_iter_products_yields_products_subdict(self):
		page = resp(200, {"data": [{"products": {"products_id": "5"}, "products_description": {}}]})
		with patch("requests.Session.request") as req:
			req.side_effect = [token(), page]
			self.assertEqual(list(make_client().iter_products()), [{"products_id": "5"}])

	def test_set_stock_payload(self):
		with patch("requests.Session.request") as req:
			req.side_effect = [token(), resp(200, {})]
			make_client().set_stock("165", 7)
		call = req.call_args_list[1]
		self.assertEqual(call.args[:2], ("PUT", API + "/products/165/products"))
		self.assertEqual(call.kwargs["json"], {"products_quantity": 7})

	def test_add_tracking_and_status(self):
		with patch("requests.Session.request") as req:
			req.side_effect = [token(), resp(200, {}), resp(200, {})]
			client = make_client()
			client.add_tracking("1000", 3, "XYZ")
			client.add_status("1000", 4, "hi", True)
		self.assertEqual(req.call_args_list[1].args[:2], ("POST", API + "/orders/1000/tracking"))
		self.assertEqual(req.call_args_list[1].kwargs["json"], {"carrier_id": 3, "parcel_id": "XYZ"})
		self.assertEqual(req.call_args_list[2].args[:2], ("POST", API + "/orders/1000/status_history"))
		self.assertEqual(
			req.call_args_list[2].kwargs["json"],
			{"orders_status_id": 4, "comments": "hi", "customer_notified": 1},
		)

	def test_foreign_next_url_is_rejected(self):
		nxt = "https://evil.example/api/v1/orders?page=2"
		page = resp(200, {"paging": {"next": nxt}, "data": [{"a": 1}]})
		with patch("requests.Session.request") as req:
			req.side_effect = [token(), page]
			it = make_client().iter_orders(None)
			self.assertEqual(next(it), {"a": 1})
			with self.assertRaises(ModifiedAPIError):
				next(it)
		self.assertEqual(len(req.call_args_list), 2)

	def test_missing_access_token_raises(self):
		with patch("requests.Session.request") as req:
			req.side_effect = [resp(200, {"nope": 1})]
			with self.assertRaises(ModifiedAPIError):
				make_client().me()
