"""Turn the Markdown the models write (**bold**, # headings, lists, `code`) into styled
pieces of text for the chat window, so it shows formatting instead of the raw marks."""

import re

INLINE = re.compile(r"\*\*(.+?)\*\*|__(.+?)__|`([^`]+)`|(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])")
HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
BULLET = re.compile(r"^(\s*)[*+-]\s+(.*)$")
RULE = re.compile(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$")


def inline_runs(text, base=()):
    """'Use **MPI** now' -> [('Use ', ()), ('MPI', ('bold',)), (' now', ())]"""
    runs, position = [], 0
    for match in INLINE.finditer(text):
        if match.start() > position:
            runs.append((text[position:match.start()], tuple(base)))
        bold, bold2, code, italic = match.groups()
        if code is not None:
            runs.append((code, tuple(base) + ("code",)))
        elif italic is not None:
            runs.append((italic, tuple(base) + ("italic",)))
        else:
            runs.append((bold or bold2, tuple(base) + ("bold",)))
        position = match.end()
    if position < len(text):
        runs.append((text[position:], tuple(base)))
    return runs


def markdown_runs(text):
    """Split Markdown text into (text, styles) pieces. Styles: bold, italic, code, heading."""
    runs = []
    in_code_block = False
    lines = text.split("\n")
    for number, line in enumerate(lines):
        end = "\n" if number < len(lines) - 1 else ""
        if line.strip().startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            runs.append((line + end, ("code",)))
            continue
        heading = HEADING.match(line)
        bullet = BULLET.match(line)
        if heading:
            runs += inline_runs(heading.group(2), ("heading",))
        elif RULE.match(line):
            runs.append(("─" * 24, ()))
        elif bullet:
            indent = "    " * (len(bullet.group(1).expandtabs(4)) // 2)
            runs.append((f"{indent}• ", ()))
            runs += inline_runs(bullet.group(2))
        else:
            runs += inline_runs(line)
        if end:
            runs.append((end, ()))
    return [(piece, styles) for piece, styles in runs if piece]


def plain_text(text):
    """The same text without any Markdown marks (for places that can't show styles)."""
    return "".join(piece for piece, _ in markdown_runs(text))
