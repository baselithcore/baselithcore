# Project Governance

How decisions get made in BaselithCore, who makes them, and what a contributor
can expect. This document describes the project as it actually runs today, not
an aspirational structure.

## Scope

BaselithCore is a modular orchestration engine for production-grade agentic AI.
Its scope boundary is architectural, not editorial — the **Sacred Core rule**:

> `core/` contains only domain-agnostic logic (orchestration, infrastructure,
> utilities). Domain-specific logic, external integrations, and business
> features live under `plugins/`.

A proposal that would put domain logic in `core/` is out of scope regardless of
its merit, and the boundary is enforced mechanically by
`scripts/check_architecture_boundaries.py`. The answer to "will you accept
this?" is usually "yes, as a plugin".

## Roles

**Users** run the framework. Bug reports and questions are contributions;
neither requires writing code.

**Contributors** open pull requests. No formal membership, CLA, or prior
approval is needed. See [CONTRIBUTING.md](CONTRIBUTING.md).

**Maintainers** review and merge, own release decisions, and are listed in
[`.github/CODEOWNERS`](.github/CODEOWNERS), which routes review requests
automatically. Security-sensitive paths — authentication, plugin integrity and
signing, CI configuration — are owned explicitly, so ownership of them survives
any change to the default.

The project currently has a single maintainer. That is a fact about its size,
not a policy: the sections below define how decisions are made so the process
does not have to be reinvented when that changes.

## Decision-making

**Ordinary changes** — bug fixes, new plugins, documentation, tests, additive
API — are decided in the pull request. A maintainer's approval plus green CI is
sufficient. Silence is not approval; the PR waits for a review.

**Substantial changes** need a written proposal in a
[GitHub Discussion](https://github.com/baselithcore/baselithcore/discussions)
or an issue *before* the code, so the design is argued while it is still cheap
to change. A change is substantial when it:

- removes or renames anything in the `stable` tier (see
  [API stability tiers](mkdocs-site/docs/advanced/api-stability.md));
- adds a package to `core/`, or promotes a package's stability tier;
- adds, removes, or materially changes a CI quality gate;
- changes the plugin manifest contract or the plugin ABI;
- adds a runtime dependency to the base install.

**Disagreements** are resolved by discussion on the proposal, on the technical
merits and the project's constraints: the Sacred Core rule, the stability
promises already published, and the maintenance cost of the result. Where
discussion does not converge, maintainers decide and record why in the thread.
A decision is expected to cite the constraint it rests on.

## Rejections

A proposal is declined with a reason, in writing, in the thread. The common
reasons are scope (belongs in a plugin), stability cost (breaks a promise for
a gain that does not justify a MAJOR release), and maintenance cost (the
project cannot support it). "No" is not deferred indefinitely by silence — an
unanswered proposal is a failure of the process, not an answer.

## Releases

Releases are automated. Conventional Commits drive semantic-release, which
computes the version, writes the changelog, tags, and publishes to PyPI with
build attestations. No release is cut by hand, and the version in
`core/_version.py` is written by the pipeline.

What a version number promises is defined by the
[versioning and deprecation policy](mkdocs-site/docs/advanced/versioning-and-deprecation.md),
scaled by the [stability tier](mkdocs-site/docs/advanced/api-stability.md) of
the surface involved. Both are enforced by gates rather than remembered:
removing a public symbol fails the build until the change records what it did.

## Becoming a maintainer

Maintainers are invited, on evidence: a track record of merged pull requests,
reviews that improve other people's changes, and judgement about the scope
boundary above. There is no application process and no minimum contribution
count. An invitation comes with commit rights and an entry in `CODEOWNERS`.

A maintainer who becomes inactive may be moved to emeritus status by the
remaining maintainers; this is administrative, not a judgement.

## Security

Vulnerabilities follow [SECURITY.md](SECURITY.md) — private report, coordinated
disclosure — and never the public issue tracker.

## Changing this document

By pull request, treated as a substantial change: it needs a proposal first and
maintainer approval to merge.
