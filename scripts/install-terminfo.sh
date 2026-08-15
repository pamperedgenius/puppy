#!/usr/bin/env bash
# Compiles and installs puppy's terminfo entry into the user's own terminfo
# database (~/.terminfo, via `tic`'s default search-order behavior -- no sudo,
# no touching /usr/share/terminfo). Safe to re-run; `tic` overwrites just the
# one compiled entry, nothing else in the database.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
src="$repo_root/terminfo/puppy.terminfo"

if [[ ! -f "$src" ]]; then
    echo "error: $src not found" >&2
    exit 1
fi

tic -x "$src"
echo "Installed. Verify with: TERM=puppy infocmp puppy"
