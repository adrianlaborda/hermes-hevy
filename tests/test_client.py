import json

import httpx
import pytest

from conftest import API_KEY, Seq, paged
from hermes_hevy.client import (
    KEY_PAGE, KEY_SETTINGS, ApiError, AuthError, ConflictError, HevyClient, HevyError, LimitError, NotConfiguredError,
    NotFoundError, RateLimitedError, ValidationError, key_rejected_message, redact)

WORKOUTS = "/v1/workouts"
ROUTINE = "/v1/routines/r1"


@pytest.fixture
def make_client(fake, clock, sleeps):
    clients = []

    def factory(key=lambda: API_KEY, **kwargs):
        client = HevyClient(key, transport=httpx.MockTransport(fake.handler), clock=clock,
                            sleep=sleeps.append, **kwargs)
        clients.append(client)
        return client

    yield factory
    for client in clients:
        client.close()


# -- headers and credentials ---------------------------------------------------------------------

def test_api_key_header_is_sent_on_every_request(fake, make_client):
    fake.routes[WORKOUTS] = {"page": 1, "page_count": 1, "workouts": []}
    fake.routes[("POST", WORKOUTS)] = httpx.Response(201, json={"workout": []})
    fake.routes[("PUT", ROUTINE)] = {"routine": {}}
    client = make_client()
    client.get(WORKOUTS)
    client.post(WORKOUTS, {"workout": {"title": "Leg day"}})
    client.put(ROUTINE, {"routine": {}})

    assert len(fake.requests) == 3
    for request in fake.requests:
        assert request.headers["api-key"] == API_KEY
        assert request.headers["Accept"] == "application/json"
        assert "authorization" not in request.headers
    assert json.loads(fake.calls(WORKOUTS, "POST")[0].content) == {"workout": {"title": "Leg day"}}


def test_key_provider_is_read_per_request(fake, make_client):
    fake.routes[WORKOUTS] = {"page": 1, "page_count": 1, "workouts": []}
    current = {"key": "key-a-0123456789"}
    client = make_client(key=lambda: current["key"], cache_ttl=0)
    client.get(WORKOUTS)
    current["key"] = "key-b-0123456789"
    client.get(WORKOUTS)
    assert [r.headers["api-key"] for r in fake.requests] == ["key-a-0123456789", "key-b-0123456789"]


@pytest.mark.parametrize("key", [lambda: None, lambda: "   "])
def test_missing_key_is_not_configured_and_sends_nothing(fake, make_client, key):
    client = make_client(key=key)
    with pytest.raises(NotConfiguredError) as err:
        client.get(WORKOUTS)
    with pytest.raises(NotConfiguredError):
        client.post(WORKOUTS, {"workout": {}})
    with pytest.raises(NotConfiguredError):
        client.fingerprint()
    message = str(err.value)
    assert KEY_PAGE in message and KEY_SETTINGS in message
    assert fake.requests == []


def test_fingerprint_is_stable_per_key_and_hides_the_key(make_client):
    fingerprint = make_client().fingerprint()
    assert len(fingerprint) == 64
    assert fingerprint == make_client().fingerprint()
    assert fingerprint != make_client(key=lambda: "another-key-0123456789").fingerprint()
    assert API_KEY not in fingerprint


# -- error classes and messages ------------------------------------------------------------------

@pytest.mark.parametrize("status, error_class", [
    (400, ValidationError),
    (401, AuthError),
    (403, LimitError),
    (404, NotFoundError),
    (409, ConflictError),
    (429, RateLimitedError),
    (500, ApiError),
    (503, ApiError),
])
def test_each_status_maps_to_its_error_class(fake, make_client, status, error_class):
    fake.routes[WORKOUTS] = httpx.Response(status, json={"error": "nope"})
    with pytest.raises(error_class) as err:
        make_client().get(WORKOUTS, ttl=0, retries=0)
    assert isinstance(err.value, HevyError)
    assert err.value.status == status


def test_401_gives_the_key_rejected_message(fake, make_client):
    fake.routes[WORKOUTS] = httpx.Response(401, json={"error": "unauthorized"})
    with pytest.raises(AuthError) as err:
        make_client().get(WORKOUTS, ttl=0)
    assert str(err.value) == key_rejected_message(401)
    assert KEY_PAGE in str(err.value) and KEY_SETTINGS in str(err.value)
    assert "never" in str(err.value).lower()


def test_hevy_message_is_included_in_the_error(fake, make_client):
    fake.routes[("PUT", ROUTINE)] = httpx.Response(403, json={
        "error": "routine-limit-exceeded",
        "message": "Free accounts can have up to 4 routines. Upgrade to Hevy Pro for more.",
    })
    with pytest.raises(LimitError) as err:
        make_client().put(ROUTINE, {"routine": {}})
    assert "Upgrade to Hevy Pro for more." in str(err.value)

    fake.routes[("POST", WORKOUTS)] = httpx.Response(400, json={"error": "title is required"})
    with pytest.raises(ValidationError, match="title is required"):
        make_client().post(WORKOUTS, {"workout": {}})


def test_api_key_never_appears_in_an_error_message(fake, make_client):
    for status in (400, 403, 404, 409, 429, 500):
        fake.routes[WORKOUTS] = httpx.Response(status, json={"error": "x", "message": f"echo {API_KEY}"})
        with pytest.raises(HevyError) as err:
            make_client().get(WORKOUTS, ttl=0, retries=0)
        assert API_KEY not in str(err.value)
        if status != 429:  # the rate-limit message carries no Hevy text, so nothing to redact
            assert "[redacted]" in str(err.value)


def test_api_key_is_not_in_network_or_non_json_errors(fake, make_client):
    def boom(request):
        raise httpx.ConnectError(f"cannot reach with {API_KEY}", request=request)
    fake.routes[WORKOUTS] = boom
    with pytest.raises(ApiError) as err:
        make_client(retries=0).get(WORKOUTS, ttl=0)
    assert API_KEY not in str(err.value)

    fake.routes[WORKOUTS] = httpx.Response(200, text=f"<html>{API_KEY}</html>")
    with pytest.raises(ApiError, match="not JSON") as err:
        make_client().get(WORKOUTS, ttl=0)
    assert API_KEY not in str(err.value)


def test_error_detail_is_trimmed(fake, make_client):
    fake.routes[WORKOUTS] = httpx.Response(400, json={"error": "x" * 500})
    with pytest.raises(ValidationError) as err:
        make_client().get(WORKOUTS, ttl=0)
    assert "x" * 200 in str(err.value) and "x" * 201 not in str(err.value)


def test_redact_removes_secrets_and_ignores_empty_ones():
    assert redact("a key-123 b key-123", ("key-123", "")) == "a [redacted] b [redacted]"


def test_no_content_and_empty_bodies_return_none(fake, make_client):
    fake.routes[WORKOUTS] = httpx.Response(204)
    fake.routes[ROUTINE] = httpx.Response(200, content=b"")
    client = make_client()
    assert client.get(WORKOUTS) is None
    assert client.get(ROUTINE) is None


# -- retries and backoff -------------------------------------------------------------------------

def test_get_is_retried_on_503_then_succeeds(fake, make_client, sleeps):
    fake.routes[WORKOUTS] = Seq([httpx.Response(503), {"page": 1, "page_count": 1, "workouts": []}])
    assert make_client().get(WORKOUTS) == {"page": 1, "page_count": 1, "workouts": []}
    assert len(fake.calls(WORKOUTS)) == 2
    assert sleeps == [0.5]


def test_get_gives_up_after_the_retry_budget(fake, make_client, sleeps):
    fake.routes[WORKOUTS] = httpx.Response(502)
    with pytest.raises(ApiError, match="server error"):
        make_client(retries=2).get(WORKOUTS, ttl=0)
    assert len(fake.calls(WORKOUTS)) == 3
    assert sleeps == [0.5, 1.0]


def test_post_is_not_retried_on_503(fake, make_client, sleeps):
    fake.routes[("POST", WORKOUTS)] = httpx.Response(503, json={"error": "try later"})
    with pytest.raises(ApiError):
        make_client().post(WORKOUTS, {"workout": {}})
    assert len(fake.calls(WORKOUTS, "POST")) == 1
    assert sleeps == []


def test_post_is_retried_on_429(fake, make_client, sleeps):
    fake.routes[("POST", WORKOUTS)] = Seq([httpx.Response(429), httpx.Response(201, json={"workout": []})])
    assert make_client().post(WORKOUTS, {"workout": {}}) == {"workout": []}
    assert len(fake.calls(WORKOUTS, "POST")) == 2
    assert sleeps == [0.5]


def test_put_is_retried_on_503(fake, make_client, sleeps):
    fake.routes[("PUT", ROUTINE)] = Seq([httpx.Response(503), {"routine": {"id": "r1"}}])
    assert make_client().put(ROUTINE, {"routine": {}}) == {"routine": {"id": "r1"}}
    assert len(fake.calls(ROUTINE, "PUT")) == 2


def test_connection_errors_are_retried_for_get_and_put_only(fake, make_client, sleeps):
    def boom(request):
        raise httpx.ConnectError("down", request=request)
    fake.routes[WORKOUTS] = boom
    fake.routes[("POST", WORKOUTS)] = boom
    with pytest.raises(ApiError, match="Could not reach Hevy"):
        make_client(retries=1).get(WORKOUTS, ttl=0)
    assert len(fake.calls(WORKOUTS)) == 2 and sleeps == [0.5]

    with pytest.raises(ApiError, match="Could not reach Hevy"):
        make_client(retries=1).post(WORKOUTS, {"workout": {}})
    assert len(fake.calls(WORKOUTS, "POST")) == 1


def test_retry_after_is_honoured(fake, make_client, sleeps):
    fake.routes[WORKOUTS] = Seq([httpx.Response(429, headers={"Retry-After": "3"}), {"page": 1, "page_count": 0}])
    make_client().get(WORKOUTS, ttl=0)
    assert sleeps == [3.0]


def test_retry_after_is_capped_at_ten_seconds(fake, make_client, sleeps):
    fake.routes[WORKOUTS] = Seq([httpx.Response(503, headers={"Retry-After": "999"}), {"page": 1, "page_count": 0}])
    make_client().get(WORKOUTS, ttl=0)
    assert sleeps == [10.0]


def test_unparseable_retry_after_falls_back_to_backoff(fake, make_client, sleeps):
    fake.routes[WORKOUTS] = Seq([httpx.Response(503, headers={"Retry-After": "soon"}), {"page": 1, "page_count": 0}])
    make_client().get(WORKOUTS, ttl=0)
    assert sleeps == [0.5]


# -- cache ---------------------------------------------------------------------------------------

def test_get_is_cached_within_ttl(fake, make_client, clock):
    fake.routes[WORKOUTS] = Seq([{"page": 1, "page_count": 0, "workouts": []}, {"page": 1, "page_count": 0, "workouts": [{"id": "x"}]}])
    client = make_client(cache_ttl=60)
    first = client.get(WORKOUTS, {"page": 1})
    assert client.get(WORKOUTS, {"page": 1}) == first
    assert len(fake.calls(WORKOUTS)) == 1

    clock.now += 61
    assert client.get(WORKOUTS, {"page": 1}) != first
    assert len(fake.calls(WORKOUTS)) == 2


def test_cache_keys_include_sorted_params(fake, make_client):
    fake.routes[WORKOUTS] = {"page": 1, "page_count": 0, "workouts": []}
    client = make_client()
    client.get(WORKOUTS, {"page": 1, "pageSize": 10})
    client.get(WORKOUTS, {"pageSize": 10, "page": 1})
    client.get(WORKOUTS, {"page": 2, "pageSize": 10})
    assert len(fake.calls(WORKOUTS)) == 2


def test_ttl_zero_bypasses_the_cache(fake, make_client):
    fake.routes[WORKOUTS] = {"page": 1, "page_count": 0, "workouts": []}
    client = make_client(cache_ttl=60)
    client.get(WORKOUTS, ttl=0)
    client.get(WORKOUTS, ttl=0)
    assert len(fake.calls(WORKOUTS)) == 2


def test_cache_disabled_by_default_ttl_zero(fake, make_client):
    fake.routes[WORKOUTS] = {"page": 1, "page_count": 0, "workouts": []}
    client = make_client(cache_ttl=0)
    client.get(WORKOUTS)
    client.get(WORKOUTS)
    assert len(fake.calls(WORKOUTS)) == 2


def test_post_and_put_invalidate_the_cache(fake, make_client):
    fake.routes[WORKOUTS] = {"page": 1, "page_count": 0, "workouts": []}
    fake.routes[("POST", WORKOUTS)] = httpx.Response(201, json={"workout": []})
    fake.routes[("PUT", ROUTINE)] = {"routine": {}}
    client = make_client()

    client.get(WORKOUTS)
    client.post(WORKOUTS, {"workout": {}})
    client.get(WORKOUTS)
    assert len(fake.calls(WORKOUTS)) == 2

    client.get(WORKOUTS)
    client.put(ROUTINE, {"routine": {}})
    client.get(WORKOUTS)
    assert len(fake.calls(WORKOUTS)) == 3


def test_write_invalidates_the_cache_even_when_it_fails(fake, make_client):
    fake.routes[WORKOUTS] = {"page": 1, "page_count": 0, "workouts": []}
    fake.routes[("POST", WORKOUTS)] = httpx.Response(400, json={"error": "bad"})
    client = make_client()
    client.get(WORKOUTS)
    with pytest.raises(ValidationError):
        client.post(WORKOUTS, {"workout": {}})
    client.get(WORKOUTS)
    assert len(fake.calls(WORKOUTS)) == 2


def test_separate_keys_do_not_share_cache(fake, make_client):
    fake.routes[WORKOUTS] = {"page": 1, "page_count": 0, "workouts": []}
    keys = iter(["key-a-0123456789", "key-b-0123456789"])
    current = {"key": next(keys)}
    client = make_client(key=lambda: current["key"])
    client.get(WORKOUTS)
    current["key"] = next(keys)
    client.get(WORKOUTS)
    assert len(fake.calls(WORKOUTS)) == 2


# -- pagination ----------------------------------------------------------------------------------

def test_paginate_walks_every_page_until_page_count(fake, make_client):
    items = [{"id": str(i)} for i in range(25)]
    fake.routes[WORKOUTS] = paged("workouts", items)
    rows = list(make_client().paginate(WORKOUTS, "workouts", page_size=10))
    assert [r["id"] for r in rows] == [str(i) for i in range(25)]
    assert len(fake.calls(WORKOUTS)) == 3


def test_paginate_stops_on_an_empty_page(fake, make_client):
    fake.routes[WORKOUTS] = {"page": 1, "page_count": 5, "workouts": []}
    assert list(make_client().paginate(WORKOUTS, "workouts", page_size=10)) == []
    assert len(fake.calls(WORKOUTS)) == 1


def test_paginate_stops_after_page_one_when_page_count_is_zero(fake, make_client):
    fake.routes[WORKOUTS] = {"page": 1, "page_count": 0, "workouts": [{"id": "a"}]}
    assert [r["id"] for r in make_client().paginate(WORKOUTS, "workouts", page_size=10)] == ["a"]
    assert len(fake.calls(WORKOUTS)) == 1


def test_paginate_stops_on_404_past_page_one(fake, make_client):
    def route(request):
        page = int(request.url.params["page"])
        if page > 2:
            return httpx.Response(404, json={"error": "Page not found"})
        return {"page": page, "page_count": 5, "workouts": [{"id": str(page)}]}
    fake.routes[WORKOUTS] = route
    rows = list(make_client().paginate(WORKOUTS, "workouts", page_size=10))
    assert [r["id"] for r in rows] == ["1", "2"]
    assert len(fake.calls(WORKOUTS)) == 3


def test_paginate_raises_404_on_page_one(fake, make_client):
    fake.routes[WORKOUTS] = httpx.Response(404, json={"error": "Not found"})
    with pytest.raises(NotFoundError):
        list(make_client().paginate(WORKOUTS, "workouts", page_size=10))


def test_paginate_honours_max_pages_and_sends_params(fake, make_client):
    fake.routes[WORKOUTS] = paged("workouts", [{"id": str(i)} for i in range(50)])
    rows = list(make_client().paginate(WORKOUTS, "workouts", page_size=10, params={"since": "2026-11-01T00:00:00Z"},
                                       max_pages=2))
    assert len(rows) == 20
    query = [dict(r.url.params) for r in fake.calls(WORKOUTS)]
    assert query == [
        {"since": "2026-11-01T00:00:00Z", "page": "1", "pageSize": "10"},
        {"since": "2026-11-01T00:00:00Z", "page": "2", "pageSize": "10"},
    ]
