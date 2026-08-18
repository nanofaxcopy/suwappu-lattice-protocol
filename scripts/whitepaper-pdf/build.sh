#!/usr/bin/env bash
# Build the LTP whitepaper PDF from docs/WHITEPAPER.md.
#
# The Markdown in docs/ is the single source of truth; this produces the
# typeset artifact for preprint submission (IACR Cryptology ePrint Archive)
# and for anyone who would rather read a PDF.
#
#   scripts/whitepaper-pdf/build.sh [output.pdf]
#
# Requires: pandoc, lualatex (texlive-latex-recommended + -extra), and the
# DejaVu + TeX Gyre font families. On Debian/Ubuntu:
#   apt-get install pandoc texlive-latex-recommended texlive-latex-extra \
#                   texlive-fonts-recommended texlive-luatex fonts-dejavu
#
# Author/contact metadata below lands on page 1. ePrint does not accept
# anonymous submissions, so these must be real.
set -euo pipefail

# Pandoc decodes command-line arguments using the locale's encoding. Under a
# non-UTF-8 locale (containers commonly have none set at all) every non-ASCII
# byte in a -V value is mangled into U+FFFD — the em dashes in the title
# metadata below turn into replacement characters that then fail to typeset.
# Pin a UTF-8 locale rather than restricting the metadata to ASCII.
if [ -z "${LC_ALL:-}" ]; then
  if locale -a 2>/dev/null | grep -qix "C.utf8\|C.UTF-8"; then
    export LC_ALL=C.UTF-8
  elif locale -a 2>/dev/null | grep -qix "en_US.utf8\|en_US.UTF-8"; then
    export LC_ALL=en_US.UTF-8
  else
    echo "warning: no UTF-8 locale found; non-ASCII metadata may be mangled" >&2
  fi
fi
export LANG="${LANG:-${LC_ALL:-C}}"

cd "$(dirname "$0")/../.."

SRC="docs/WHITEPAPER.md"
OUT="${1:-build/whitepaper/ltp-whitepaper.pdf}"
WORK="build/whitepaper"

TITLE="LTP: Lattice Transfer Protocol"
SUBTITLE="A data transfer protocol in which no data payload is transmitted between sender and receiver"
AUTHOR="Jas Strokus"
AFFILIATION="Suwappu Labs"
CONTACT="layerinfinite@gmail.com"

# Version and date are read from the metadata table in the Markdown so the
# PDF can never disagree with the source.
VERSION="$(grep -oP '^\| *[A-Za-z .]+ *\| *\K[0-9]+\.[0-9]+\.[0-9]+[^ |]*' "$SRC" | head -1)"
DATE="$(grep -oP '^\| *[A-Za-z .]+ *\| *[0-9.]+[^|]*\| *\K[0-9]{4}-[0-9]{2}-[0-9]{2}' "$SRC" | head -1)"
STATUS="$(sed -n 's/^| *[A-Za-z .]* *| *[0-9.]*[^|]*| *[0-9-]* *| *\([^|]*\) *|.*/\1/p' "$SRC" | head -1 | xargs)"
: "${VERSION:?could not parse version from $SRC}"
: "${DATE:?could not parse date from $SRC}"

echo "==> LTP whitepaper PDF"
echo "    version ${VERSION}  date ${DATE}  status ${STATUS:-n/a}"

for tool in pandoc lualatex; do
  command -v "$tool" >/dev/null 2>&1 || { echo "error: $tool not found" >&2; exit 127; }
done

mkdir -p "$WORK" "$(dirname "$OUT")"

echo "==> [1/3] preprocessing markdown"
python3 scripts/whitepaper-pdf/preprocess.py \
  "$SRC" "$WORK/body.md" "$WORK/abstract.md"

echo "==> [2/3] rendering abstract"
pandoc "$WORK/abstract.md" -f markdown+tex_math_dollars+raw_tex-raw_html -t latex \
  -o "$WORK/abstract-body.tex"
{
  printf '\\begin{abstract}\n'
  cat "$WORK/abstract-body.tex"
  printf '\\end{abstract}\n'
} > "$WORK/abstract-block.tex"

# The running head needs the version before the preamble is read.
printf '\\newcommand{\\ltpversion}{%s}\n' "$VERSION" > "$WORK/version.tex"

echo "==> [3/3] pandoc + lualatex"
# `raw_tex` keeps the \textbf{} substitutions from the preprocessor intact;
# `-raw_html` drops the few inline <br>/<sub> tags rather than passing them
# through to LaTeX as literal text. `autolink_bare_uris` wraps every bare
# https://... reference URL in \url{}, which the loaded url/hyperref
# machinery can break at slashes — without it, a bibliography URL landing
# near a line's end has no valid break point and overflows the margin.
pandoc "$WORK/body.md" \
  -f markdown+pipe_tables+tex_math_dollars+raw_tex+autolink_bare_uris-raw_html \
  --pdf-engine=lualatex \
  --include-in-header="$WORK/version.tex" \
  --include-in-header=scripts/whitepaper-pdf/preamble.tex \
  --include-before-body="$WORK/abstract-block.tex" \
  --toc --toc-depth=3 \
  --highlight-style=tango \
  -V documentclass=article \
  -V fontsize=11pt \
  -V title="$TITLE" \
  -V subtitle="$SUBTITLE" \
  -V author="$AUTHOR \\\\ $AFFILIATION \\\\ \\texttt{$CONTACT}" \
  -V date="Version $VERSION — $DATE${STATUS:+ — $STATUS}" \
  -o "$OUT" 2>&1 | grep -viE "^\[WARNING\] (Could not convert TeX math|Duplicate)" || true

if [ ! -f "$OUT" ]; then
  echo "error: pandoc did not produce $OUT" >&2
  exit 1
fi

# Page count is cosmetic: poppler is not a build dependency, so never let a
# missing pdfinfo (or its non-zero exit) abort the build under `set -e`.
PAGES=""
if command -v pdfinfo >/dev/null 2>&1; then
  PAGES="$(pdfinfo "$OUT" 2>/dev/null | awk '/^Pages:/{print $2}')" || PAGES=""
fi
if [ -z "$PAGES" ]; then
  PAGES="$(python3 - "$OUT" <<'PY' 2>/dev/null || true
import re, sys, zlib
data = open(sys.argv[1], "rb").read()
# Uncompressed page objects first; fall back to inflating object streams,
# which is where a pandoc/lualatex PDF usually keeps them.
n = len(re.findall(rb"/Type\s*/Page[^sA-Za-z]", data))
if not n:
    for m in re.finditer(rb"stream\r?\n", data):
        chunk = data[m.end(): m.end() + 200000]
        try:
            n += len(re.findall(rb"/Type\s*/Page[^sA-Za-z]", zlib.decompress(chunk)))
        except Exception:
            pass
print(n or "")
PY
)"
fi
SIZE="$(du -h "$OUT" | cut -f1)"
echo
echo "OK — $OUT (${PAGES:-unknown} pages, $SIZE)"
