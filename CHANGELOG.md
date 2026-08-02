# Changelog

All notable changes to this project are documented here. This file is
maintained automatically by semantic-release from Conventional Commits and
follows [Keep a Changelog](https://keepachangelog.com) and
[Semantic Versioning](https://semver.org).

# [0.21.0](https://github.com/baselithcore/baselithcore/compare/v0.20.0...v0.21.0) (2026-08-02)


### Bug Fixes

* **baselithbot:** write the VisionConfig singleton to the module that owns it ([c504f1b](https://github.com/baselithcore/baselithcore/commit/c504f1b11d0af979348ab9faec93939021ea50dc))
* **coding-agent:** call the real SandboxService API so code actually executes ([8227748](https://github.com/baselithcore/baselithcore/commit/82277487bbe2413b9749e9fef9d4561bbccac497))
* **gitignore:** anchor the scratch `docs/` rule to the repo root ([0224185](https://github.com/baselithcore/baselithcore/commit/0224185a94dea78f46b769bd204fe69cc22efff3))
* **openapi:** restore generator-canonical formatting so the drift gate passes ([7c7bff6](https://github.com/baselithcore/baselithcore/commit/7c7bff6e571589ae881389f920e375272adc7dd8))
* re-sign drifted plugin manifests, realign prompt catalog and coding-agent test ([3346a96](https://github.com/baselithcore/baselithcore/commit/3346a96f47e70a3545e6bf06ee5ee1f60f34e5a2))
* **security:** actually disable keep-alive pooling in hardened client default transport ([b534e52](https://github.com/baselithcore/baselithcore/commit/b534e52fd3cd3a181a6f00f0a1eebe25d74ea749))
* **security:** block CGNAT range and enforce SsrfError contract in ssrf guard ([b199c2e](https://github.com/baselithcore/baselithcore/commit/b199c2ebbe44f6fd4689bd0e4f49436b94448222))
* **security:** close mounts/proxy bypass and redirect/pooling gaps in hardened client ([5d25de0](https://github.com/baselithcore/baselithcore/commit/5d25de02283761cebad9364432ccb2a1a4dfd989))
* **security:** enforce SsrfError contract on malformed URLs; harden exporters and browser pool ([9d644e2](https://github.com/baselithcore/baselithcore/commit/9d644e22b7488565fa25993b178fd15c09c3a644))
* **security:** pin JWKS/discovery fetches and unblock A2A event-loop DNS ([b57e0a9](https://github.com/baselithcore/baselithcore/commit/b57e0a96529b412c57ed2853c9903a74d86a4943))


### Features

* **a2a:** durable Postgres-backed TaskStore ([7227e8d](https://github.com/baselithcore/baselithcore/commit/7227e8d8a1b8d9cbc5c770ac758d6ab0837e4dfc))
* add ratchet-based 500-line file size checker to CI and pre-commit hooks ([6185636](https://github.com/baselithcore/baselithcore/commit/61856366c53b85bf5062c2b341527baffae695ff))
* **agent:** typed developer-facing Agent API ([3e9343b](https://github.com/baselithcore/baselithcore/commit/3e9343beec0b6501f129cb7dfa965f91fc9fbb68))
* **baselithbot:** route all outbound HTTP through SSRF-hardened client ([17fcaf7](https://github.com/baselithcore/baselithcore/commit/17fcaf73e907b8d7c1a975ffe94e2d7dd6136fe8))
* **browser_agent:** delegate SSRF primitives to core and guard sub-resource requests ([fa9d89d](https://github.com/baselithcore/baselithcore/commit/fa9d89da4ec758796b844105d8d68048d3b5e1dd))
* **core:** reconcile core/ with enterprise — indexing batching, security hardening, doc_sources sync ([c089011](https://github.com/baselithcore/baselithcore/commit/c0890115fbfe43fe30151327afab8dc737a873dd))
* **evaluation:** deterministic eval regression gate in CI ([378b1b7](https://github.com/baselithcore/baselithcore/commit/378b1b76f717de004f8155efd04ddfc20b335948))
* **guardrails:** apply InputGuard to the orchestrator streaming path ([57d93cd](https://github.com/baselithcore/baselithcore/commit/57d93cdbef9154f12cf4e6ab830f4c17d53b52af))
* **guardrails:** sanitize external content by default, wire guard pipeline into orchestrator ([405d1a9](https://github.com/baselithcore/baselithcore/commit/405d1a917dfbdd41c25f8b39972361ef0c129035))
* **hitl:** operational approval flow — checkpoint store wiring + /approvals API ([35d7133](https://github.com/baselithcore/baselithcore/commit/35d713347992c6453905787ffdad562c52e1b8f4))
* implement LLM service request routing based on task categories and cross-provider fallback execution logic ([0584dec](https://github.com/baselithcore/baselithcore/commit/0584dec8aeb27db0c3811c8cc8830ca890cf4ee7))
* **llm:** cross-provider fallback for the native structured path ([3cf2ac6](https://github.com/baselithcore/baselithcore/commit/3cf2ac6d171b12aebee90754aea7874a43957478))
* **llm:** Google Gemini provider (optional [gemini] extra) ([7deee3c](https://github.com/baselithcore/baselithcore/commit/7deee3c91469fdf42f5fdb0da07e6f9bc98ec7ad))
* **llm:** Pydantic bridge for typed structured output (generate_typed) ([17f6a8c](https://github.com/baselithcore/baselithcore/commit/17f6a8c381781a249ee9c6dfe5c46d4c49d99b53))
* **mcp:** align server with MCP spec revision 2025-11-25 ([57ce8c4](https://github.com/baselithcore/baselithcore/commit/57ce8c4d5a06f44aa982549eac04a74548370d50))
* **memory:** wire ContextFolder into AgentMemory, shrink context under token pressure ([f1c29a4](https://github.com/baselithcore/baselithcore/commit/f1c29a419fb717ce4655b5aec662239db7aebd43))
* **observability:** USD cost metric + gen_ai dashboards, OpenAPI drift gate, SDK CI ([5e5d44d](https://github.com/baselithcore/baselithcore/commit/5e5d44dbb8b07f7c9a92223317a2868157a5be5d))
* **orchestration:** crash-recovery sweep for interrupted checkpointed runs ([87e55b2](https://github.com/baselithcore/baselithcore/commit/87e55b2884ff538c9ec2137936afc199ba9f4018))
* **orchestration:** propagate per-tool autonomy categories to the parallel executor ([61b963f](https://github.com/baselithcore/baselithcore/commit/61b963fcb7b2ac17e33880557195108f8ec5722b))
* **prompts:** prompt-as-code runtime, effort-by-task-category, few-shot bridge ([ecbb6e3](https://github.com/baselithcore/baselithcore/commit/ecbb6e3c6b2ad61a3075c0836ed0a8259d3f144a))
* **reasoning:** escalate early after consecutive tool failures in ReAct ([3c9fb49](https://github.com/baselithcore/baselithcore/commit/3c9fb49041a8b553d3a21f7de08d48933076fc2f))
* **reasoning:** gate ReAct tool calls with contract, autonomy approval, and loop budget ([4f96d84](https://github.com/baselithcore/baselithcore/commit/4f96d8418d6214303dbe85f4e0c6c15cf54fc197))
* **sandbox:** AST static analysis before code execution ([2795520](https://github.com/baselithcore/baselithcore/commit/27955204c6adba4ba40d0cf72a055c2ddb262e8e))
* **security:** adopt unified SSRF guard on core outbound call sites ([28de083](https://github.com/baselithcore/baselithcore/commit/28de083d5e82b7658698a4e2d6057012c9e0702c))
* **security:** SSRF-hardened httpx client factory ([4aaae5c](https://github.com/baselithcore/baselithcore/commit/4aaae5c73019f45ff8668376961cdd401a2494bd))
* **security:** unified SSRF guard module in core/security ([45893ac](https://github.com/baselithcore/baselithcore/commit/45893ac6b0230a45526ccd191394533cbd250cf8))
* **swarm:** structured HandoffBrief + bounded context at the handoff boundary ([2e1f1a2](https://github.com/baselithcore/baselithcore/commit/2e1f1a25622239c6ceaac7d4a44560f35eb1ad6e))
* unify SSRF security logic and add initial design documentation for system-wide hardening ([5bdb5ca](https://github.com/baselithcore/baselithcore/commit/5bdb5ca35b3755374cd7df706e565338b48d25b2))
* **web_scraper:** delegate SSRF guard to core.security and guard Playwright sub-resources ([a926430](https://github.com/baselithcore/baselithcore/commit/a926430d3e50186c173eabf8edb95198c35cd428))
* **workflows:** per-node retry and cyclic evaluation loops in WorkflowExecutor ([b6b8a29](https://github.com/baselithcore/baselithcore/commit/b6b8a29b180cd6a67d2198c371ea787dbdf91a7e))

# [0.20.0](https://github.com/baselithcore/baselithcore/compare/v0.19.0...v0.20.0) (2026-07-28)


### Features

* add mineru integration, update dependencies, and configure environment constraints ([65de693](https://github.com/baselithcore/baselithcore/commit/65de693187b8083aeec556378e4b4e2fe6b5135f))
* improve retry logging by including exception types for silent errors ([71ab8f7](https://github.com/baselithcore/baselithcore/commit/71ab8f7a36dc985fa8813e0b9dbeaa80679d5a34))
* surface underlying PyJWT error causes in audit logs and bind request metadata to observability context. ([ce81ee3](https://github.com/baselithcore/baselithcore/commit/ce81ee3cf8ce726c7696c656af0c5463340d3390))

# [0.19.0](https://github.com/baselithcore/baselithcore/compare/v0.18.0...v0.19.0) (2026-07-16)


### Bug Fixes

* improve SPA entry-point resolution and add lifecycle event tracking documentation and tests ([0435f36](https://github.com/baselithcore/baselithcore/commit/0435f36ea486f6c7cee4d5e1c6b2cfacf66f1f83))
* prevent plugin loader from incorrectly identifying framework base classes as plugins ([a902cef](https://github.com/baselithcore/baselithcore/commit/a902cef44f6931e597d9de25803283bcdd4abda4))


### Features

* implement plugin lifecycle event bus and prevent stale SPA asset caching via Cache-Control headers ([6498a94](https://github.com/baselithcore/baselithcore/commit/6498a94a4cafbee96495648772757f107e5909f8))
* implement recursive transitive dependency activation and suppress 404 search errors in Qdrant ([e8f0bc9](https://github.com/baselithcore/baselithcore/commit/e8f0bc9526fe707dc055589c40d894258668e433))
* implement structured error details, add graph query decoding, and expose dependent plugin discovery ([14dfdd8](https://github.com/baselithcore/baselithcore/commit/14dfdd883ccf0cdd4462bdbf1c20787e53ae8e02))
* introduce load_plugin_dotenv utility for safe, scoped environment variable management in plugins ([76d1552](https://github.com/baselithcore/baselithcore/commit/76d1552ccbedccebae1100742717c13a0aeb30d5))
* replace Chandra OCR with MinerU as the primary OCR engine ([1f4b6e5](https://github.com/baselithcore/baselithcore/commit/1f4b6e566f6919b8bb13b2540c7a5f7550c763a9))

# [0.18.0](https://github.com/baselithcore/baselithcore/compare/v0.17.0...v0.18.0) (2026-07-11)


### Features

* add A2A task resubscription handlers and implement durable Redis-backed scratchpad storage. ([f4ec608](https://github.com/baselithcore/baselithcore/commit/f4ec608bb5c6a7e1e044dc9cd0dcf42ce0c744e1))
* add JSON-Schema validation to AgentContracts and implement swarm task decomposition limits with safety truncation. ([7cc74c1](https://github.com/baselithcore/baselithcore/commit/7cc74c150c99c04305644cf1cf1ae1da6684e5ed))
* add Redis-based cross-worker single-flight, automate prompt registry loading, and update model pricing/routing configuration. ([228a480](https://github.com/baselithcore/baselithcore/commit/228a480c89eeb7e8b15218a707a2551d4dbf77d8))
* add support for streaming structured tool calls via neutral event sequence ([e3faf57](https://github.com/baselithcore/baselithcore/commit/e3faf572bf5cd9f1958c45a655c251f6cc999bff))
* implement Aho–Corasick document index for O(n) search and add relevance-decay pruning logic with optional runtime configuration ([46000bd](https://github.com/baselithcore/baselithcore/commit/46000bd4077afed06eec98cfae1e521c70016c8f))
* implement MCP Streamable HTTP transport and introduce ReAct native tool calling support. ([8bc81f2](https://github.com/baselithcore/baselithcore/commit/8bc81f22495eb39ed3473933d7039eeefd4fdf41))
* implement SkillsService for declarative plugin skill management with progressive disclosure, MFA enforcement, and enhanced security validation ([5ca236d](https://github.com/baselithcore/baselithcore/commit/5ca236d3d1bcd3cda2de3756ba865fcf911a113e))
* introduce deterministic history compaction for agents and enforce per-chunk stream deadlines ([fd10699](https://github.com/baselithcore/baselithcore/commit/fd1069925e4e121deb341992c0ad8f43856b3e41))
* introduce independent tracking and optional limits for SQL queries in cost control middleware ([8e31728](https://github.com/baselithcore/baselithcore/commit/8e317281269fef0be8d5ca5635779db85f5aafef))
* introduce per-plugin LLM sub-scopes and improve router prefix validation for request attribution ([e9cbefc](https://github.com/baselithcore/baselithcore/commit/e9cbefcbfba99ef6a56a392d95b4c36209beb0ff))

# [0.17.0](https://github.com/baselithcore/baselithcore/compare/v0.16.0...v0.17.0) (2026-07-08)


### Features

* implement per-plugin LLM policy routing with provider-specific credentials, request context attribution, and entity endpoint ETag support ([217b7b9](https://github.com/baselithcore/baselithcore/commit/217b7b95fc5364a68c04148cf71b81bbc7272c88))
* add observability helpers for circuit breaker and database connection pool metrics ([c628c40](https://github.com/baselithcore/baselithcore/commit/c628c406b4cdec6f28a2214a32e54bc41557711a))
* add TRL 5 validation framework with golden dataset tests, configurable JWT lifetimes, and automated campaign infrastructure ([e88b040](https://github.com/baselithcore/baselithcore/commit/e88b040a3a55e83176932eb60a93a1923832ecbb))
* relocate portal to port 3010, add systemd deployment files, and fix Backstage provider extension point injection ([7e08942](https://github.com/baselithcore/baselithcore/commit/7e08942fcfe8a08e86aec2610afafd3c0663c34a))
* implement full Backstage entity graph assembly for plugin cataloging ([52386ec](https://github.com/baselithcore/baselithcore/commit/52386ec93c5a0b3ad1e78ac88988c222abe8f542))
* add configurable plugin management links, documentation support, and exponential backoff retry logic for Backstage provider sync ([e1db605](https://github.com/baselithcore/baselithcore/commit/e1db6050d9bb28a983a1857682a43bd42e175a0b))
* implement sub-app-mount API discovery to export Backstage API entities for mounted FastAPI apps ([f0bb2aa](https://github.com/baselithcore/baselithcore/commit/f0bb2aa9b7a3a797b46168be3907f959aded7ec4))
* add support for local TechDocs, subcomponent hierarchies, and full resource dependency tracking in Backstage catalog exports ([000b817](https://github.com/baselithcore/baselithcore/commit/000b81702acd3d47d3e79b8e5835e4f4bae3cefb))
* implement pure ASGI HTTP RED metrics middleware and expose via Prometheus metrics collection ([d00a4c5](https://github.com/baselithcore/baselithcore/commit/d00a4c5d67380d0d61d5588903ac2736259e7f97))
* implement BM25 search memoization, add request timeouts to LLM providers, and sanitize plugin identifiers for Backstage exports ([43ff460](https://github.com/baselithcore/baselithcore/commit/43ff460d03d1923258826ac35a8b93b0e319c8a0))
* implement GitHub token exchange for marketplace authentication in CLI login command ([ccbaaa4](https://github.com/baselithcore/baselithcore/commit/ccbaaa49efe78bbabd18478666aca2a455468881))
* implement SQLite persistence layer for DORA incident and third-party register data ([db54c1e](https://github.com/baselithcore/baselithcore/commit/db54c1eeb31410023dd959007f6bf177aa7fe75b))
* add governed client config resolver for plugins using native SDKs ([e417a72](https://github.com/baselithcore/baselithcore/commit/e417a727b8fb531f4188babb99500a10cdfd3bd0))


### Bug Fixes

* secure plugin publishing by deprecating registry URL overrides and adding path containment, while optimizing shutdown, security authentication, and adding wall-clock limits ([74d167f](https://github.com/baselithcore/baselithcore/commit/74d167f1a464928354a3d949f00c88820f99f2ce))


# [0.16.0](https://github.com/baselithcore/baselithcore/compare/v0.15.0...v0.16.0) (2026-07-03)


### Features

* add IdempotencyMiddleware and update API error response structure to RFC 9457 ([6c739f4](https://github.com/baselithcore/baselithcore/commit/6c739f40d0251d36a15af498a1711751a4ee54a3))
* add PRICING_AS_OF constant to track model pricing snapshot dates ([692c467](https://github.com/baselithcore/baselithcore/commit/692c4671be878c7c2c3b51c24c333be0e08d78de))
* harden security with IP-based admin lockout, production-only A2A fail-closed auth, A2UI URL scheme allow-listing, and input validation on feedback endpoints ([8816f45](https://github.com/baselithcore/baselithcore/commit/8816f45214cf27622437dad588676163e63224f8))
* implement core privacy, incident management, and MFA service modules with corresponding documentation and tests ([1175512](https://github.com/baselithcore/baselithcore/commit/11755128e312cc611c2bc60da37d1e4b9a3ed6b8))
* implement DORA Art. 19 incident reporting and Art. 28 ICT third-party register subsystems ([2139eb2](https://github.com/baselithcore/baselithcore/commit/2139eb29832319cfc7d6287ba29613c9081e6512))
* implement enforcement chokepoints for orchestration and secure DSN handling in Sentry. ([63e1c6d](https://github.com/baselithcore/baselithcore/commit/63e1c6d315e3809d1587658739a14bd8f1591364))
* implement hybrid memory recall with BM25-dense fusion and expand trajectory evaluation assertions ([ca15b64](https://github.com/baselithcore/baselithcore/commit/ca15b6438ee7b5c74d4ab49d1bb708bf1d41ead4))
* implement native tool calling and structured output support across providers with checkpointing and telemetry ([5983fd8](https://github.com/baselithcore/baselithcore/commit/5983fd89d3fbe569c03a66f147e52c7457485c52))

# [0.15.0](https://github.com/baselithcore/baselithcore/compare/v0.14.0...v0.15.0) (2026-06-29)


### Features

* add get_tenant_or_default to fallback to default tenant context on error ([1abc88b](https://github.com/baselithcore/baselithcore/commit/1abc88b9dc7d9e3b0a845c62a38e673b246d709b))
* add QuotaMiddleware for per-tenant and per-identity usage limits and document tenant-scoped storage and data purging ([905a5c1](https://github.com/baselithcore/baselithcore/commit/905a5c1bb1d6e22b768bb719fd485ebab76c8416))
* add runtime tenancy mode overrides for plugins with system exemption and safety fallbacks ([41b0582](https://github.com/baselithcore/baselithcore/commit/41b05823439c2407f55499957bc8db208b650225))
* add system flag to Plugin interface to hide infrastructure plugins from user-facing navigation ([e9d37b5](https://github.com/baselithcore/baselithcore/commit/e9d37b5ab0f77cee77fd33c159c21878dccbc636))
* implement multi-tenant storage isolation and per-identity/tenant usage quota enforcement ([833e7a4](https://github.com/baselithcore/baselithcore/commit/833e7a4a6c02b76f7e19f77ac6baf1e12148acaa))
* implement per-user plugin tenancy support and refactor EventBus initialization into a singleton module ([dfd672e](https://github.com/baselithcore/baselithcore/commit/dfd672e8a74498e1ee4ce364048d3e0ef2d36737))
* introduce resolve_plugin_tenant_key for store-layer plugin tenancy resolution and document usage ([d39767f](https://github.com/baselithcore/baselithcore/commit/d39767f8e0429d6c9cd67555e7dda95f6f234104))
* migrate interactions and feedback tables to Alembic to resolve dependency issues with indexing migrations ([58db197](https://github.com/baselithcore/baselithcore/commit/58db197325a115b5ddfaf8e1356db19de67c36a7))

# [0.14.0](https://github.com/baselithcore/baselithcore/compare/v0.13.0...v0.14.0) (2026-06-17)


### Bug Fixes

* verify API route presence via OpenAPI schema instead of app.routes to support FastAPI 0.137+ lazy resolution ([56a653a](https://github.com/baselithcore/baselithcore/commit/56a653ab4bee2a7828484a55d6962ca7bb71276c))


### Features

* add chaos resilience tests and Locust load testing suite ([0ac5e49](https://github.com/baselithcore/baselithcore/commit/0ac5e496cfdf00ab9d088b91027af1963b7d3c8e))
* add persistent request quota enforcement and generic cursor-based pagination utilities ([d307f17](https://github.com/baselithcore/baselithcore/commit/d307f17ca812abca5fbd4fe32c50eba369393462))
* bypass gzip compression for Server-Sent Events to prevent stream buffering ([b127512](https://github.com/baselithcore/baselithcore/commit/b127512189fb1a880a8a54ba9a1b382c39fb4b45))
* implement automated supply-chain security with Dependabot, CodeQL, and Semgrep scanning ([d50ac56](https://github.com/baselithcore/baselithcore/commit/d50ac5680dd4dbbe688ab121fd8aa61b991231d2))
* implement Baselith Python SDK with client, models, and error handling ([2811cd1](https://github.com/baselithcore/baselithcore/commit/2811cd16ac18cf95176135c927dda2a14d1b1b12))
* implement bounded LRU cache for JWT verification and secure LLM provider API keys using SecretStr. ([875dd26](https://github.com/baselithcore/baselithcore/commit/875dd262c20a8b03cc19ed82bbdbf5a4ba0abe85))
* implement extensive performance optimizations for 0.14 including connection pooling, request-path caching, concurrent execution, and query bounds. ([e138e37](https://github.com/baselithcore/baselithcore/commit/e138e374896da058f1c4c9bf0e8ecbbf5b4334c9))
* implement fine-grained capability scopes and federated OIDC identity provider integration ([8481edf](https://github.com/baselithcore/baselithcore/commit/8481edf4dbf9a3d2fb1d9a70fc73e867c4196305))
* implement outbound webhook subsystem with configurable delivery, SSRF protection, and event dispatching ([a1e0fca](https://github.com/baselithcore/baselithcore/commit/a1e0fca693a4702e22f0f6d8c6c0233e079478a1))
* implement privacy and data-subject request (DSR) framework for GDPR compliance ([7c71c71](https://github.com/baselithcore/baselithcore/commit/7c71c710f8f07388e5c18bcf88559cc4af1254c2))
* implement tenant isolation guards, per-tenant encryption, and add performance microbenchmarks ([bf078c2](https://github.com/baselithcore/baselithcore/commit/bf078c2f4cc2b095f763612ffb2da549d8609484))
* implement versioned prompt registry with YAML-based file loading, template rendering, and tracing support ([8374e31](https://github.com/baselithcore/baselithcore/commit/8374e315fc0293fcc9a113d8c680c8b49e3c5d50))


### Performance Improvements

* implement experience replay episode capping and JWT verification caching to improve system efficiency ([164b8b6](https://github.com/baselithcore/baselithcore/commit/164b8b6290117eff6857e1f8b9889703abb773aa))

# [0.13.0](https://github.com/baselithcore/baselithcore/compare/v0.12.0...v0.13.0) (2026-06-11)


### Features

* configure .env file loading for service settings and update API key aliases to support provider-specific prefixes ([af0aec2](https://github.com/baselithcore/baselithcore/commit/af0aec271cd854006b39f9ec440fddbbafaaa7ef))
* harden MCP security with process command allowlists and autonomy-based tool execution gates ([e48f6a8](https://github.com/baselithcore/baselithcore/commit/e48f6a84fb9b034490e15a736202165eb76e4623))
* implement single-load environment parsing for performance and add tool autonomy approval gating logic ([129765f](https://github.com/baselithcore/baselithcore/commit/129765f1fbac669e853ba9928c8ccfae98512b8c))


### Performance Improvements

* implement vectorized semantic cache scans, eager auth singleton initialization, and deterministic cache key serialization ([386eab4](https://github.com/baselithcore/baselithcore/commit/386eab46cb221a0b8986a8c8e6cdbdf2fb61f11e))
* optimize performance across services by streamlining Redis rate limiting, offloading blocking operations to threads, and improving token counting efficiency. ([7fc3fcf](https://github.com/baselithcore/baselithcore/commit/7fc3fcf57e66e8c043c34365f786e206326c53cb))

# [0.12.0](https://github.com/baselithcore/baselithcore/compare/v0.11.1...v0.12.0) (2026-06-07)


### Bug Fixes

* correct cyclonedx-py flag and migrate trivy-action to manual CLI installation ([e6bdf3d](https://github.com/baselithcore/baselithcore/commit/e6bdf3d7e8c46b4d1fd81d27c0d269f632981f1b))
* normalize plugin naming conventions, improve lifecycle metadata handling, and secure database credential extraction ([0363c4f](https://github.com/baselithcore/baselithcore/commit/0363c4f778eac174490f5808b3efe513a3f54054))
* update Trivy installation to use direct tarball download and ignore non-zero scan exit codes ([d5bff4d](https://github.com/baselithcore/baselithcore/commit/d5bff4d357907754fda2d2a4124316f8a37e7c19))


### Features

* centralize OpenTelemetry initialization and tracing bridge in new core module ([74f8593](https://github.com/baselithcore/baselithcore/commit/74f8593fac9edbd2b5f81f2be8987a896396d8fd))
* implement dependency-free static admin console and add path-scoped CSP relaxation for docs routes ([16a8e85](https://github.com/baselithcore/baselithcore/commit/16a8e85b320a17b12ffbf326d8a61d3149f98f1b))
* implement distributed locking, secure secret management, and full Kubernetes deployment infrastructure. ([73e9cfe](https://github.com/baselithcore/baselithcore/commit/73e9cfedcda917afa5c383e8d2a620422f2100f1))
* implement durable dead-letter queue for background job recovery and persistence ([ff7184d](https://github.com/baselithcore/baselithcore/commit/ff7184da4cb87229c990157fab2cdbf085a7f9ec))
* implement feature flags module, automate CHANGELOG generation, and add release image signing workflow. ([625bc2f](https://github.com/baselithcore/baselithcore/commit/625bc2f3134a6049447ba8572cdb902e7c8d7e03))

## [Unreleased]

### Added

- **Encryption at rest** — versioned AES-256-GCM field encryption
  (`core.security.encryption`) with key rotation; opt-in via `DATA_ENCRYPTION_KEYS`.
- **Pluggable secret resolution** (`core.security.secrets`) — env / file
  (Docker & Kubernetes secrets) backends plus a registration hook for Vault/KMS.
- **Distributed lock** (`core.resilience.DistributedLock`) — Redis-backed mutex
  for multi-replica coordination (prevents cron/scheduler double-fire).
- **Dead-letter queue** (`core.task_queue.dead_letter`) — durable capture +
  replay of terminally-failed jobs, with admin endpoints under `/admin/dlq`.
- **Standardized error envelope** for unhandled and framework errors, with a
  correlation id (additive; `HTTPException`/validation responses unchanged).
- **API versioning** — additive `/v1` aliases (toggle `API_V1_ENABLED`).
- **Feature flags** (`core.feature_flags`) — runtime toggles, percentage
  rollout, kill-switches, pluggable backend.
- **Kubernetes Helm chart** and **Terraform** module under `deploy/`, including
  a scheduled backup CronJob and `/health/ready` readiness probe.
- **Backup verification** (`scripts/verify-backup.sh`) with integrity and
  restore-drill modes; gzip-aware restore.
- **SLOs & error-budget alerts** (`deploy/prometheus/slo-rules.yml`).
- **Supply chain**: CycloneDX SBOM and Trivy scan jobs in CI.

### Changed

- Raised the project test-coverage gate.

### Fixed

- `scripts/restore-db.sh` now restores gzipped (`.sql.gz`) backups.

---

> Earlier releases were published as GitHub Releases only. From the next release
> onward, version sections are appended above automatically.
