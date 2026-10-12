"""Conformance of the real core services to ``baselith.core.interfaces``.

The service protocols are not ``runtime_checkable``, so conformance is
checked structurally here: every protocol member exists on the concrete
class, is the same kind of callable (sync / coroutine / async generator),
and accepts every call the protocol allows.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

import pytest

from baselith.core.interfaces import (
    AsyncEmbedderProtocol,
    ChatServiceProtocol,
    DocumentRerankerProtocol,
    EmbedderProtocol,
    LLMServiceProtocol,
    RerankerProtocol,
    ScoreRerankerProtocol,
    VectorStoreProtocol,
)
from baselith.core.nlp._remote import RemoteCrossEncoder, RemoteEmbeddingModel
from baselith.core.nlp.lazy import LazyEmbedder, LazyReranker
from baselith.core.services.chat.service import ChatService
from baselith.core.services.llm.service import LLMService
from baselith.core.services.vectorstore.service import VectorStoreService

PROTOCOLS: tuple[type, ...] = (
    AsyncEmbedderProtocol,
    ChatServiceProtocol,
    DocumentRerankerProtocol,
    EmbedderProtocol,
    LLMServiceProtocol,
    ScoreRerankerProtocol,
    VectorStoreProtocol,
)

IMPLEMENTATIONS: list[tuple[type, type]] = [
    (EmbedderProtocol, RemoteEmbeddingModel),
    (AsyncEmbedderProtocol, LazyEmbedder),
    (ScoreRerankerProtocol, RemoteCrossEncoder),
    (ScoreRerankerProtocol, LazyReranker),
    (VectorStoreProtocol, VectorStoreService),
    (LLMServiceProtocol, LLMService),
    (ChatServiceProtocol, ChatService),
]

# (protocol, member) pairs where the concrete class is known not to conform.
# Empty: LLMServiceProtocol.generate_response_stream used to be declared
# `async def ... -> AsyncIterator[str]` (a coroutine returning an iterator),
# which no async generator — LLMService's included — satisfies.
KNOWN_MISMATCHES: dict[tuple[type, str], str] = {}


def _members(protocol: type) -> list[str]:
    return [
        name
        for name, value in vars(protocol).items()
        if not name.startswith("_") and inspect.isfunction(value)
    ]


def _kind(fn: Callable[..., Any]) -> str:
    if inspect.isasyncgenfunction(fn):
        return "async-generator"
    if inspect.iscoroutinefunction(fn):
        return "coroutine"
    return "sync"


def _declared_kind(fn: Callable[..., Any]) -> str:
    """A protocol stub's kind: ``def -> AsyncIterator`` declares a generator."""
    kind = _kind(fn)
    returns = str(inspect.signature(fn).return_annotation)
    if kind == "sync" and "AsyncIterator" in returns:
        return "async-generator"
    return kind


def _protocol_calls(fn: Callable[..., Any]) -> list[tuple[list[Any], dict[str, Any]]]:
    """The minimal and the maximal call the protocol signature permits."""
    required: list[Any] = []
    full: list[Any] = []
    kw_required: dict[str, Any] = {}
    kw_full: dict[str, Any] = {}
    params = list(inspect.signature(fn).parameters.values())[1:]  # drop self
    for p in params:
        if p.kind is p.VAR_KEYWORD:
            kw_full["extra_option"] = object()
        elif p.kind is p.KEYWORD_ONLY:
            kw_full[p.name] = object()
            if p.default is p.empty:
                kw_required[p.name] = object()
        elif p.kind is not p.VAR_POSITIONAL:
            full.append(object())
            if p.default is p.empty:
                required.append(object())
    return [(required, kw_required), (full, kw_full)]


def _cases() -> list[Any]:
    cases = []
    for protocol, impl in IMPLEMENTATIONS:
        for member in _members(protocol):
            reason = KNOWN_MISMATCHES.get((protocol, member))
            marks = [pytest.mark.xfail(strict=True, reason=reason)] if reason else []
            cases.append(
                pytest.param(
                    protocol,
                    impl,
                    member,
                    id=f"{impl.__name__}.{member}",
                    marks=marks,
                )
            )
    return cases


def test_every_protocol_is_exported_and_declares_members() -> None:
    import baselith.core.interfaces as interfaces

    for protocol in PROTOCOLS:
        assert getattr(interfaces, protocol.__name__) is protocol
        assert _members(protocol), protocol.__name__
    assert RerankerProtocol is DocumentRerankerProtocol


@pytest.mark.parametrize(("protocol", "impl", "member"), _cases())
def test_implementation_conforms_to_protocol_member(
    protocol: type, impl: type, member: str
) -> None:
    declared = getattr(protocol, member)
    concrete = getattr(impl, member, None)
    assert callable(concrete), f"{impl.__name__} lacks {member}"
    assert _kind(concrete) == _declared_kind(declared)
    signature = inspect.signature(concrete)
    for args, kwargs in _protocol_calls(declared):
        signature.bind(object(), *args, **kwargs)  # raises TypeError on mismatch


def test_structural_check_rejects_a_non_conforming_class() -> None:
    class SyncLLM:
        def generate_response(self, prompt: str) -> str:
            return prompt

    declared = LLMServiceProtocol.generate_response
    assert _kind(SyncLLM.generate_response) != _declared_kind(declared)
    with pytest.raises(TypeError):
        inspect.signature(SyncLLM.generate_response).bind(
            object(), *_protocol_calls(declared)[1][0]
        )


# An explicit subclass that omits a member inherits the protocol's stub:
# a declaration with no behaviour, which answers ``None`` rather than raising.
_STUB_CALLS: list[tuple[type, str, tuple[Any, ...]]] = [
    (EmbedderProtocol, "encode", (["q"],)),
    (AsyncEmbedderProtocol, "encode", (["q"],)),
    (DocumentRerankerProtocol, "rerank", ("q", [], 1)),
    (ScoreRerankerProtocol, "predict", ([("q", "d")],)),
    (VectorStoreProtocol, "search", ([0.1], 1)),
    (VectorStoreProtocol, "index", ([],)),
    (VectorStoreProtocol, "create_collection", ("c",)),
    (LLMServiceProtocol, "generate_response", ("p",)),
    (LLMServiceProtocol, "generate_response_stream", ("p",)),
    (ChatServiceProtocol, "handle_chat", (None,)),
    (ChatServiceProtocol, "handle_chat_stream", (None,)),
    (ChatServiceProtocol, "handle_chat_async", (None,)),
    (ChatServiceProtocol, "handle_chat_stream_async", (None,)),
]


def test_stub_table_covers_every_protocol_member() -> None:
    declared = {(p, m) for p in PROTOCOLS for m in _members(p)}
    assert {(p, m) for p, m, _ in _STUB_CALLS} == declared


@pytest.mark.parametrize(
    ("protocol", "member", "args"),
    _STUB_CALLS,
    ids=[f"{p.__name__}.{m}" for p, m, _ in _STUB_CALLS],
)
async def test_explicit_subclass_inherits_inert_stub(
    protocol: type, member: str, args: tuple[Any, ...]
) -> None:
    subclass = type(f"Partial{protocol.__name__}", (protocol,), {})
    result = getattr(subclass(), member)(*args)
    if inspect.isawaitable(result):
        result = await result
    assert result is None
