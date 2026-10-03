"""app/mouser.py with the Search API replaced by recorded-shape answers (field names as Mouser's
Search API documents them; to be checked against a real key, docs/ARVUTUS-PLAN.md S8)."""

import json

import httpx
import pytest

from app import mouser, vault

LINK = "https://www.mouser.ee/ProductDetail/Texas-Instruments/NE555P?qs=ZNvtPZ8nWTnUCjzWmrYYhA%3D%3D"
PART = {"MouserPartNumber": "595-NE555P", "ManufacturerPartNumber": "NE555P", "Manufacturer": "Texas Instruments",
        "Description": "Timers & Support Products Single Precision", "Min": "1", "Mult": "1",
        "AvailabilityInStock": "41230", "Availability": "41230 In Stock",
        "ProductDetailUrl": "https://www.mouser.ee/ProductDetail/Texas-Instruments/NE555P?qs=ZNvtPZ8nWTnUCjzWmrYYhA%3D%3D",
        "PriceBreaks": [{"Quantity": 1, "Price": "0,44 €", "Currency": "EUR"},
                        {"Quantity": 10, "Price": "0,342 €", "Currency": "EUR"}]}
OTHER = {**PART, "MouserPartNumber": "595-NE555PE4", "ProductDetailUrl": "https://www.mouser.ee/ProductDetail/x?qs=muu"}


class FakeApi:
    def __init__(self, answer):
        self.answer, self.calls = answer, []

    def __call__(self, request):
        self.calls.append(json.loads(request.content))
        assert request.url.params["apiKey"] == "võti-12345678"
        return httpx.Response(200, json=self.answer(json.loads(request.content)) if callable(self.answer) else self.answer)


@pytest.fixture
def api(conn, monkeypatch):
    vault.set(conn, mouser.KEY, "võti-12345678")
    mouser._minute.clear()

    def use(answer):
        fake = FakeApi(answer)
        transport = httpx.MockTransport(fake)
        real = httpx.Client
        monkeypatch.setattr(mouser.httpx, "Client", lambda **kw: real(transport=transport, **kw))
        return fake
    return use


def test_links_and_numbers():
    assert mouser.is_mouser(LINK) and mouser.is_mouser("595-NE555P") and mouser.is_mouser(" 595-NE555P ")
    assert not mouser.is_mouser("https://www.digikey.ee/x") and not mouser.is_mouser("NE555P")
    assert mouser.search_term(LINK) == "NE555P"
    assert mouser.search_term("https://www.mouser.com/ProductDetail/595-NE555P") == "595-NE555P"


@pytest.mark.parametrize("text, value", [("0,44 €", 0.44), ("€0.44", 0.44), ("$1,234.50", 1234.5),
                                         ("1.234,50 €", 1234.5), ("", None)])
def test_price_value(text, value):
    assert mouser.price_value(text) == value


def test_a_link_picks_the_part_its_qs_names_and_is_cached(conn, api):
    fake = api({"Errors": [], "SearchResults": {"NumberOfResult": 2, "Parts": [OTHER, PART]}})
    got = mouser.parts(conn, [LINK])[LINK]
    assert (got["nr"], got["laos"], got["breaks"], got["valuuta"]) == ("595-NE555P", 41230, [[1, 0.44], [10, 0.342]], "EUR")
    assert fake.calls == [{"SearchByPartRequest": {"mouserPartNumber": "NE555P", "partSearchOptions": "None"}}]
    assert mouser.parts(conn, [LINK]) == {LINK: got} and len(fake.calls) == 1  # cached
    mouser.parts(conn, [LINK], refresh=True)
    assert len(fake.calls) == 1  # fresh enough
    conn.execute("UPDATE mouser_parts SET fetched_at = '2020-01-01T00:00:00Z'")
    mouser.parts(conn, [LINK], refresh=True)
    assert len(fake.calls) == 2


def test_unknown_values_wait_unless_fetched(conn, api):
    fake = api({"Errors": [], "SearchResults": {"Parts": []}})
    assert mouser.parts(conn, ["595-X"], fetch_new=False) == {}
    assert mouser.parts(conn, ["595-X"]) == {"595-X": {"error": "ei leitud"}}
    assert len(fake.calls) == 1


def test_ambiguous_results_ask_for_the_mouser_number(conn, api):
    api({"Errors": [], "SearchResults": {"Parts": [OTHER, {**OTHER, "MouserPartNumber": "595-B"}]}})
    assert mouser.parts(conn, ["https://www.mouser.ee/ProductDetail/x/NE555"])[
        "https://www.mouser.ee/ProductDetail/x/NE555"] == {"error": "mitu vastet: kasuta Mouseri numbrit"}


def test_api_errors_are_masked_and_not_cached(conn, api):
    api({"Errors": [{"Message": "Invalid unique identifier võti-12345678"}], "SearchResults": None})
    got = mouser.parts(conn, ["595-NE555P"])
    assert got == {"595-NE555P": {"error": "Mouser: Invalid unique identifier •••"}}
    assert conn.execute("SELECT COUNT(*) FROM mouser_parts").fetchone()[0] == 0


def test_no_key(conn):
    assert mouser.parts(conn, ["595-NE555P"]) == {"595-NE555P": {"error": "Mouseri võti puudub (Ühendused → Mouser)"}}


def test_calls_per_minute_are_capped(conn, api, monkeypatch):
    monkeypatch.setattr(mouser, "PER_MINUTE", 2)
    api({"Errors": [], "SearchResults": {"Parts": [PART]}})
    got = mouser.parts(conn, ["595-A", "595-B", "595-C"])
    assert got["595-C"]["error"].startswith("Mouseri päringute piir")


def test_the_card_saves_the_key(owner, conn):
    assert owner.post("/api/connections/mouser", json={"mouser_key": "uus-voti-123456"}).status_code in (200, 204)
    assert vault.get(conn, mouser.KEY) == "uus-voti-123456"
