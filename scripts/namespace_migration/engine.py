"""Run the rules over a tree: per-file processing, parallel fan-out, apply."""

from __future__ import annotations

import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import libcst as cst

from .config import OLD_TOPS, SKIP_FILE_PRAGMA, TEST, TOOLING, classify
from .files import candidate_files, list_files, shadow_roots, shadowed_for
from .index import ModuleIndex
from .markdown import rewrite_imports_regex, rewrite_markdown
from .move import move_tree, pending_moves
from .py_rewrite import rewrite_python
from .report import FileResult, Report
from .targeted import apply_targeted
from .text_rules import REPO_FIXTURE_RE, TextContext, rewrite_text


@dataclass(frozen=True)
class Job:
    """One file to process (picklable; the shared index travels once per worker)."""

    root: str
    rel: str
    shadowed: frozenset[str]


_INDEX: ModuleIndex | None = None


def _init_worker(index: ModuleIndex) -> None:
    global _INDEX
    _INDEX = index


def process_file(job: Job) -> FileResult:
    """Compute the rewrite of one file; never writes."""
    assert _INDEX is not None, "call _init_worker first"
    result = FileResult(job.rel)
    path = Path(job.root) / job.rel
    try:
        source = path.read_bytes().decode("utf-8")
    except (UnicodeDecodeError, OSError):
        return result
    if SKIP_FILE_PRAGMA in source or ("core" not in source and "plugins" not in source):
        return result
    ctx = TextContext(
        index=_INDEX,
        local_dir=str(PurePosixPath(job.rel).parent)
        if job.rel.endswith(".md")
        else None,
        repo_fixture=classify(job.rel) == TEST and bool(REPO_FIXTURE_RE.search(source)),
        rooted_paths=classify(job.rel) == TOOLING
        and PurePosixPath(job.rel).suffix not in (".py", ".pyi", ".md", ".rst"),
        file_class=classify(job.rel),
        shadowed=job.shadowed,
        counts=result.counts,
        unresolved=result.unresolved,
    )
    suffix = PurePosixPath(job.rel).suffix
    try:
        new = apply_targeted(source, job.rel, ctx)
        if suffix in (".py", ".pyi"):
            moves = PurePosixPath(job.rel).parts[0] in OLD_TOPS
            try:
                new = rewrite_python(new, job.rel, ctx, moves=moves)
            except cst.ParserSyntaxError as exc:
                # Not Python yet (a template with placeholders): the regex
                # import rule plus the text rules still apply.
                ctx.unresolved.append(("parse_fallback", str(exc).splitlines()[0]))
                new = rewrite_text(rewrite_imports_regex(new, ctx), ctx)
        elif suffix in (".md", ".rst"):
            new = rewrite_markdown(new, ctx, job.rel)
        else:
            new = rewrite_text(new, ctx)
    except Exception as exc:
        result.error = f"{type(exc).__name__}: {exc}"
        result.counts.clear()
        return result
    if new != source:
        result.new_text = new
    return result


def _chunks(n_files: int, jobs: int) -> int:
    return max(1, n_files // (jobs * 8))


def run(
    root: Path,
    *,
    apply: bool,
    move: bool = True,
    jobs: int = 1,
    only: list[str] | None = None,
) -> Report:
    """Analyse (and with *apply*, rewrite and move) the tree at *root*.

    Args:
        root: Repository root.
        apply: Write the rewrites and perform the move.
        move: Perform the directory move when applying.
        jobs: Worker processes (1 = in-process, deterministic either way).
        only: Restrict processing to these repo-relative files (tests).

    Returns:
        The aggregated report; ``report.moved`` lists moves done (apply) or
        pending (dry run / check).
    """
    started = time.perf_counter()
    all_files = list_files(root)
    index = ModuleIndex.from_files(all_files)
    roots = shadow_roots(all_files)
    targets = candidate_files(only if only is not None else all_files)
    batch = [Job(str(root), rel, shadowed_for(rel, roots)) for rel in targets]
    report = Report()
    _init_worker(index)
    if jobs > 1 and len(batch) > 50:
        with ProcessPoolExecutor(
            max_workers=jobs, initializer=_init_worker, initargs=(index,)
        ) as pool:
            results = list(
                pool.map(process_file, batch, chunksize=_chunks(len(batch), jobs))
            )
    else:
        results = [process_file(job) for job in batch]
    for res in results:
        report.add(res)
        if apply and res.new_text is not None:
            (root / res.rel).write_bytes(res.new_text.encode("utf-8"))
    if apply and move:
        report.moved = move_tree(root)
    elif not apply:
        report.moved = pending_moves(root)
    report.elapsed_s = time.perf_counter() - started
    return report


__all__ = ["Job", "process_file", "run"]
