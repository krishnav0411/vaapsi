"""Kill switch — arm in-process + persist to .env (D5 Stage B).

Why persist at all: the in-process flag dies with the process, and an
operator who pressed KILL must not have a restart silently re-arm
outbound sends. ``activate()`` therefore does two things in order: flips
the live ``get_settings().kill_switch`` (the policy engine, the human
gate and the dashboard banner all read it per call, so the running
process is KILLED immediately), then upserts the single line
``VAAPSI_KILL_SWITCH=true`` into the repo ``.env`` so the next boot
starts denied too.

The .env edit is surgical by contract: only the kill-switch line is
appended or updated, every other line is preserved byte-for-byte, no
value is ever read back into code, and the file's contents are never
logged (secrets must not leak through an ops convenience). This module
is the one sanctioned .env writer in the repo — every other module
leaves that file alone.
"""

from __future__ import annotations

from pathlib import Path

from app.settings import BASE_DIR, get_settings

# Overridable so tests can redirect persistence away from the real .env.
ENV_PATH = BASE_DIR / ".env"
KILL_KEY = "VAAPSI_KILL_SWITCH"
KILL_VALUE = "true"


def activate(env_path: Path | None = None) -> str:
    """Arm the kill switch in-process and durably; return mode ``KILLED``.

    Idempotent: pressing Kill twice flips the flag twice and rewrites the
    same single env line — never a duplicate.
    """
    get_settings().kill_switch = True
    _upsert_env_flag(env_path if env_path is not None else ENV_PATH)
    return "KILLED"


def _upsert_env_flag(env_path: Path) -> None:
    """Append-or-update exactly one ``VAAPSI_KILL_SWITCH=true`` line.

    Existing lines (secrets included) are never parsed, reformatted or
    logged — the file is read only to find the one line to replace, and
    written back untouched otherwise. Missing file → created with the
    single flag line.
    """
    line = f"{KILL_KEY}={KILL_VALUE}"
    prefix = f"{KILL_KEY}="
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()
    else:
        lines = []
    for i, existing in enumerate(lines):
        if existing.strip().startswith(prefix):
            lines[i] = line
            break
    else:
        lines.append(line)
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
