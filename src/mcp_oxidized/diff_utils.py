import difflib
from typing import Optional


def _split_lines(text: str) -> list:
    return text.splitlines()


def unified_diff(
    old_text: str,
    new_text: str,
    old_label: str = "old",
    new_label: str = "new",
    context_lines: int = 3,
) -> str:
    """Return a unified diff string between two config texts."""
    old_lines = _split_lines(old_text)
    new_lines = _split_lines(new_text)
    diff = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=old_label,
        tofile=new_label,
        lineterm="",
        n=context_lines,
    )
    return "\n".join(diff)


def inline_diff(
    ref_text: str,
    current_text: str,
    context_lines: int = 0,
) -> str:
    """
    Return the current config with changed lines annotated inline.
    Lines prefixed with:
      [+] line present in current but not in ref
      [-] line present in ref but not in current
      [ ] line unchanged
    When context_lines > 0, only changed lines and their neighbours are shown.
    """
    ref_lines = _split_lines(ref_text)
    cur_lines = _split_lines(current_text)
    matcher = difflib.SequenceMatcher(None, ref_lines, cur_lines, autojunk=False)
    result = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            if context_lines == 0:
                for line in cur_lines[j1:j2]:
                    result.append(f"[ ] {line}")
            else:
                block = cur_lines[j1:j2]
                n = len(block)
                # keep up to context_lines at start and end of equal block
                keep_start = min(context_lines, n)
                keep_end = min(context_lines, n)
                if keep_start + keep_end >= n:
                    for line in block:
                        result.append(f"[ ] {line}")
                else:
                    for line in block[:keep_start]:
                        result.append(f"[ ] {line}")
                    result.append("...")
                    for line in block[n - keep_end:]:
                        result.append(f"[ ] {line}")
        elif tag in ("replace", "insert", "delete"):
            for line in ref_lines[i1:i2]:
                result.append(f"[-] {line}")
            for line in cur_lines[j1:j2]:
                result.append(f"[+] {line}")

    return "\n".join(result)


def blame_annotate(
    config_text: str,
    versions: list,
) -> str:
    """
    Annotate each line of config_text with the version (commit number and date)
    that last introduced it, by scanning version history from oldest to newest.

    versions: list of dicts from Oxidized API, each with keys 'oid', 'date', 'message'.
              Expected to be ordered newest-first (as returned by Oxidized).
    """
    current_lines = _split_lines(config_text)
    n = len(current_lines)
    # blame[i] = label string for line i
    blame = ["unknown"] * n

    # Work from oldest to newest version to find when each line was last changed.
    ordered = list(reversed(versions))  # oldest first

    prev_lines: Optional[list] = None

    for idx, ver in enumerate(ordered):
        label = f"v{idx + 1} {ver.get('date', '')[:10]}"
        ver_lines = ver.get("_config_lines")
        if ver_lines is None:
            prev_lines = None
            continue

        if prev_lines is None:
            # All lines in this (oldest) version get this label
            for i, line in enumerate(current_lines):
                if line in ver_lines:
                    blame[i] = label
        else:
            # Find lines added or changed relative to previous version
            added = set(ver_lines) - set(prev_lines)
            for i, line in enumerate(current_lines):
                if line in added:
                    blame[i] = label

        prev_lines = ver_lines

    # Build annotated output
    result = []
    for i, line in enumerate(current_lines):
        result.append(f"[{blame[i]}] {line}")
    return "\n".join(result)
