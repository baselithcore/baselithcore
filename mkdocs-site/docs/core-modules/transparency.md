---
title: AI Transparency
description: Article 50 disclosure and content-provenance primitives
---

The `core/transparency` subsystem provides the two technical means **EU AI Act Article 50**
requires of AI systems that interact with people or emit synthetic content. It is
**opt-in** (`TRANSPARENCY_ENABLED`, default off) and **additive** — nothing in the
request path changes until a call site attaches a disclosure notice or a
provenance header.

| Article 50 obligation | Primitive |
| --------------------- | --------- |
| §1 — inform users they interact with an AI | `DisclosureService` → `DisclosureNotice` |
| §2 / §4 — mark AI-generated / -modified content machine-readably | `ProvenanceTagger` → `ProvenanceTag` |

Both are unified behind `TransparencyService` / `get_transparency_service()`.

## Configuration

`core/config/transparency.py` (`get_transparency_config()`):

| Env var | Default | Purpose |
| ------- | ------- | ------- |
| `TRANSPARENCY_ENABLED` | `false` | Master switch for the subsystem |
| `TRANSPARENCY_DISCLOSURE_TEXT` | built-in EN text | Disclosure shown to users (§1) |
| `TRANSPARENCY_PROVIDER_NAME` | `None` | Provider name carried in the notice |
| `TRANSPARENCY_CLAIM_GENERATOR` | `BaselithCore` | Producing-system id in provenance tags (C2PA `claim_generator`) |
| `TRANSPARENCY_SIGNING_SECRET` | `None` | HMAC secret (`SecretStr`); when set, tags are signed and verifiable |

## Disclosure (Art 50(1))

```python
from core.transparency import get_transparency_service

svc = get_transparency_service()
if svc.should_disclose(obvious=False):       # §1 exemption: obvious=True suppresses
    notice = svc.disclosure_notice()
    response["ai_disclosure"] = notice.to_dict()
```

`should_disclose(obvious=True)` models the Art 50(1) exemption for contexts where
AI involvement is already obvious to a reasonably well-informed person.

## Content provenance (Art 50(2)/(4))

`mark_content()` returns a machine-readable `ProvenanceTag` carrying the content
class, modality, producing model, a SHA-256 of the exact bytes, and — when a
signing secret is configured — an HMAC-SHA256 signature. Marking is itself
audited (`AUDIT | TRANSPARENCY | content marked …`).

```python
from core.transparency import ContentClass, Modality, get_transparency_service

svc = get_transparency_service()
tag = svc.mark_content(answer_text, content_class=ContentClass.AI_GENERATED,
                       modality=Modality.TEXT, model="claude-opus-4-8")

header_name, header_value = svc.provenance_header(tag)   # X-Baselith-AI-Provenance
response.headers[header_name] = header_value
```

Verification re-binds a tag to content and checks the signature. Under a signing
policy an **unsigned** tag is rejected:

```python
assert svc.verify_content(tag, answer_text) is True
```

### C2PA alignment

`ProvenanceTag.c2pa_assertion()` renders the tag as a
[C2PA](https://c2pa.org/) / Content-Credentials assertion bundle (`c2pa.actions`
with `c2pa.created` / `c2pa.edited` plus a `c2pa.hash.data` SHA-256 assertion), so
a deployer can promote a tag into a full C2PA manifest for media without
reshaping the model. Full cryptographic manifest embedding (JUMBF/COSE) and
decode-time statistical watermarking (e.g. SynthID) are out of scope for the core
primitive — the former needs media-format tooling, the latter operates inside the
model provider at logit level.

## First-party wiring

The framework's own chat surface (`plugins/api_routers/chat.py`) applies these
primitives when `TRANSPARENCY_ENABLED` is set:

* `POST /chat` — adds the disclosure to `metadata.ai_disclosure`, sets
  `X-Baselith-AI-Disclosure: true`, and returns an `X-Baselith-AI-Provenance`
  header over the answer text.
* `POST /chat/stream` — sets `X-Baselith-AI-Disclosure: true`. Provenance is
  omitted on the stream (it needs the full-output hash, which would require
  buffering the response).

With the flag off (default) the endpoints are byte-for-byte unchanged. Other
surfaces that emit AI output should call `get_transparency_service()` the same way.
