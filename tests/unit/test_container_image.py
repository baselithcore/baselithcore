"""Guards for the container image and the compose stack.

Split out of ``test_packaging_and_ci.py`` (500-line cap). Every assertion here
stands in for a failure that only a built and *run* image would show: a
maintenance script shipped into production, a CUDA wheel in a CPU-only image,
a browser binary the code never launches, an apt package list that quietly
grows back the daemons nothing in the container can reach, a runtime pip that
is older than its own advisories.

All of it is read from the files themselves — no network, no build, no
subprocess — so the suite stays fast and these stay honest.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO_ROOT / "Dockerfile"
COMPOSE = REPO_ROOT / "compose.yaml"


def _without_comments(text: str) -> str:
    """Strip ``#`` comment lines, so prose about a pattern is not read as it.

    The Dockerfile explains at length why it does what it does, and those
    explanations naturally quote the very strings these assertions search for.
    """
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


def test_runtime_image_does_not_ship_the_maintenance_scripts() -> None:
    """`scripts/reset_*.py` drop the production data stores."""
    text = DOCKERFILE.read_text(encoding="utf-8")
    copies = re.findall(r"^COPY\s+(?:--\S+\s+)*scripts/?\s", text, flags=re.M)
    assert not copies, (
        "The Dockerfile copies the whole scripts/ directory into the runtime "
        "image again. That ships reset_all.py, reset_graphdb.py and "
        "reset_qdrant.py — which exist to wipe the data stores — onto the "
        "filesystem of the internet-facing process. Copy individual scripts by "
        "name if one is genuinely needed at runtime."
    )


def test_image_installs_the_locked_dependency_set() -> None:
    """requirements.txt carries ranges, so installing it re-resolves nightly."""
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "uv export --frozen" in text, (
        "The image must materialise uv.lock, not resolve requirements.txt: "
        "ranges made two builds of the same commit two different images."
    )
    assert not re.search(
        r"pip install[^\n]*-r requirements\.txt", _without_comments(text)
    ), "The image is installing from requirements.txt again."


def test_image_strips_cuda_from_the_cpu_only_build() -> None:
    """The CPU torch wheel needs none of it; left in it is gigabytes.

    This asserts what the filter DOES, not how it is spelled. It used to pin
    the literal ``grep -vE '^(nvidia-|triton)'``, which froze a pattern that
    did not do the job: torch declares ``cuda-bindings`` on linux, that pulls
    ``cuda-pathfinder``, and neither matches ``^nvidia-`` or ``^triton``. 27MB
    of CUDA bindings shipped in every "CPU-only" image while this test was
    green, because it was checking the spelling of the answer rather than the
    answer. Running the pattern against real package names cannot go stale the
    same way.
    """
    # Comments stripped throughout: the Dockerfile explains both patterns at
    # length and quotes them, and a search over the prose finds the
    # explanation rather than the instruction.
    text = _without_comments(DOCKERFILE.read_text(encoding="utf-8"))
    assert "--index-url https://download.pytorch.org/whl/cpu" in text

    filter_match = re.search(r"grep -vE '([^']+)'", text)
    assert filter_match is not None, (
        "The export no longer filters the GPU stack out of the locked set."
    )
    gpu_filter = re.compile(filter_match.group(1))

    for requirement in (
        "nvidia-cublas-cu12==12.4.5.8",
        "nvidia-cudnn-cu12==9.1.0.70",
        "triton==3.1.0",
        "cuda-bindings==13.3.1",
        "cuda-pathfinder==1.6.0",
    ):
        assert gpu_filter.search(requirement), (
            f"{requirement!r} survives the GPU filter and lands in a CPU-only "
            "image. Every GPU package family torch can pull has to match."
        )

    for requirement in ("torch==2.13.0", "numpy==2.3.4", "transformers==5.3.0"):
        assert not gpu_filter.search(requirement), (
            f"The GPU filter also strips {requirement!r}, which the image needs."
        )

    # The post-install guard is the second half, and it missed the same family
    # for the same reason: it reads DIRECTORY names in site-packages, and
    # cuda-bindings installs one called `cuda`, not `nvidia`.
    guard_match = re.search(r"grep -qE '([^']+)'", text)
    assert guard_match is not None, "the CPU-only guard is gone"
    guard = re.compile(guard_match.group(1))
    for directory in ("nvidia", "cuda"):
        assert guard.search(directory), (
            f"The guard does not match a site-packages directory named "
            f"{directory!r}, so it would report success with CUDA installed."
        )


def test_image_installs_only_the_browser_it_launches() -> None:
    """`playwright install chromium` fetches two browsers; one cannot run."""
    # Comments stripped: the block above this instruction explains the flag at
    # length and quotes it, which a naive search reads as the instruction.
    text = _without_comments(DOCKERFILE.read_text(encoding="utf-8"))
    install = re.search(r"playwright install[^\n]*", text)
    assert install is not None, "the image no longer installs a browser"
    assert "--only-shell" in install.group(0), (
        "The image is installing the full Chromium again (641MB). With no "
        "channel set, launch(headless=True) resolves chromium_headless_shell, "
        "and the full browser is only what headless=False would start -- which "
        "cannot work here, because no stage installs Xvfb or an X client. Note "
        "the direction: --no-shell is the opposite flag and breaks every call "
        "site with 'Executable doesn't exist'."
    )


def test_image_lists_the_browser_apt_packages_itself() -> None:
    """`--with-deps` installs cups, avahi and an X server the shell never loads."""
    text = _without_comments(DOCKERFILE.read_text(encoding="utf-8"))
    install = re.search(r"playwright install[^\n]*", text)
    assert install is not None, "the image no longer installs a browser"
    assert "--with-deps" not in install.group(0), (
        "The image delegates the apt packages to `playwright install "
        "--with-deps` again. That set is sized for the full browser and for "
        "headed runs: on Debian 13 it brought libcups2t64 (avahi behind it), "
        "xvfb and xserver-common -- 37 unfixable Debian CVEs in code nothing "
        "in the container can reach. List the packages explicitly instead."
    )
    runtime = text[text.rfind("FROM ") :]
    # The packages sit between `apt-get install` and the `playwright install`
    # that needs them, in the same RUN; a backslash-continued list, so read
    # every token in that span.
    apt_at = runtime.find("apt-get install")
    assert apt_at != -1, "the runtime stage no longer installs apt packages"
    playwright_at = runtime.find("playwright install", apt_at)
    assert playwright_at != -1, "the browser is no longer installed"
    packages = set(runtime[apt_at:playwright_at].replace("\\", " ").split())
    # What headless_shell links (ldd) and what Playwright's launch preflight
    # demands. libasound2t64 is the one people reach for removing: the binary
    # links libasound.so.2 directly and refuses to start without it.
    for required in ("libnss3", "libgbm1", "libasound2t64", "libfontconfig1"):
        assert required in packages, (
            f"{required} is missing from the browser package list; the "
            "headless shell will not launch without it."
        )
    for dropped in ("libcups2t64", "xvfb", "xserver-common", "xfonts-scalable"):
        assert dropped not in packages, (
            f"{dropped} is back in the image. It exists only for the full "
            "browser or for a headed run, neither of which this image can do."
        )


def test_runtime_pip_is_above_its_advisories() -> None:
    """The base image's pip is the runtime pip, and the deps stage's upgrade never reaches it."""
    text = _without_comments(DOCKERFILE.read_text(encoding="utf-8"))
    runtime = text[text.rfind("FROM ") :]
    upgrade = re.search(r"pip install --upgrade \"pip>=([0-9.]+)\"", runtime)
    assert upgrade is not None, (
        "The runtime stage no longer upgrades pip. python:3.12-slim ships "
        "25.0.1, which carries five advisories fixed by 26.2.0, and "
        "`baselith plugin deps install` runs that copy via `sys.executable "
        "-m pip`."
    )
    floor = tuple(int(part) for part in upgrade.group(1).split("."))
    assert floor >= (26, 2, 0), (
        f"pip floor {upgrade.group(1)} is below 26.2.0, the version that "
        "closes CVE-2026-13346 (arbitrary file installation via a malicious "
        "index)."
    )


def test_project_distribution_is_not_a_second_importable_copy() -> None:
    """/install-app precedes /app on PYTHONPATH, so a duplicate shadows it."""
    text = _without_comments(DOCKERFILE.read_text(encoding="utf-8"))
    assert re.search(r"rm -rf /install-app/lib/[^\n]*/core", text), (
        "pip install . materialises core/ and plugins/ inside /install-app "
        "alongside the console script, and /install-app comes BEFORE /app on "
        "PYTHONPATH -- so which copy wins depends on the working directory. "
        "`baselith plugin enable` writes configs/plugins.yaml, so the CLI run "
        "from the wrong directory edits a tree the API never reads."
    )


def test_healthcheck_allows_for_a_slow_cold_start() -> None:
    """Without a start period the retry budget runs during boot."""
    text = DOCKERFILE.read_text(encoding="utf-8")
    healthcheck = re.search(r"^HEALTHCHECK(.*?)CMD ", text, flags=re.M | re.S)
    assert healthcheck is not None, "the image no longer declares a HEALTHCHECK"
    flags = healthcheck.group(1)
    assert "--start-period=" in flags, (
        "A container that imports torch and loads the cached models can exceed "
        "the 3x30s retry budget, so it is marked unhealthy while still booting."
    )
    assert "--start-interval=" in flags


@pytest.mark.parametrize("service", ["api", "worker"])
def test_compose_app_services_are_hardened(service: str) -> None:
    """compose.yaml is the stack people actually run; it had none of this."""
    spec = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"][service]
    assert spec.get("security_opt") == ["no-new-privileges:true"], (
        f"{service} can gain privileges through a setuid binary."
    )
    assert spec.get("cap_drop") == ["ALL"], (
        f"{service} keeps the full default capability set, which a Python web "
        "app has no use for."
    )
