# Support

Where to take a question, and what to expect.

## Documentation first

- **[Documentation site](https://docs.baselithcore.xyz)** — installation,
  quickstart, architecture, per-module reference.
- **`baselith doctor`** — environment and configuration diagnostics. Run it
  before reporting anything that looks like a setup problem; it catches most
  of them and its output is what a bug report needs anyway.
- **[Quality gates](mkdocs-site/docs/advanced/quality-gates.md)** — what each
  failing gate means and how to satisfy it.

## Questions and ideas

**[GitHub Discussions](https://github.com/baselithcore/baselithcore/discussions)**
— how do I do X, is this the intended design, here is what I built. Discussions
are the right place for anything without a reproducer.

## Bugs

**[GitHub Issues](https://github.com/baselithcore/baselithcore/issues)** — for
behaviour that contradicts the documentation, with a reproducer, the version,
and what you expected instead. Issues without a reproducer usually become
discussions.

## Security vulnerabilities

**Never** in a public issue or discussion. Follow [SECURITY.md](SECURITY.md):
private advisory or `security@baselithcore.dev`. Response targets are published
there.

## What to expect

This is an open-source project maintained by volunteers. There is no service
level agreement on issues or discussions, and no guaranteed response time
outside the security process. A clear, reproducible report gets an answer far
faster than an urgent one.

## Supported versions

Only the latest release is supported. Security fixes land on it; older versions
are not backported. The supported range is stated in
[SECURITY.md](SECURITY.md).
