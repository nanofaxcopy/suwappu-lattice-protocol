# Whitepaper PDF build

Typesets [`docs/WHITEPAPER.md`](../../docs/WHITEPAPER.md) into a preprint PDF.
The Markdown is the single source of truth; this produces the artifact for
submission to the [IACR Cryptology ePrint Archive](https://eprint.iacr.org/)
and for readers who want a paginated copy.

```bash
make docs-whitepaper          # or: just whitepaper
scripts/whitepaper-pdf/build.sh [output.pdf]
```

Output defaults to `build/whitepaper/ltp-whitepaper.pdf` (gitignored — it is a
release artifact, not source).

## Requirements

```bash
apt-get install pandoc texlive-latex-recommended texlive-latex-extra \
                texlive-fonts-recommended texlive-luatex fonts-texgyre \
                fonts-dejavu fonts-wqy-zenhei
```

## How it works

| File | Role |
|---|---|
| `preprocess.py` | Rewrites the Markdown for print: drops the centered HTML masthead and the hand-maintained link TOC, lifts the Abstract into its own file, and substitutes glyphs the text font lacks — **in prose only**, never inside fenced code blocks |
| `preamble.tex` | LaTeX preamble: fonts (by filename, so no fontconfig dependency), table and code-block handling, running head, hyperlinks |
| `build.sh` | Reads version/date/status out of the Markdown metadata table, runs pandoc + lualatex, reports pages |

The version, date and status on the title page are **parsed from the metadata
table in the Markdown**, so the PDF cannot disagree with the source.

## Notes for whoever maintains this

Four things here are load-bearing and easy to break:

1. **`LC_ALL` is pinned to a UTF-8 locale.** Pandoc decodes command-line
   arguments using the locale encoding. With no UTF-8 locale set — the default
   in most containers — every em dash in the title metadata becomes three
   U+FFFD replacement characters that then fail to typeset.
2. **Glyph substitution skips fenced code blocks.** The code blocks carry
   published interoperability test vectors; rewriting `d₀` to
   `d\textsubscript{0}` there would alter content implementers are meant to
   reproduce byte-for-byte. The monospace face has those glyphs anyway.
3. **No `--number-sections`.** The Markdown headings already carry their own
   numbers (`## 2.1 Phase 1: COMMIT`); letting LaTeX add a second set produces
   "0.1 2.1 Phase 1: COMMIT".
4. **`Highlighting` is redefined in `\AtBeginDocument`.** Pandoc defines that
   environment itself and overrides the global `\fvset`, which leaves the long
   pseudocode lines running past the margin.
5. **CJK text goes through `\ltpcjktext{...}`, never a bare `{\ltpcjk ...}`
   group.** Pandoc's `raw_tex` extension only passes `\command{...}` forms
   through unescaped; a bare `{...}` group has its braces escaped to `\{`
   `\}` in the LaTeX output, which strips the scope off the font switch and
   lets it bleed into every paragraph after it — silently, since the result
   still compiles, just with the wrong font applied three bibliography
   entries later. If you add another CJK run, route it through
   `preprocess.py`'s `CJK_RUN` substitution, not a hand-written brace group.
6. **`autolink_bare_uris` is required, not optional.** Without it, a bare
   `https://...` reference URL is typeset as ordinary justified text with no
   valid break point, and one landing near a line's end overflows the page
   margin by 50-100pt (very visible, unlike the two benign boxes below). The
   extension wraps every bare URL in `\url{}`, which the loaded
   `url`/`hyperref` machinery breaks at slashes.

A clean build reports 50-53 pages (grows as citations/sections are added), no
missing characters, and two overfull boxes (~5-10pt each — a CJK table cell
and one hyphenation, both invisible).
