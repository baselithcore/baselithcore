"""Shell-metacharacter rejection shared by the local and remote executors.

Both surfaces build an argv vector and run it with ``shell=False``, so an
operator, a pipe or a substitution in the command text is never what the caller
meant — and on the SSH path it is worse than meaningless: ``ssh host "<cmd>"``
hands the whole string to the remote *login shell*, so an allowlist that only
inspects ``argv[0]`` lets ``ls; curl … | sh`` through.
"""

from __future__ import annotations

import shlex

SHELL_META_TOKENS: frozenset[str] = frozenset(
    {"|", "||", "&", "&&", ";", ">", ">>", "<", "<<", "(", ")", "$("}
)
SHELL_META_SUBSTRINGS: tuple[str, ...] = ("`",)

# Statement separators the lexer cannot surface: ``shlex`` treats them as plain
# whitespace, so ``ls\nrm -rf /`` lexes to ``['ls', 'rm', '-rf', '/']`` and an
# allowlist matching argv[0] sees only ``ls`` — while a shell runs both lines.
# They are therefore matched on the raw string, before any tokenization.
RAW_SEPARATORS: tuple[str, ...] = ("\n", "\r")


def lex_command(command: str) -> list[str]:
    """Tokenize ``command`` with shell operators as their own tokens.

    ``punctuation_chars=True`` keeps quoted content intact (a grep regex such
    as ``'foo|bar'`` stays one token) while splitting ``cmd;other`` — which
    plain ``shlex.split`` leaves glued — into separate tokens.

    Raises:
        ValueError: When the command cannot be lexed (unbalanced quotes).
    """
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    return list(lexer)


def find_shell_meta(command: str, tokens: list[str]) -> str | None:
    """Return the first shell metacharacter found, or ``None`` when clean.

    Checks the raw ``command`` for statement separators the lexer swallows,
    then the lexed ``tokens`` for operators and substitutions.
    """
    for separator in RAW_SEPARATORS:
        if separator in command:
            return separator
    for token in tokens:
        if token in SHELL_META_TOKENS or any(s in token for s in SHELL_META_SUBSTRINGS):
            return token
    return None


__all__ = [
    "RAW_SEPARATORS",
    "SHELL_META_SUBSTRINGS",
    "SHELL_META_TOKENS",
    "find_shell_meta",
    "lex_command",
]
