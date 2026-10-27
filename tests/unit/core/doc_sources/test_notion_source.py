"""The Notion document source and its block conversion."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import SecretStr

from baselith.plugins.document_sources.models import DocumentSourceError
from baselith.plugins.document_sources.sources import notion
from baselith.plugins.document_sources.sources.notion import (
    NotionAuthError,
    NotionDocumentSource,
)
from baselith.plugins.document_sources.sources.notion_blocks import (
    block_to_lines,
    normalise_id,
    page_title,
    rich_text,
)

pytestmark = [pytest.mark.unit]

TOKEN = "secret_integration_token"
A = "11111111-1111-1111-1111-111111111111"
B = "22222222-2222-2222-2222-222222222222"
C = "33333333-3333-3333-3333-333333333333"
LONG = "This paragraph is comfortably longer than the stub threshold."


def rt(text: str, href: str | None = None) -> list[dict]:
    return [{"plain_text": text, "href": href}]


def block(
    kind: str, text: str = "", *, id: str = "b", has_children: bool = False, **extra
) -> dict:
    return {
        "id": id,
        "type": kind,
        "has_children": has_children,
        kind: {"rich_text": rt(text), **extra},
    }


def page(page_id: str, title: str, **extra) -> dict:
    return {
        "id": page_id,
        "url": f"https://www.notion.so/{page_id.replace('-', '')}",
        "last_edited_time": "2026-10-01T10:00:00.000Z",
        "properties": {"Name": {"type": "title", "title": rt(title)}},
        **extra,
    }


class FakeNotion:
    """Routes Notion API calls to canned JSON and records them."""

    def __init__(self) -> None:
        self.pages: dict[str, dict] = {}
        self.children: dict[str, list[list[dict]]] = {}  # block id -> pages of results
        self.search_pages: list[list[dict]] = []
        self.requests: list[httpx.Request] = []
        self.failures: dict[str, list[int]] = {}  # path -> statuses to answer first

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path.removeprefix("/v1")
        queued = self.failures.get(path)
        if queued:
            status = queued.pop(0)
            return httpx.Response(
                status,
                json={"message": f"BODY-SECRET-{status}"},
                headers={"retry-after": "0"},
            )
        if path == "/search":
            body = json.loads(request.content)
            index = int(body.get("start_cursor", "0"))
            results = self.search_pages[index]
            more = index + 1 < len(self.search_pages)
            return httpx.Response(
                200,
                json={
                    "results": results,
                    "has_more": more,
                    "next_cursor": str(index + 1) if more else None,
                },
            )
        if path.startswith("/pages/"):
            found = self.pages.get(path.rsplit("/", 1)[1])
            return (
                httpx.Response(200, json=found)
                if found
                else httpx.Response(404, json={"message": "nope"})
            )
        if path.startswith("/blocks/") and path.endswith("/children"):
            block_id = path.split("/")[2]
            pages = self.children.get(block_id, [[]])
            index = int(request.url.params.get("start_cursor", "0"))
            more = index + 1 < len(pages)
            return httpx.Response(
                200,
                json={
                    "results": pages[index],
                    "has_more": more,
                    "next_cursor": str(index + 1) if more else None,
                },
            )
        return httpx.Response(404, json={})

    def source(self, roots=(), **kwargs) -> NotionDocumentSource:
        client = httpx.AsyncClient(transport=httpx.MockTransport(self.handler))
        kwargs.setdefault("min_interval", 0)
        return NotionDocumentSource(SecretStr(TOKEN), roots, client=client, **kwargs)


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch):
    monkeypatch.setattr(notion.asyncio, "sleep", AsyncMock())


async def collect(source: NotionDocumentSource) -> list:
    return [item async for item in source.iter_items()]


class TestBlocks:
    @pytest.mark.parametrize(
        ("blk", "depth", "expected"),
        [
            (block("heading_1", "Title"), 0, ["# Title"]),
            (block("heading_3", "Deep"), 0, ["### Deep"]),
            (block("paragraph", "Plain"), 0, ["Plain"]),
            (block("bulleted_list_item", "one"), 1, ["  - one"]),
            (block("numbered_list_item", "first"), 0, ["1. first"]),
            (block("to_do", "ship", checked=True), 0, ["- [x] ship"]),
            (block("to_do", "write", checked=False), 0, ["- [ ] write"]),
            (block("quote", "a\nb"), 0, ["> a", "> b"]),
            (block("callout", "note"), 0, ["> note"]),
            (
                block("code", "print(1)", language="python"),
                0,
                ["```python", "print(1)", "```"],
            ),
            ({"type": "divider", "divider": {}}, 0, ["---"]),
            (
                {"type": "equation", "equation": {"expression": "E=mc^2"}},
                0,
                ["$$ E=mc^2 $$"],
            ),
            (
                {"type": "bookmark", "bookmark": {"url": "https://a.b/c"}},
                0,
                ["https://a.b/c"],
            ),
            ({"type": "bookmark", "bookmark": {"url": "javascript:alert(1)"}}, 0, []),
            (
                {"type": "table_row", "table_row": {"cells": [rt("a"), rt("b")]}},
                0,
                ["| a | b |"],
            ),
            (
                {"type": "image", "image": {"external": {"url": "https://x/y.png"}}},
                0,
                [],
            ),
            ({"type": "synced_block", "synced_block": {}}, 0, []),
            (
                {"type": "from_the_future", "from_the_future": {"rich_text": rt("x")}},
                0,
                [],
            ),
            (block("paragraph", ""), 0, []),
            ({"type": "paragraph"}, 0, []),
        ],
    )
    def test_block_conversion(self, blk, depth, expected):
        assert block_to_lines(blk, depth) == expected

    def test_rich_text_keeps_http_links_only(self):
        items = [
            {"plain_text": "see ", "href": None},
            {"plain_text": "docs", "href": "https://a.b"},
            {"plain_text": "bad", "href": "javascript:x"},
            "not-a-dict",
        ]
        assert rich_text(items) == "see [docs](https://a.b)bad"
        assert rich_text(None) == ""

    def test_page_title_comes_from_the_title_property(self):
        assert page_title(page(A, "Roadmap")) == "Roadmap"
        assert page_title({"properties": {"x": {"type": "rich_text"}}}) == ""
        assert page_title({}) == ""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("11111111111111111111111111111111", A),
            (A.upper(), A),
            (f"  {A}  ", A),
            ("../../etc/passwd", None),
            ("1111", None),
            (f"{A}/children", None),
            (None, None),
            (123, None),
        ],
    )
    def test_normalise_id_accepts_only_uuids(self, raw, expected):
        assert normalise_id(raw) == expected


class TestSearchMode:
    async def test_indexes_every_shared_page_across_search_pages(self):
        fake = FakeNotion()
        fake.search_pages = [[page(A, "One")], [page(B, "Two")]]
        fake.children[A] = [[block("paragraph", LONG, id="x")]]
        fake.children[B] = [
            [block("heading_2", "Section", id="y"), block("paragraph", LONG, id="z")]
        ]
        items = await collect(fake.source())
        assert [i.metadata["title"] for i in items] == ["One", "Two"]
        first = items[0]
        assert first.uid == f"notion:{A}" and first.content == f"# One\n\n{LONG}"
        assert first.metadata["origin"] == "notion" and first.metadata["page_id"] == A
        assert first.metadata["last_edited"] == "2026-10-01T10:00:00.000Z"
        assert first.fingerprint.startswith(f"notion:{A}:")
        assert "## Section" in items[1].content

    async def test_archived_and_stub_pages_are_skipped(self):
        fake = FakeNotion()
        fake.search_pages = [
            [page(A, "Gone", archived=True), page(B, "Stub"), page(C, "Real")]
        ]
        fake.children[B] = [[block("paragraph", "hi")]]
        fake.children[C] = [[block("paragraph", LONG)]]
        items = await collect(fake.source())
        assert [i.metadata["title"] for i in items] == ["Real"]

    async def test_the_fingerprint_follows_the_content(self):
        fake = FakeNotion()
        fake.search_pages = [[page(A, "One")]]
        fake.children[A] = [[block("paragraph", LONG)]]
        before = (await collect(fake.source()))[0].fingerprint
        fake.children[A] = [[block("paragraph", LONG + " Edited.")]]
        assert (await collect(fake.source()))[0].fingerprint != before

    async def test_max_pages_stops_the_run(self):
        fake = FakeNotion()
        fake.search_pages = [[page(A, "One"), page(B, "Two"), page(C, "Three")]]
        for pid in (A, B, C):
            fake.children[pid] = [[block("paragraph", LONG)]]
        assert len(await collect(fake.source(max_pages=2))) == 2

    async def test_child_pages_are_not_followed_when_searching(self):
        fake = FakeNotion()
        fake.search_pages = [[page(A, "One")]]
        fake.children[A] = [
            [
                block("paragraph", LONG),
                {"id": B, "type": "child_page", "child_page": {"title": "Kid"}},
            ]
        ]
        items = await collect(fake.source())
        assert len(items) == 1
        assert not any(r.url.path.endswith(f"/pages/{B}") for r in fake.requests)


class TestRootMode:
    async def test_roots_and_their_child_pages(self):
        fake = FakeNotion()
        fake.pages = {A: page(A, "Root"), B: page(B, "Child")}
        fake.children[A] = [
            [
                block("paragraph", LONG, id="x"),
                {"id": B, "type": "child_page", "child_page": {"title": "Child"}},
            ]
        ]
        fake.children[B] = [[block("paragraph", LONG + " child", id="y")]]
        items = await collect(fake.source([A.replace("-", "")]))
        assert [i.metadata["title"] for i in items] == ["Root", "Child"]

    async def test_a_page_listed_twice_is_read_once(self):
        fake = FakeNotion()
        fake.pages = {A: page(A, "Root")}
        fake.children[A] = [
            [
                block("paragraph", LONG),
                {"id": A, "type": "child_page", "child_page": {}},
            ]
        ]
        assert len(await collect(fake.source([A, A]))) == 1

    async def test_nested_children_are_indented(self):
        fake = FakeNotion()
        fake.pages = {A: page(A, "Root")}
        fake.children[A] = [
            [
                block(
                    "bulleted_list_item",
                    "outer is long enough to count",
                    id=B,
                    has_children=True,
                )
            ]
        ]
        fake.children[B] = [[block("bulleted_list_item", "inner", id=C)]]
        (item,) = await collect(fake.source([A]))
        assert "- outer is long enough to count\n  - inner" in item.content

    async def test_a_page_that_is_not_shared_is_skipped_not_fatal(self):
        fake = FakeNotion()
        fake.pages = {B: page(B, "Visible")}
        fake.children[B] = [[block("paragraph", LONG)]]
        items = await collect(fake.source([A, B]))
        assert [i.metadata["title"] for i in items] == ["Visible"]

    async def test_a_rejected_token_stops_everything(self):
        fake = FakeNotion()
        fake.failures["/pages/" + A] = [401]
        with pytest.raises(NotionAuthError):
            await collect(fake.source([A]))

    async def test_invalid_root_ids_are_refused_up_front(self):
        with pytest.raises(DocumentSourceError, match="invalid page id"):
            NotionDocumentSource(SecretStr(TOKEN), ["../../users/me"])

    async def test_ids_that_come_back_from_the_api_are_validated_before_use(self):
        fake = FakeNotion()
        fake.pages = {A: page(A, "Root")}
        fake.children[A] = [
            [
                block("paragraph", LONG),
                {"id": "../../../admin", "type": "child_page", "child_page": {}},
            ]
        ]
        assert len(await collect(fake.source([A]))) == 1
        assert all("admin" not in r.url.path for r in fake.requests)


class TestHttpBehaviour:
    async def test_every_request_carries_the_token_and_version(self):
        fake = FakeNotion()
        fake.search_pages = [[page(A, "One")]]
        fake.children[A] = [[block("paragraph", LONG)]]
        await collect(fake.source())
        for request in fake.requests:
            assert request.headers["authorization"] == f"Bearer {TOKEN}"
            assert request.headers["notion-version"] == "2022-06-28"
            assert request.url.host == "api.notion.com"

    async def test_rate_limit_and_server_errors_are_retried(self):
        fake = FakeNotion()
        fake.search_pages = [[page(A, "One")]]
        fake.children[A] = [[block("paragraph", LONG)]]
        fake.failures["/search"] = [429, 503]
        assert len(await collect(fake.source())) == 1

    async def test_persistent_failure_reports_status_only_never_the_body(self):
        fake = FakeNotion()
        fake.failures["/search"] = [500, 500, 500]
        with pytest.raises(DocumentSourceError) as exc:
            await collect(fake.source())
        assert "500" in str(exc.value) and "BODY-SECRET" not in str(exc.value)
        assert TOKEN not in str(exc.value)

    async def test_client_errors_are_not_retried(self):
        fake = FakeNotion()
        fake.failures["/search"] = [400, 400]
        with pytest.raises(DocumentSourceError, match="400"):
            await collect(fake.source())
        assert len([r for r in fake.requests if r.url.path.endswith("/search")]) == 1

    async def test_a_network_error_is_a_source_error_without_the_token(self):
        def boom(request):
            raise httpx.ConnectError("refused", request=request)

        client = httpx.AsyncClient(transport=httpx.MockTransport(boom))
        source = NotionDocumentSource(SecretStr(TOKEN), client=client, min_interval=0)
        with pytest.raises(DocumentSourceError, match="ConnectError") as exc:
            await collect(source)
        assert TOKEN not in str(exc.value)

    async def test_a_huge_page_is_truncated(self, monkeypatch):
        monkeypatch.setattr(notion, "MAX_BLOCKS_PER_PAGE", 3)
        fake = FakeNotion()
        fake.pages = {A: page(A, "Big")}
        fake.children[A] = [
            [block("paragraph", f"{LONG} {i}", id=f"x{i}") for i in range(10)]
        ]
        (item,) = await collect(fake.source([A]))
        assert item.content.count(LONG) == 3 and "[page truncated]" in item.content

    async def test_close_leaves_an_injected_client_to_its_owner(self):
        fake = FakeNotion()
        source = fake.source()
        await source.close()
        assert not source._client.is_closed


class TestFactory:
    def test_inert_without_a_token(self, monkeypatch):
        from baselith.core.config.processing import ProcessingConfig

        monkeypatch.setattr(notion, "get_processing_config", lambda: ProcessingConfig())
        assert notion._create_notion_source() is None

    def test_built_from_config(self, monkeypatch):
        from baselith.core.config.processing import ProcessingConfig

        config = ProcessingConfig(
            NOTION_API_TOKEN=TOKEN,
            NOTION_ROOT_PAGE_IDS=f"{A}, {B}",
            NOTION_MAX_PAGES="7",
        )
        monkeypatch.setattr(notion, "get_processing_config", lambda: config)
        source = notion._create_notion_source()
        assert isinstance(source, NotionDocumentSource)
        assert source._roots == [A, B] and source._max_pages == 7

    def test_a_bad_root_id_disables_the_source_instead_of_crashing_indexing(
        self, monkeypatch
    ):
        from baselith.core.config.processing import ProcessingConfig

        config = ProcessingConfig(
            NOTION_API_TOKEN=TOKEN, NOTION_ROOT_PAGE_IDS="not-an-id"
        )
        monkeypatch.setattr(notion, "get_processing_config", lambda: config)
        assert notion._create_notion_source() is None

    def test_the_token_never_appears_in_the_config_repr(self):
        from baselith.core.config.processing import ProcessingConfig

        assert TOKEN not in repr(ProcessingConfig(NOTION_API_TOKEN=TOKEN))

    def test_registered_with_the_source_registry(self):
        from baselith.plugins.document_sources.sources.registry import (
            registered_sources,
        )

        assert "notion" in registered_sources()
