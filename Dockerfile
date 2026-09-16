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
#
# BuildKit cache mounts (this file requires BuildKit — Docker >= 23, which is
# what buildx in release-image.yml and `docker compose build` both use). The
# downloaded .debs and the package lists live in a cache that survives between
# builds instead of being re-fetched every time, and because a cache mount is
# not part of the layer, neither of them ends up in the image — which is what
# the old trailing `rm -rf /var/lib/apt/lists/*` was there to guarantee.
# `docker-clean` has to go first: the base image installs it precisely to
# delete the .debs after every apt run, which would empty the cache we just
# mounted. `sharing=locked` serialises concurrent builds on the same cache
# rather than letting two apt processes corrupt it.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    rm -f /etc/apt/apt.conf.d/docker-clean \
    && apt-get update \
    && apt-get install -y --no-install-recommends build-essential

# --- The dependency set, materialised from the lock ---
# uv.lock, not requirements.txt. requirements.txt carries the same *specs* as
# pyproject.toml — ranges, by design, because it is a library contract — so
# `pip install -r requirements.txt` resolved every transitive dependency to
# whatever was newest on the morning of the build. The image therefore ran a
# combination no test ever saw, and two builds of the same commit were two
# different images. `uv export --frozen` emits the exact set uv.lock pins, i.e.
# the one CI installs and tests against. requirements.txt stays as the
# human-readable mirror of the spec surface (and as the subject of the
# `requirements_sync` gate), it is just no longer what the image installs.
#
# The extras are the capability groups the image bakes in. `mineru` is
# deliberately absent (it conflicts with `huggingface`, see [tool.uv] conflicts
# in pyproject.toml), and two more were removed once the built image was
# actually inspected, because they could not work inside it:
#
#   * `ocr` (pytesseract, pdf2image). Both are thin wrappers around SYSTEM
#     binaries — `tesseract` and poppler's `pdftoppm` — and no stage here
#     installs either. Verified on the built image: `pytesseract
#     .get_tesseract_version()` raises TesseractNotFoundError and `command -v
#     pdftoppm` finds nothing. plugins/document_sources/ocr_backends.py catches
#     that and degrades, so nothing crashed; the packages were simply ~10MB of
#     a capability the image can never deliver. An operator who needs OCR
#     derives an image that apt-installs tesseract-ocr + poppler-utils and adds
#     the extra back.
#   * `computer_use` (pyautogui, mss). Both need an X11 display. In this
#     headless server image `import pyautogui` raises KeyError: 'DISPLAY' and
#     mss raises "Cannot connect to display". The import guards in
#     plugins/baselithbot/computer_use/ already treat them as optional.
#
# The `grep -v` drops the CUDA runtime, triton and the CUDA Python bindings.
# They are dependencies of the PyPI torch wheel, which is what uv.lock
# resolved; this image installs the CPU build from download.pytorch.org
# instead (see the next step), and that wheel needs none of them. Left in, they
# add several GB of GPU libraries that nothing in the image ever loads.
#
# `cuda-` belongs in that pattern and was missing: torch 2.13.0 declares
# `cuda-bindings` on linux, which pulls `cuda-pathfinder`, and neither matches
# `^nvidia-` or `^triton`. 27MB of CUDA bindings shipped in every "CPU-only"
# image while the guard below reported success — see the note there.
COPY pyproject.toml uv.lock ./

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=cache,target=/root/.cache/pip \
    pip install uv==0.12.0 \
    && uv export --frozen --no-dev --no-emit-project --no-hashes --no-annotate \
        --extra qdrant --extra huggingface --extra rag --extra nlp --extra memory \
        --extra web --extra browser --extra documents \
        --format requirements-txt -o /tmp/requirements.lock.txt \
    && grep -vE '^(nvidia-|triton|cuda-)' /tmp/requirements.lock.txt \
        > /tmp/requirements.image.txt \
    && echo "locked set: $(grep -cE '^[a-zA-Z0-9]' /tmp/requirements.image.txt) packages"

# Torch CPU-only, pinned for reproducible builds and kept in step with
# uv.lock. >=2.6 closes CVE-2025-32434 (torch.load RCE). Neither torchaudio nor
# torchvision is installed: nothing in core/ or plugins/ imports them (the
# tree's only torch import is core/services/llm/providers/huggingface_provider
# .py, and sentence-transformers needs torchvision only for image models, which
# no plugin loads). The only thing in uv.lock that wants torchvision is
# mineru's OPTIONAL `pipeline` extra, which the export above does not select —
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
#
# `--no-cache-dir` is gone on purpose: with the BuildKit cache mount below, pip
# reuses its own wheel cache across builds (a rebuild after a lock bump
# re-downloads only what changed) and the cache still never lands in a layer,
# which is the only thing --no-cache-dir was buying.
#
# The final `if` is the guard for the CUDA strip in the export step above: if an
# nvidia-* package ever makes it into the prefix, the "CPU-only" image has
# quietly grown by gigabytes, and the build fails instead of pushing it.
#
# It matches `cuda` as well as `nvidia`, and that is not cosmetic: the check
# reads DIRECTORY names in site-packages, and cuda-bindings/cuda-pathfinder
# install a directory called `cuda`. Matching only `^nvidia` meant the guard
# printed nothing while 27MB of CUDA bindings sat in the image — a green light
# that was measuring the wrong thing. The offending names are echoed on
# failure so the next person does not have to go looking for them.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip \
    && PYTHONPATH=/install/lib/python3.12/site-packages \
       pip install --prefix /install "setuptools>=83.0.0" \
    && PYTHONPATH=/install/lib/python3.12/site-packages \
       pip install --prefix /install \
        torch==2.13.0 \
        --index-url https://download.pytorch.org/whl/cpu \
    && PYTHONPATH=/install/lib/python3.12/site-packages \
       pip install --prefix /install -r /tmp/requirements.image.txt \
    && if ls /install/lib/python3.12/site-packages | grep -qE '^(nvidia|cuda)'; then \
         echo "ERROR: CUDA packages installed into a CPU-only image" >&2; \
         ls /install/lib/python3.12/site-packages | grep -E '^(nvidia|cuda)' >&2; \
         exit 1; \
       fi

# --- The spaCy pipeline ---
# spaCy and its compiled stack (thinc, blis, its slice of numpy) are ~160MB of
# this image, and without a MODEL they buy nothing: core/config/processing.py
# ships `enable_spacy_documents: bool = Field(default=True)` with
# `spacy_model="en_core_web_sm"`, and core/nlp/spacy_utils.py catches the
# resulting OSError and falls back to `spacy.blank()` — a sentencizer, no NER,
# no tagger, no lemmatiser. Verified on the built image before this step
# existed: `spacy.load("en_core_web_sm")` raised E050.
#
# So the choice was to drop the `nlp` extra or to add the 15MB that makes the
# 160MB work. Since core enables the feature by DEFAULT, dropping it would ship
# an official image whose core NLP path is permanently degraded; baking the
# model is the coherent half of the trade.
#
# Pinned rather than resolved: `python -m spacy download` fetches
# compatibility.json at build time and picks a version, which is exactly the
# "two builds of the same commit are two different images" problem the uv.lock
# export above exists to avoid. The load check on the next line is the guard
# for the pin — spaCy models are compatible within a minor, so if the `spacy`
# range in pyproject.toml ever resolves past 3.8 this fails the build loudly
# instead of shipping a model the runtime cannot open.
ARG SPACY_MODEL="en_core_web_sm"
ARG SPACY_MODEL_VERSION="3.8.0"
RUN --mount=type=cache,target=/root/.cache/pip \
    PYTHONPATH=/install/lib/python3.12/site-packages \
    pip install --prefix /install \
      "https://github.com/explosion/spacy-models/releases/download/${SPACY_MODEL}-${SPACY_MODEL_VERSION}/${SPACY_MODEL}-${SPACY_MODEL_VERSION}-py3-none-any.whl" \
    && PYTHONPATH=/install/lib/python3.12/site-packages BAKED_SPACY_MODEL="${SPACY_MODEL}" \
       python -c "import os, spacy; nlp = spacy.load(os.environ['BAKED_SPACY_MODEL']); assert 'ner' in nlp.pipe_names, nlp.pipe_names; print('[docker] spaCy pipeline:', nlp.pipe_names)"

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
# The app still RUNS from /app, so this tree exists for its entry points, not
# to be imported — and the `rm -rf` below is what makes that true rather than
# merely intended. `pip install .` also materialises core/ and plugins/ inside
# the prefix, and PYTHONPATH lists /install-app BEFORE /app, so which copy won
# depended on the working directory. Measured on the built image:
#
#   cwd=/      import core -> /install-app/lib/python3.12/site-packages/core
#   cwd=/app   import core -> /app/core
#
# uvicorn runs from WORKDIR /app, so the API was always reading the right tree;
# the `baselith` console script invoked from anywhere else was not. That split
# matters because the CLI WRITES — `baselith plugin enable` rewrites
# configs/plugins.yaml — so a command run from the wrong directory would have
# edited a tree the API never reads. Deleting the packages leaves /app as the
# only importable copy for every entry point, and takes 24.6MB of duplicate
# source (17MB core, 7MB plugins) out of the image as a side effect.
#
# The dist-info stays: it is what `importlib.metadata` resolves the installed
# version from, and it carries the console script's entry point.
FROM deps AS app

COPY pyproject.toml README.md ./
COPY core/ core/
COPY plugins/ plugins/
RUN --mount=type=cache,target=/root/.cache/pip \
    PYTHONPATH=/install/lib/python3.12/site-packages \
    pip install --no-deps --prefix /install-app . \
    && PYTHONPATH=/install/lib/python3.12/site-packages:/install-app/lib/python3.12/site-packages \
       pip check \
    && rm -rf /install-app/lib/python3.12/site-packages/core \
              /install-app/lib/python3.12/site-packages/plugins \
    && test -x /install-app/bin/baselith

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

# --- Dependencies ---
# Deliberately NOT owned by appuser: the process only reads them. Leaving them
# root-owned costs nothing, keeps them out of the duplication above, and means
# a compromised process cannot rewrite its own site-packages.
COPY --from=deps /install /install
COPY configs/plugin-requirements.txt configs/plugin-requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    if grep -Eq '^[[:space:]]*[^#[:space:]]' configs/plugin-requirements.txt; then \
        PYTHONPATH=/install/lib/python3.12/site-packages \
        pip install --prefix /install -r configs/plugin-requirements.txt; \
    fi
# The model cache IS written at runtime (HF_HOME=/app/models), so it is owned.
COPY --from=deps --chown=appuser:appuser /build/models /app/models

# --- Playwright's Chromium ---
# Above the source COPYs on purpose: this installs Chromium plus its apt
# packages (a 1.39GB layer, the largest single step) and depends on nothing but
# /install. Below them, a one-line code change invalidated it and the build
# reinstalled the whole browser. Ownership is set inside the same RUN that
# creates the files, so it adds no second copy.
#
# The apt packages are listed here instead of delegated to `playwright install
# --with-deps`. That flag installs Playwright's generic `chromium` set plus its
# `tools` set — sized for the full browser and for headed runs — and on Debian
# 13 that meant libcups2t64 (with avahi behind it), xvfb, xserver-common and
# X11 bitmap fonts: ~110 packages, of which the cups/avahi/xorg group alone
# carried 37 Debian CVEs with no fix available, in code nothing in this
# container can reach. The only binary installed is the headless shell (see
# `--only-shell` below); `ldd` on it names no libcups, and no X server can be
# used here (see below). So the list is Playwright's own `chromium` list for
# debian13 minus libcups2t64, and its `tools` list minus xvfb and
# xfonts-scalable. libasound2t64 stays: headless_shell links libasound.so.2
# directly and Playwright's launch preflight refuses to start without it
# (measured). Fonts are kept as Playwright ships them — Liberation for the
# metric-compatible Latin families, Noto Color Emoji, and the CJK/Thai
# fallbacks — because a screenshot of a page in those scripts is otherwise
# tofu. The apt cache mounts (and the docker-clean removal) are the same as in
# the deps stage; there is no trailing `rm -rf /var/lib/apt/lists/*` because a
# cache mount is not part of the layer in the first place.
#
# Two guards keep this list honest: Playwright's preflight at launch fails
# with the missing package by name, and `image_build` in ci.yml launches the
# browser in the built image on every PR that touches this file.
#
# `--only-shell` is worth 641MB, and the direction of that flag is the one
# thing here you should not take on trust — it was measured, and the obvious
# guess was backwards.
#
# `playwright install chromium` downloads TWO browsers: the full Chromium
# (641MB) and `chromium_headless_shell` (340MB). The natural assumption is that
# `launch(headless=True)` runs the full browser in headless mode and the shell
# is an opt-in for `channel="chromium-headless-shell"`. It is the other way
# round in this version: with no channel set, `headless=True` resolves to
# `/ms-playwright/chromium_headless_shell-*/chrome-linux/headless_shell`.
# Installing with `--no-shell` therefore broke every call site in the repo with
# "Executable doesn't exist" — verified by running it.
#
# So the unreachable binary is the FULL Chromium, and not only because nothing
# asks for it by name: it is what `headless=False` would launch, and headless
# is false nowhere that matters (plugins/browser_agent/tools.py hardcodes True,
# plugins/document_sources/web.py hardcodes True, core/config/scraper.py's
# `playwright_headless` defaults to it). More to the point it CANNOT work here
# — a headed browser needs a display, and no stage installs Xvfb or an X
# client, nor do the compose files or deploy/ provide one. Verified on the
# image that still had both binaries:
#
#   headless=True  -> page renders
#   headless=False -> BrowserType.launch: Target page, context or browser has
#                     been closed
#
# An operator who genuinely wants headed browsing needs a derived image that
# adds Xvfb anyway, and that image can drop this flag. Shipping the binary here
# only made the failure 641MB more expensive.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    rm -f /etc/apt/apt.conf.d/docker-clean \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        libasound2t64 libatk-bridge2.0-0t64 libatk1.0-0t64 libatspi2.0-0t64 \
        libcairo2 libdbus-1-3 libdrm2 libgbm1 libglib2.0-0t64 libnspr4 libnss3 \
        libpango-1.0-0 libx11-6 libxcb1 libxcomposite1 libxdamage1 libxext6 \
        libxfixes3 libxkbcommon0 libxrandr2 \
        libfontconfig1 libfreetype6 fonts-liberation fonts-noto-color-emoji \
        fonts-unifont fonts-ipafont-gothic fonts-wqy-zenhei \
        fonts-tlwg-loma-otf fonts-freefont-ttf \
    && mkdir -p /ms-playwright \
    && python -m playwright install --only-shell chromium \
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
# --- Maintenance scripts: an explicit allowlist, not the directory ---
# `COPY scripts/ scripts/` shipped the whole repository toolbox into the
# runtime image, including `scripts/reset_all.py`, `reset_analytics_db.py`,
# `reset_graphdb.py` and `reset_qdrant.py` — four scripts whose entire purpose
# is to drop the production data stores, sitting on the filesystem of the
# process that is exposed to the internet. It also shipped every CI gate
# (`check_*.py`), the plugin signing keys' tooling and the eval runners: build
# tooling that has no caller inside the container and only widens what an RCE
# can reach for.
#
# Nothing the image runs needs any of it. The API entrypoint is
# `uvicorn backend:app`, the worker is `baselith queue worker` (a console
# script from /install-app), and the migration Job runs `alembic upgrade head`
# against alembic.ini + migrations/, both copied above. Verified by grepping
# core/, backend.py, the compose files and every Helm template for a reference
# to scripts/ — the only one was this COPY.
#
# The operational shell scripts (backup-db.sh, restore-db.sh, verify-backup.sh,
# prod-preflight.sh) are documented as running on the HOST against a checkout,
# not inside this container, so they stay out too.
#
# If something here ever does need a script, add that one file by name.
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

# --- Debian security updates ---
# LAST, and the position is the whole point.
#
# The digest pin freezes the Debian package set as well as the interpreter, and
# Debian keeps publishing fixes against a frozen set. Refreshing the digest
# collects them only if upstream happened to rebuild, so a pin left alone
# accumulates distro CVEs until the post-push Trivy gate fails a release. The
# pin decides which base a build starts from; this decides that the build does
# not ship known holes in it.
#
# Two reasons it runs here rather than near the top of the stage:
#
#   * The Chromium step above installs ~60 apt packages, and an
#     upgrade placed before it can never reach them. On a FRESH build that
#     costs nothing — apt installs them from the archive with its security
#     updates already in, and either position upgrades the same 12 base
#     packages (measured). It costs something on a later release, where that
#     1.39GB Chromium layer comes from the build cache months old and its
#     packages are frozen at the day it was built; only an upgrade downstream
#     of it catches those.
#   * Docker keys a RUN's cache on the command string and the parent layer, not
#     on what the apt archive holds today. Near the top, the layer stayed
#     cached for as long as the base digest did, so the upgrade ran once and
#     never again — and everything below it (the 1.89GB dependency copy, the
#     1.39GB Chromium install) would have had to rebuild to force it. Here, the
#     source COPYs above already change on every release (semantic-release
#     rewrites core/_version.py), so this layer is rebuilt every release for
#     free, and it is the only one that is. Re-tagging an unchanged tree reuses
#     it, which is the honest limit of the arrangement.
#
# The interpreter is unaffected: python:3.12-slim compiles CPython from source
# into /usr/local, so apt owns no part of it.
#
# pip rides in the same layer, for the same reason. The base image ships pip
# 25.0.1 in /usr/local and that copy is the one runtime pip there is —
# `baselith plugin deps install` (core/marketplace/installer.py) runs
# `sys.executable -m pip` — so it cannot simply be removed. 25.0.1 carries five
# advisories fixed by 26.2.0 (CVE-2025-8869, CVE-2026-3219, CVE-2026-6357,
# CVE-2026-8643, CVE-2026-13346: symlink, archive and entry-point handling
# during wheel installation, and an index-driven arbitrary file write). The
# deps stage upgrades its own pip, but that stage's /usr/local never reaches
# this image. A floor rather than a pin, so it floats with the apt upgrade
# above: the floor records the last fixed version, the float collects the
# next one.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    --mount=type=cache,target=/root/.cache/pip \
    rm -f /etc/apt/apt.conf.d/docker-clean \
    && apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && pip install --upgrade "pip>=26.2.0"

USER appuser

EXPOSE 8000

# python, not curl: the runtime carries no shell utilities, and pulling one in
# for a healthcheck would widen the attack surface for nothing.
#
# --start-period is the fix for a container that was marked unhealthy while it
# was still perfectly fine: without it the retry budget (3 x 30s = 90s) starts
# counting at process start, and this app's boot is nowhere near that fast —
# it imports torch and sentence-transformers, loads the pre-cached embedder and
# reranker off disk, runs the plugin loader and opens the DB/Redis pools. On a
# throttled pod that exceeds 90s, the container flips to `unhealthy`, and an
# orchestrator configured to act on it restarts a process that was seconds from
# serving. During the start period a failing probe does not count against
# `--retries`; the FIRST success ends the period early, so a fast boot is not
# penalised by the generous budget.
#
# --start-interval polls every 5s during that window instead of every 30s, so a
# container that becomes healthy at t=12s is marked healthy at ~t=15s rather
# than waiting for the next 30s tick (Docker >= 25; ignored by older daemons).
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    --start-period=300s --start-interval=5s \
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
# SIGKILLs. The default MUST match backend.py's
# `GRACEFUL_SHUTDOWN_TIMEOUT`, "30"` — it read 25 here, so the same variable
# meant two different drains depending on whether the app was started through
# this CMD or through `python backend.py`.
# WEB_CONCURRENCY: size to ~CPU cores in production; 1 worker means any
# CPU-bound work freezes the whole API. With WEB_CONCURRENCY>1 also set
# PROMETHEUS_MULTIPROC_DIR (e.g. /tmp/prometheus) so /metrics aggregates across
# workers instead of answering per-process — the Helm chart does this for you.
# --timeout-keep-alive: uvicorn's 5s default is shorter than the idle timeout of
# the upstream keepalive pool of every common reverse proxy (nginx 60s, ALB 60s,
# Envoy 60s), so the proxy reuses sockets uvicorn already closed and surfaces
# sporadic 502s. Keep the app side *longer* than the proxy side.
CMD ["sh", "-c", "exec uvicorn backend:app --host ${HOST:-0.0.0.0} --port ${PORT:-8000} --workers ${WEB_CONCURRENCY:-1} --proxy-headers --no-server-header --forwarded-allow-ips ${FORWARDED_ALLOW_IPS:-127.0.0.1} --timeout-graceful-shutdown ${GRACEFUL_SHUTDOWN_TIMEOUT:-30} --timeout-keep-alive ${UVICORN_KEEP_ALIVE:-75}"]
