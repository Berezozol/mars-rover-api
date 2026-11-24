"""The developer page and llms.txt about API keys (stage 20B, AC-25).

`GET /developers` has an "API keys" section with the text about data (what is stored, for how
long, who sends the mails, how to delete it, links to /keys/manage and /keys/delete) and no
personal address of the owner; `GET /llms.txt` says how to get a key.
"""

import re
from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI

pytestmark = [pytest.mark.api, pytest.mark.usefixtures("migrated_db")]

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+")
HEADING_RE = re.compile(r"<h[1-4][^>]*>\s*API keys\s*</h[1-4]>", re.IGNORECASE)


@pytest.fixture
async def site(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://api.example.test") as c:
        yield c


@pytest.fixture
async def page(site: httpx.AsyncClient) -> str:
    response = await site.get("/developers")
    assert response.status_code == 200, response.text
    return response.text


def section(page: str) -> str:
    """The text of the "API keys" section: from its heading to the next heading of the same
    or a higher level (or the end of the page)."""
    match = HEADING_RE.search(page)
    assert match, 'no "API keys" heading on /developers'
    level = int(page[match.start() + 2])
    rest = page[match.end() :]
    stop = re.search(rf"<h[1-{level}][ >]", rest)
    return rest[: stop.start()] if stop else rest


def test_ac25_developers_has_an_api_keys_section(page: str) -> None:
    assert HEADING_RE.search(page)


def test_ac25_the_section_says_how_to_get_a_key(page: str) -> None:
    text = section(page)

    assert "/keys" in text
    assert "keys/requests" in text
    assert "X-Api-Key" in text and "api_key" in text


def test_ac25_the_section_links_to_the_manage_and_delete_pages(page: str) -> None:
    text = section(page)

    assert "/keys/manage" in text
    assert "/keys/delete" in text


@pytest.mark.parametrize(
    "fragment",
    ["email", "hash", "90 days", "within a day", "delete"],
    ids=["email", "hash", "counters-90-days", "pending-within-a-day", "deletion"],
)
def test_ac25_the_data_text_covers_what_is_stored_and_for_how_long(
    page: str, fragment: str
) -> None:
    assert fragment.lower() in re.sub(r"<[^>]+>", " ", section(page)).lower()


def test_ac25_the_data_text_names_who_sends_the_mails(page: str) -> None:
    text = re.sub(r"<[^>]+>", " ", section(page)).lower()

    assert "mail" in text
    assert "sent" in text or "send" in text


def test_ac25_the_data_text_says_the_address_is_not_shared_or_used_for_other_mail(
    page: str,
) -> None:
    text = re.sub(r"<[^>]+>", " ", section(page)).lower()

    phrases = ("not share", "never share", "do not give", "not give your", "nobody", "no one")
    assert any(phrase in text for phrase in phrases), text


def test_ac25_the_page_has_no_personal_mailbox(page: str) -> None:
    """Replying to the mail is enough; no private address is published."""
    found = {m.group(0) for m in EMAIL_RE.finditer(page)}
    private = {a for a in found if a.lower().endswith(("@gmail.com", "@googlemail.com"))}

    assert private == set(), private


async def test_ac25_llms_txt_says_how_to_get_a_key(site: httpx.AsyncClient) -> None:
    response = await site.get("/llms.txt")

    assert response.status_code == 200
    body = response.text
    assert "/keys" in body
    assert "key" in body.lower()
    assert not {
        m.group(0) for m in EMAIL_RE.finditer(body) if m.group(0).lower().endswith("@gmail.com")
    }
