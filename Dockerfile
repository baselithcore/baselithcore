# ============================================================
# BaselithCore container image — the ONLY one
# ============================================================
# This replaces `Dockerfile-slim` (single-stage, local/dev) and
# `Dockerfile-full` (multi-stage, released). Two files that installed the same
# `requirements.txt` were not two products, they were one product and a drift
# source, and they had drifted in both directions:
#
#   * The names were backwards. "slim" was the BIGGER image (5.26GB against
#     4.83GB) because it kept build-essential, git, curl, wget and make in its
#     shipped layer, while "full" was the hardened multi-stage one.
#   * `values.yaml` told operators to pick a `slim` tag that "excludes heavy
#     browser/OCR extras" and a `full` tag for when they need them. No build
#     ever produced either: both installed the same Playwright, Chromium and
#     pytesseract.
#   * The RELEASED image had no `baselith` console script — the multi-stage
#     build never ran `pip install .`. The Helm chart's worker Deployment runs
#     `["baselith", "queue", "worker"]`, so that pod could only CrashLoop with
#     "baselith: not found". `baselith doctor` was equally unavailable.
#   * The released image was also missing the thread caps below, so torch and
#     OpenBLAS each sized their pools from the HOST's core count, ignoring the
#     container's CPU limit — the classic oversubscription that shows up as
#     latency under load.
#
# One file, three stages. `--target deps` gets a shell with the toolchain if
# you need to debug a dependency build; the default target is the runtime.
#
# ============================================================
# Stage 1: dependencies — heavy and cache-stable
# ============================================================
# Digest-pinned: same Dockerfile at the same commit builds the same base.
# Refresh the digest deliberately when bumping the base image — and note that
# "deliberately" has a deadline attached: the pin freezes the Debian package
# set too, so an untouched digest accumulates distro CVEs until the post-push
# Trivy gate in release-image.yml fails the release. That is exactly how an
# earlier pin (2c941e86) ended: 30 HIGH/CRITICAL findings with fixes
# available, in util-linux (CVE-2026-53612/53613/53614) and openssl
# (CVE-2026-14456). Verify a candidate before pinning it:
#   trivy image --scanners vuln --severity HIGH,CRITICAL --ignore-unfixed \
#     python:3.12-slim@sha256:<candidate>
# The digest below was checked that way and reports zero.
#
# No --platform=$BUILDPLATFORM: this stage installs native wheels (torch,
# psycopg, cryptography ...) that are copied verbatim into the runtime stage,
# so it must run on the *target* architecture — a builder pinned to the build
# host would ship amd64 .so files inside the arm64 image of a multi-arch push.
# The release workflow builds each platform on a runner of that architecture.
FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea AS deps

WORKDIR /app

# --- Build-time args ---
ARG EMBEDDER_MODEL="sentence-transformers/all-MiniLM-L6-v2"
ARG RERANKER_MODEL="cross-encoder/ms-marco-MiniLM-L-6-v2"

ENV PIP_DISABLE_PIP_VERSION_CHECK=on \
    PYTHONDONTWRITEBYTECODE=1 \
    TRANSFORMERS_CACHE=/build/models \
    HF_HOME=/build/models \
    HUGGINGFACE_HUB_CACHE=/build/models \
    SENTENCE_TRANSFORMERS_HOME=/build/models \
    BUILD_EMBEDDER_MODEL=${EMBEDDER_MODEL} \
    BUILD_RERANKER_MODEL=${RERANKER_MODEL}

# build-essential only: it is what a source distribution needs to compile.
# `git` went with the consolidation (no requirement is a VCS URL) and so did
# `ssdeep`/`libfuzzy-dev` — no package, module or plugin in this repo
# references them, and the released image has never carried libfuzzy at
# runtime, so nothing could have been linking it.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# Torch CPU-only, pinned for reproducible builds and kept in step with
# uv.lock. >=2.6 closes CVE-2025-32434 (torch.load RCE). Neither torchaudio nor
# torchvision is installed: nothing in core/ or plugins/ imports them (the
# tree's only torch import is core/services/llm/providers/huggingface_provider
# .py, and sentence-transformers needs torchvision only for image models, which
# no plugin loads). The only thing in uv.lock that wants torchvision is
# mineru's OPTIONAL `pipeline` extra, which requirements.txt does not install —
# verified on the built image, where `import torchvision` raises
# ModuleNotFoundError and everything else works.
#
# Every install targets --prefix /install (the only tree the runtime stage
# copies) and runs with that tree on PYTHONPATH, so pip judges "already
# satisfied" against the prefix rather than this stage's own site-packages.
# That is what keeps the CPU torch: without it pip would either resolve torch
# again from PyPI (the CUDA build) or treat a torch living in site-packages as
# installed and ship a runtime without it.
#
# setuptools goes FIRST, from PyPI, and the order is the whole point.
# `--index-url` below REPLACES PyPI rather than adding to it, so every
# transitive dependency of torch resolves from download.pytorch.org — and that
# mirror's newest setuptools is 78.1.0, one patch below the 78.1.1 that fixes
# CVE-2025-47273. The release image therefore shipped a flagged setuptools
# (plus its vendored jaraco.context and wheel) while uv.lock had long resolved
# 83.0.0. Installing it into the prefix first means the torch step finds the
# requirement already satisfied and never reaches for the mirror's copy; pip
# does not downgrade a satisfied requirement. A floor, not an exact pin, so
# future security releases still flow.
RUN pip install --upgrade pip \
    && PYTHONPATH=/install/lib/python3.12/site-packages \
       pip install --no-cache-dir --prefix /install "setuptools>=83.0.0" \
    && PYTHONPATH=/install/lib/python3.12/site-packages \
       pip install --no-cache-dir --prefix /install \
        torch==2.13.0 \
        --index-url https://download.pytorch.org/whl/cpu \
    && PYTHONPATH=/install/lib/python3.12/site-packages \
       pip install --no-cache-dir --prefix /install -r requirements.txt

# --- Pre-cache the embedder + reranker ---
# Baked in rather than downloaded on first use: a pod that fetches several
# hundred MB from Hugging Face during its startup probe is a cold start that
# can outlive the probe budget, and it needs egress to huggingface.co from
# production.
RUN python - <<'PY'
import os, sys
from pathlib import Path
site = Path("/install") / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
sys.path.insert(0, site.as_posix())
from sentence_transformers import SentenceTransformer, CrossEncoder
for name, loader in (
    (os.getenv("BUILD_EMBEDDER_MODEL"), SentenceTransformer),
    (os.getenv("BUILD_RERANKER_MODEL"), CrossEncoder),
):
    try:
        loader(name)
        print(f"[docker] cached model: {name}")
    except Exception as e:
        print(f"[docker] warning: unable to cache {name}: {e}", file=sys.stderr)
PY

# ============================================================
# Stage 2: the project's own distribution
# ============================================================
# Installed with --no-deps into a SEPARATE prefix, for one reason: the console
# script. `[project.scripts] baselith = core.cli.__main__:main` only exists on
# PATH if the distribution is installed, and the Helm chart's worker runs
# `baselith queue worker` while `baselith doctor` is the documented way to
# diagnose a deployment. A separate prefix keeps the 1.89GB dependency layer
# above cache-stable: this layer is a few tens of MB and is the only one a
# source change invalidates.
#
# The app still RUNS from /app (the CLI detects the checkout and re-executes
# against it), so this tree exists for its entry points, not to be imported.
FROM deps AS app

COPY pyproject.toml README.md ./
COPY core/ core/
COPY plugins/ plugins/
RUN PYTHONPATH=/install/lib/python3.12/site-packages \
    pip install --no-cache-dir --no-deps --prefix /install-app .

# ============================================================
# Stage 3: runtime (default target)
# ============================================================
FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea AS runtime

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    # No .pyc written at runtime: the tree is precompiled below, and the
    # production filesystem is read-only anyway (readOnlyRootFilesystem).
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/install/lib/python3.12/site-packages:/install-app/lib/python3.12/site-packages:/app \
    PATH="/install/bin:/install-app/bin:${PATH}" \
    TRANSFORMERS_CACHE=/app/models \
    HF_HOME=/app/models \
    HUGGINGFACE_HUB_CACHE=/app/models \
    SENTENCE_TRANSFORMERS_HOME=/app/models \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    # Thread caps. torch and OpenBLAS size their pools from the number of CPUs
    # they can see, which is the HOST's count — not the container's CPU limit.
    # Left unset, every uvicorn worker starts a pool per core and they fight
    # each other for a fraction of a core. Raise them deliberately, together
    # with the pod's CPU request, if you profile a CPU-bound embedding path.
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    # Silences the fork warning and the tokenizer's own thread pool, which is
    # useless work in a process that is already one worker of several.
    TOKENIZERS_PARALLELISM=false \
    HOST=0.0.0.0 \
    PORT=8000 \
    WEB_CONCURRENCY=1

# --- Runtime user, created up front ---
# Everything below hands ownership over as it goes (`COPY --chown`, or a chown
# inside the RUN that created the files). A trailing `chown -R appuser:appuser
# /app /install /ms-playwright` is the one instruction that silently doubles an
# image: changing a file's owner copies it up out of its earlier layer, so the
# image carried a second copy of the dependencies and of the whole Chromium
# install. On `docker history` that single layer measured 3.14GB of a 9.04GB
# image.
RUN useradd --create-home --uid 1000 --shell /bin/bash appuser

# --- Debian security updates ---
# The digest pin above freezes the package set as well as the interpreter, and
# Debian keeps publishing fixes against it. At the time of writing the pinned
# base — and the floating `python:3.12-slim` tag, which upstream has not
# rebuilt since — carries a CRITICAL in perl-base and HIGHs in gzip,
# libpcre2-8-0 and libsqlite3-0, all with a `+deb13uN` fix already in the
# archive. Refreshing the digest fixes none of them; only applying the updates
# does.
#
# So the pin and this layer answer different questions: the pin decides which
# base a build starts from, this decides that the build does not ship known
# holes in it. The cost is that the runtime layer is no longer bit-identical
# across days — which is the correct trade for security updates, and the
# reason the release pipeline scans what it pushed rather than trusting what
# it built.
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# --- Dependencies ---
# Deliberately NOT owned by appuser: the process only reads them. Leaving them
# root-owned costs nothing, keeps them out of the duplication above, and means
# a compromised process cannot rewrite its own site-packages.
COPY --from=deps /install /install
# The model cache IS written at runtime (HF_HOME=/app/models), so it is owned.
COPY --from=deps --chown=appuser:appuser /build/models /app/models

# --- Playwright's Chromium ---
# Above the source COPYs on purpose: this installs Chromium plus ~100 apt
# packages (a 1.39GB layer, the largest single step) and depends on nothing but
# /install. Below them, a one-line code change invalidated it and the build
# reinstalled the whole browser. Ownership is set inside the same RUN that
# creates the files, so it adds no second copy.
RUN mkdir -p /ms-playwright \
    && python -m playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/* \
    && chown -R appuser:appuser /ms-playwright

# --- Console script (`baselith`) ---
COPY --from=app /install-app /install-app

# --- Application source ---
# Owned by appuser, because the app writes inside its own tree: it rewrites
# configs/plugins.yaml on every plugin enable/disable, and a plugin may persist
# state under its own directory (baselithbot's .state/.secret_key).
COPY --chown=appuser:appuser backend.py ./
# Alembic config + migration scripts: both `alembic upgrade head` (the
# pre-deploy Job) and the in-app ensure_schema() fallback resolve them from /app.
COPY --chown=appuser:appuser alembic.ini ./
COPY --chown=appuser:appuser migrations/ migrations/
COPY --chown=appuser:appuser core/ core/
COPY --chown=appuser:appuser plugins/ plugins/
COPY --chown=appuser:appuser configs/ configs/
COPY --chown=appuser:appuser scripts/ scripts/
COPY --chown=appuser:appuser templates/ templates/

# --- Precompile the application bytecode ---
# pip already ships .pyc for site-packages, but core/ and plugins/ arrive as
# source, so every worker — and every restart during a rolling deploy — would
# recompile them on boot. checked-hash .pyc files stay valid regardless of
# mtimes and are re-validated against the source, so a bind-mounted edit in
# development is never served from a stale cache.
RUN python -m compileall -q --invalidation-mode checked-hash \
    /app/backend.py /app/core /app/plugins

# --- Writable runtime directories ---
RUN mkdir -p data logs documents qdrant_data \
    && chown appuser:appuser /app data logs documents qdrant_data

USER appuser

EXPOSE 8000

# python, not curl: the runtime carries no shell utilities, and pulling one in
# for a healthcheck would widen the attack surface for nothing.
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://localhost:{os.getenv(\"PORT\",\"8000\")}/health', timeout=8)" || exit 1

# --proxy-headers + FORWARDED_ALLOW_IPS: without them request.client.host behind
# a load balancer is the proxy's IP, collapsing per-IP rate limits, the
# failed-auth throttle and the admin lockout into ONE shared bucket. Trust is
# limited to the IPs listed in FORWARDED_ALLOW_IPS (uvicorn default 127.0.0.1);
# set it to your LB / ingress address(es).
# --no-server-header: drop the `Server: uvicorn` banner. nginx replaces it at
# the edge, but a pod behind a cloud LB or ingress that passes upstream headers
# through would otherwise advertise the exact server stack to every caller.
# --timeout-graceful-shutdown: bounded drain so SIGTERM with open SSE streams
# still runs lifespan cleanup before the orchestrator (k8s: a 30s grace)
# SIGKILLs.
# WEB_CONCURRENCY: size to ~CPU cores in production; 1 worker means any
# CPU-bound work freezes the whole API. With WEB_CONCURRENCY>1 also set
# PROMETHEUS_MULTIPROC_DIR (e.g. /tmp/prometheus) so /metrics aggregates across
# workers instead of answering per-process — the Helm chart does this for you.
# --timeout-keep-alive: uvicorn's 5s default is shorter than the idle timeout of
# the upstream keepalive pool of every common reverse proxy (nginx 60s, ALB 60s,
# Envoy 60s), so the proxy reuses sockets uvicorn already closed and surfaces
# sporadic 502s. Keep the app side *longer* than the proxy side.
CMD ["sh", "-c", "exec uvicorn backend:app --host ${HOST:-0.0.0.0} --port ${PORT:-8000} --workers ${WEB_CONCURRENCY:-1} --proxy-headers --no-server-header --forwarded-allow-ips ${FORWARDED_ALLOW_IPS:-127.0.0.1} --timeout-graceful-shutdown ${GRACEFUL_SHUTDOWN_TIMEOUT:-25} --timeout-keep-alive ${UVICORN_KEEP_ALIVE:-75}"]
