"""The screen every file passes before it runs in the harness.

The code under test runs in the same interpreter as the harness's reporter, so the screen refuses
what could reach the reporter, the report line or pytest's machinery. It reads three views of a
file, so spelling a name another way does not get it through:

* the text, NFKC-normalised (Python folds identifiers the same way: ``exec`` in fullwidth
  letters is ``exec``) with its line continuations joined;
* the source as :func:`ast.unparse` writes it back: one canonical spacing, parenthesisation and
  string quoting, with escapes decoded and adjacent literals joined;
* every string the source builds from constants: ``"__ma" + "in__"``, ``"".join([...])``,
  ``chr(95)``, an f-string of literals.

A screen is a deny-list. It raises the cost of forging a report, it does not prove a file cannot
(a value computed at run time is out of its reach): the sandbox isolation and the security
review still apply to every result.
"""

from __future__ import annotations

import ast
import re
import unicodedata
from collections.abc import Iterable

#: What the code under test may not do or name. Each group closes a way to forge a green report:
_SCREENED = re.compile(
    # Read the run key (the interpreter's command line), end the process before the report or
    # print after it, or name the report line or the harness module.
    r"/proc\b|\borig_argv\b|\b_exit\b|\batexit\b|CODING_AGENT_REPORT|__main__"
    # Reach the harness's frames, code, globals or objects (the reporter and its counts).
    r"|_getframe|\bcurrentframe\b|\bf_back\b|\bf_locals\b|\bf_globals\b|\bf_builtins\b"
    r"|\btb_frame\b|\b(?:gi|cr|ag)_frame\b|__globals__|__code__|\bco_consts\b|__subclasses__"
    r"|\binspect\s*\.\s*(?:stack|trace|getouterframes|getinnerframes)\b"
    r"|\bget_referrers\b|\bget_referents\b|\bget_objects\b|\bctypes\b"
    r"|\bsys\s*\.\s*(?:modules|__dict__)\b|\b(?:getattr|vars)\s*\(\s*sys\b"
    # Run code the screen never read, or replace the process with another one.
    r"|__import__|\bimportlib\b|__builtins__|\b(?:import|from)\s+builtins\b"
    r"|\bexec\s*\(|\beval\s*\(|(?<![\w.])compile\s*\(|\bexec[lv]p?e?\b|\bspawn[lv]p?e?\b"
    r"|\bposix_spawnp?\b|\bfork(?:pty)?\s*\(|\bos\s*\.\s*(?:system|popen)\b"
    # Plug into pytest (a plugin or hook rewrites an outcome before the reporter counts it), or
    # rewrite the classes its outcomes are made of.
    r"|\bpluginmanager\b|\bpytest_plugins\b|\bdef\s+pytest_\w+|\bhookimpl\b|\bhookwrapper\b"
    r"|\b_pytest\b|\.\s*i?hook\b|\bpytest\s*\.\s*(?:Function|Item|Class|Module|Package|Session"
    r"|Config|Collector|File|Dir|Directory|TestReport|CollectReport|CallInfo|PytestPluginManager"
    r"|hookspec|main)\b"
)

#: The ``if __name__ == "__main__":`` statement, at the start of a line and nowhere else.
_MAIN_GUARD = re.compile(
    r"""^([ \t]*)if[ \t]+__name__[ \t]*==[ \t]*['"]__main__['"][ \t]*:""", re.MULTILINE
)
#: The longest string :func:`_fold` builds; past it a constant is not worth folding.
_FOLD_CHARS = 4096


def _fold(node: ast.AST) -> str | None:
    """The string ``node`` evaluates to when it is built from constants only, else None."""
    if isinstance(node, ast.Constant):
        if isinstance(node.value, str):
            return node.value
        if isinstance(node.value, bytes):
            return node.value.decode("latin-1")
        return None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _fold(node.left), _fold(node.right)
        if left is None or right is None or len(left) + len(right) > _FOLD_CHARS:
            return None
        return left + right
    if isinstance(node, ast.JoinedStr):
        parts = [
            _fold(v.value if isinstance(v, ast.FormattedValue) else v)
            for v in node.values
        ]
        return (
            None if any(p is None for p in parts) else "".join(p or "" for p in parts)
        )
    if isinstance(node, ast.Call) and len(node.args) == 1 and not node.keywords:
        func, arg = node.func, node.args[0]
        if (
            isinstance(func, ast.Name)
            and func.id == "chr"
            and isinstance(arg, ast.Constant)
            and isinstance(arg.value, int)
            and 0 <= arg.value <= 0x10FFFF
        ):
            return chr(arg.value)
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "join"
            and isinstance(arg, (ast.List, ast.Tuple))
        ):
            sep = _fold(func.value)
            items = [_fold(e) for e in arg.elts]
            if sep is None or any(i is None for i in items):
                return None
            return sep.join(i or "" for i in items)
    return None


def _views(text: str) -> Iterable[str]:
    """The three views of ``text`` the screen reads (see the module docstring)."""
    plain = unicodedata.normalize("NFKC", text).replace("\\\n", "")
    yield plain
    try:
        tree = ast.parse(plain)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return  # the run fails to collect it anyway; the plain text was read
    try:
        yield ast.unparse(tree)
    except (ValueError, RecursionError):
        pass
    folded = (_fold(node) for node in ast.walk(tree))
    yield "\n".join(f for f in folded if f)


def _found(text: str, *, main_guard: bool = False) -> str | None:
    """The first screened spelling in any view of ``text``, or None."""
    if main_guard:
        # The guard only reads ``__name__``: it becomes ``if True:``, which still parses.
        text = _MAIN_GUARD.sub(r"\1if True:", text)
    for view in _views(text):
        found = _SCREENED.search(view)
        if found:
            return found.group(0)
    return None


def screen(code: str, tests: str) -> str | None:
    """Why ``code`` or ``tests`` may not run in the harness, or None."""
    for label, text in (("code", code), ("tests", tests)):
        found = _found(text or "")
        if found:
            return (
                f"the {label} use {found!r}, which could tamper with the test report; write "
                "it without reaching the interpreter, the harness or pytest's internals"
            )
    return None


def screen_texts(texts: Iterable[str]) -> str | None:
    """:func:`screen` over many files; the ``if __name__ == "__main__":`` idiom is allowed.

    A repository file often ends with that guard; it only reads ``__name__``, so the guard
    statement (and nothing else that names ``__main__``) is taken out before the screen looks
    for anything that could reach the harness or its report.
    """
    for text in texts:
        found = _found(text or "", main_guard=True)
        if found:
            return (
                f"the files use {found!r}, which could tamper with the test report; write "
                "them without reaching the interpreter, the harness or pytest's internals"
            )
    return None


__all__ = ["screen", "screen_texts"]
