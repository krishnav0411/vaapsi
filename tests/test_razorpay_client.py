"""Unit tests for the Razorpay REST client — no live API in tests
(httpx MockTransport)."""

import base64
import json

import httpx
import pytest

from app.razorpay import RazorpayClient, RazorpayError

KEY_ID, KEY_SECRET = "rzp_test_xxx", "shhh"


def _client(handler) -> RazorpayClient:
    return RazorpayClient(KEY_ID, KEY_SECRET, transport=httpx.MockTransport(handler))


class TestAuth:
    def test_basic_auth_header(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["auth"] = request.headers.get("Authorization", "")
            return httpx.Response(200, json={"id": "sub_1"})

        _client(handler).fetch_subscription("sub_1")
        assert seen["auth"].startswith("Basic ")
        decoded = base64.b64decode(seen["auth"].split()[1]).decode()
        assert decoded == f"{KEY_ID}:{KEY_SECRET}"


class TestEndpoints:
    def test_create_plan_posts_json(self):
        body = {}

        def handler(request: httpx.Request) -> httpx.Response:
            body.update(json.loads(request.content))
            return httpx.Response(201, json={"id": "plan_1"})

        plan = _client(handler).create_plan(
            {"period": "monthly", "interval": 1, "item": {"name": "X", "amount": 49900, "currency": "INR"}}
        )
        assert plan["id"] == "plan_1"
        assert body["item"]["amount"] == 49900

    def test_create_subscription_raises_on_api_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json={"error": {"description": "bad plan"}})

        with pytest.raises(RazorpayError) as e:
            _client(handler).create_subscription({"plan_id": "plan_bad"})
        assert e.value.status_code == 400

    def test_list_plans_returns_items(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/v1/plans"
            return httpx.Response(200, json={"items": [{"id": "plan_1"}, {"id": "plan_2"}]})

        plans = _client(handler).list_plans()
        assert len(plans) == 2
