#!/usr/bin/env python3
"""Sync repository Markdown files into Notion pages.

Environment variables:
  NOTION_TOKEN: Notion internal integration token.
  NOTION_PARENT_PAGE_ID: Parent Notion page id that will contain synced pages.
  NOTION_REQUEST_TIMEOUT: Per-request timeout in seconds (default: 60).
  NOTION_MAX_RETRIES: Retries on timeout / rate limit / 5xx (default: 5).
  NOTION_RETRY_BACKOFF_SEC: Initial backoff between retries in seconds (default: 1.0).
  NOTION_SYNC_PROGRESS: Set to 0/false/no to disable progress logs (default: enabled).
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request


ROOT = pathlib.Path(__file__).resolve().parents[1]
NOTION_VERSION = "2022-06-28"
SYNC_MARK = "github-md-sync"
SKIP_DIRS = {".git", ".github", "scripts"}
REQUEST_TIMEOUT = int(os.environ.get("NOTION_REQUEST_TIMEOUT", "60"))
MAX_RETRIES = int(os.environ.get("NOTION_MAX_RETRIES", "5"))
RETRY_BACKOFF_SEC = float(os.environ.get("NOTION_RETRY_BACKOFF_SEC", "1.0"))
RETRYABLE_HTTP_CODES = frozenset({429, 500, 502, 503, 504})
# If a page has more than this many child blocks, archive it and create a fresh page
# instead of PATCH-archiving each block (avoids timeouts after splitting large docs).
RECREATE_CHILDREN_THRESHOLD = int(os.environ.get("NOTION_RECREATE_CHILDREN_THRESHOLD", "200"))
PROGRESS_ENABLED = os.environ.get("NOTION_SYNC_PROGRESS", "1").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}

# Notion code block languages (API rejects aliases like "text" from ```text fences).
NOTION_CODE_LANGUAGES = frozenset(
    {
        "abap",
        "abc",
        "agda",
        "arduino",
        "ascii art",
        "assembly",
        "bash",
        "basic",
        "bnf",
        "c",
        "c#",
        "c++",
        "clojure",
        "coffeescript",
        "coq",
        "css",
        "dart",
        "dhall",
        "diff",
        "docker",
        "ebnf",
        "elixir",
        "elm",
        "erlang",
        "f#",
        "flow",
        "fortran",
        "gherkin",
        "glsl",
        "go",
        "graphql",
        "groovy",
        "haskell",
        "hcl",
        "html",
        "idris",
        "java",
        "javascript",
        "json",
        "julia",
        "kotlin",
        "latex",
        "less",
        "lisp",
        "livescript",
        "llvm ir",
        "lua",
        "makefile",
        "markdown",
        "markup",
        "matlab",
        "mathematica",
        "mermaid",
        "nix",
        "notion formula",
        "objective-c",
        "ocaml",
        "pascal",
        "perl",
        "php",
        "plain text",
        "powershell",
        "prolog",
        "protobuf",
        "purescript",
        "python",
        "r",
        "racket",
        "reason",
        "ruby",
        "rust",
        "sass",
        "scala",
        "scheme",
        "scss",
        "shell",
        "smalltalk",
        "solidity",
        "sql",
        "swift",
        "toml",
        "typescript",
        "vb.net",
        "verilog",
        "vhdl",
        "visual basic",
        "webassembly",
        "xml",
        "yaml",
        "java/c/c++/c#",
    }
)

# Fenced code languages that become Notion equation blocks (KaTeX), not code blocks.
EQUATION_FENCE_LANGUAGES = frozenset({"latex", "tex", "math", "equation", "katex"})
# Notion equation.expression length limit (API docs do not state a cap; keep conservative).
MAX_EQUATION_EXPRESSION_LEN = 2000

MARKDOWN_CODE_LANGUAGE_ALIASES = {
    "": "plain text",
    "text": "plain text",
    "txt": "plain text",
    "plaintext": "plain text",
    "console": "plain text",
    "sh": "bash",
    "zsh": "bash",
    "js": "javascript",
    "node": "javascript",
    "nodejs": "javascript",
    "ts": "typescript",
    "py": "python",
    "yml": "yaml",
    "md": "markdown",
    "cpp": "c++",
    "cxx": "c++",
    "cc": "c++",
    "hpp": "c++",
    "cs": "c#",
    "csharp": "c#",
    "dockerfile": "docker",
    "hs": "haskell",
    "rb": "ruby",
    "rs": "rust",
    "golang": "go",
    "objc": "objective-c",
    "objectivec": "objective-c",
    "vb": "visual basic",
    "wasm": "webassembly",
    "tex": "latex",
    "ps1": "powershell",
    "pwsh": "powershell",
}


def normalize_notion_code_language(language: str) -> str:
    raw = language.strip().lower()
    if not raw:
        return "plain text"
    mapped = MARKDOWN_CODE_LANGUAGE_ALIASES.get(raw, raw)
    if mapped in NOTION_CODE_LANGUAGES:
        return mapped
    return "plain text"


def env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def normalize_notion_page_id(value: str) -> str:
    """Accept a UUID or a Notion page URL; return canonical 8-4-4-4-12 id for the API."""
    value = value.strip()
    uuid_match = re.search(
        r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
        value,
        re.IGNORECASE,
    )
    if uuid_match:
        return uuid_match.group(1).lower()

    bare = re.fullmatch(r"([0-9a-f]{32})", value, re.IGNORECASE)
    if bare:
        raw = bare.group(1).lower()
        return f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"

    if "notion.so" in value or value.startswith("http"):
        slug = value.split("?")[0].rstrip("/").rsplit("/", 1)[-1]
        hex_chars = re.sub(r"[^0-9a-fA-F]", "", slug)
        if len(hex_chars) >= 32:
            raw = hex_chars[-32:].lower()
            return f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"
        raise SystemExit(
            "NOTION_PARENT_PAGE_ID looks like a Notion URL but no 32-character page id "
            f"was found in: {value[:96]}..."
        )

    raise SystemExit(
        "NOTION_PARENT_PAGE_ID must be a Notion page UUID (with or without dashes) or a "
        f"full Notion page URL, got: {value[:96]}..."
    )


def page_id_key(page_id: str) -> str:
    return page_id.replace("-", "").lower()


_TOKEN: str | None = None
_PARENT_PAGE_ID: str | None = None


def notion_token() -> str:
    global _TOKEN
    if _TOKEN is None:
        _TOKEN = env("NOTION_TOKEN")
    return _TOKEN


def notion_parent_page_id() -> str:
    global _PARENT_PAGE_ID
    if _PARENT_PAGE_ID is None:
        _PARENT_PAGE_ID = normalize_notion_page_id(env("NOTION_PARENT_PAGE_ID"))
    return _PARENT_PAGE_ID


# title -> Notion page id for direct children of the parent page (filled each run).
CHILD_PAGE_INDEX: dict[str, str] = {}
# markdown relative path -> Notion page URL (filled each run).
INTERNAL_LINKS: dict[str, str] = {}


def format_duration(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h{minutes:02d}m{seconds:02d}s"
    if minutes:
        return f"{minutes}m{seconds:02d}s"
    return f"{seconds}s"


def log_progress(message: str) -> None:
    if not PROGRESS_ENABLED:
        return
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def progress_summary(done: int, total: int, started_at: float) -> str:
    elapsed = time.perf_counter() - started_at
    if done <= 0:
        return f"elapsed {format_duration(elapsed)}, ETA calculating"
    remaining = max(0, total - done)
    avg = elapsed / done
    eta = avg * remaining
    return (
        f"elapsed {format_duration(elapsed)}, avg {format_duration(avg)}/file, "
        f"ETA {format_duration(eta)}"
    )


def request(method: str, path: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    url = f"https://api.notion.com/v1{path}"
    headers = {
        "Authorization": f"Bearer {notion_token()}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }
    last_error: BaseException | None = None

    for attempt in range(MAX_RETRIES):
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as response:
                body = response.read().decode("utf-8")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            if exc.code in RETRYABLE_HTTP_CODES and attempt < MAX_RETRIES - 1:
                delay = RETRY_BACKOFF_SEC * (2**attempt)
                print(
                    f"Notion API {exc.code} {method} {path}, retry {attempt + 1}/{MAX_RETRIES} "
                    f"in {delay:.1f}s",
                    file=sys.stderr,
                )
                time.sleep(delay)
                last_error = exc
                continue
            raise RuntimeError(f"Notion API error {exc.code} {method} {path}: {detail}") from exc
        except (TimeoutError, urllib.error.URLError) as exc:
            if attempt < MAX_RETRIES - 1:
                delay = RETRY_BACKOFF_SEC * (2**attempt)
                print(
                    f"Notion API {type(exc).__name__} {method} {path}, "
                    f"retry {attempt + 1}/{MAX_RETRIES} in {delay:.1f}s",
                    file=sys.stderr,
                )
                time.sleep(delay)
                last_error = exc
                continue
            raise RuntimeError(
                f"Notion API request failed after {MAX_RETRIES} attempts "
                f"({method} {path}): {exc}"
            ) from exc

    raise RuntimeError(
        f"Notion API request failed after {MAX_RETRIES} attempts ({method} {path})"
    ) from last_error


# Rich text: links, $inline math$, **bold**, `math-ish backticks` (not code style).
RICH_SEGMENT_RE = re.compile(
    r"\[(?P<link_label>[^\]]+)\]\((?P<link_url>[^)]+)\)"
    r"|<(?P<autolink>https?://[^>\s]+)>"
    r"|(?P<autolink_bare>https?://[^\s)>,]+)"
    r"|\$(?P<math>[^$\n]+)\$"
    r"|\*\*(?P<bold>[^*]+)\*\*"
    r"|`(?P<backtick>[^`]+)`"
)

# Backtick content -> KaTeX for Notion inline equations (exact matches first).
BACKTICK_TO_LATEX: dict[str, str] = {
    "pi": r"\pi",
    "qdot": r"\dot{q}",
    "omega": r"\omega",
    "lambda": r"\lambda",
    "tau": r"\tau",
    "alpha": r"\alpha",
    "gamma": r"\gamma",
    "phi": r"\phi",
    "phi_bar": r"\bar{\phi}",
    "rho_tempo": r"\rho_{\mathrm{tempo}}",
    "g_0": r"g_0",
    "g_proj": r"g_{\mathrm{proj}}",
    "f_c": r"f_c",
    "f_theta": r"f_\theta",
    "q_default": r"q_{\mathrm{default}}",
    "action_scale": r"\alpha_{\mathrm{scale}}",
    "I_sec": r"I_{\mathrm{sec}}",
    "Delta t": r"\Delta t",
    "T_beat": r"T_{\mathrm{beat}}",
    "E_obs": r"E_{\mathrm{obs}}",
    "e_rms": r"e_{\mathrm{rms}}",
    "V^pi": r"V^{\pi}",
    "mu_t": r"\mu_t",
    "c_t": r"c_t",
    "s_t": r"s_t",
    "a_t": r"a_t",
    "r_t": r"r_t",
    "q^*": r"q^*",
    "q*": r"q^*",
    "K_p, K_d": r"K_p, K_d",
    "a≈0": r"a \approx 0",
    "gamma in (0,1]": r"\gamma \in (0,1]",
    "lambda in [0,1]": r"\lambda \in [0,1]",
    "phi = 0": r"\phi = 0",
    "phi = 0.5": r"\phi = 0.5",
    "phi -> 1": r"\phi \to 1",
    "P(s'|s,a)": r"P(s' \mid s,a)",
    "corr(E_obs, e_rms)": r"\mathrm{corr}(E_{\mathrm{obs}}, e_{\mathrm{rms}})",
    "phi, e_rms, onset": r"\phi, e_{\mathrm{rms}}, o",
    "g_proj, c_t, a_{t-1}": r"g_{\mathrm{proj}}, c_t, a_{t-1}",
    "pi, V": r"\pi, V",
    "gamma, lambda": r"\gamma, \lambda",
    "q, qdot": r"q, \dot{q}",
    "R, omega": r"R, \omega",
    "C": r"\mathcal{C}",
    "M": r"M",
    "b": r"b",
    "P": r"P",
    "R": r"R",
    "A": r"\mathcal{A}",
    "S": r"\mathcal{S}",
    "x[n]": r"x[n]",
    "f_s": r"f_s",
    "t_i^b": r"t_i^b",
    "t_k^b": r"t_k^b",
    "t_{k+1}^b": r"t_{k+1}^b",
    "t_{t+1}": r"t_{t+1}",
    "c_{t-K:t}": r"c_{t-K:t}",
    "o(t)": r"o(t)",
    "main": "",  # handled as non-math below
}

# Not math: keep as plain text (no grey code box in Notion).
BACKTICK_PLAIN_ONLY = frozenset(
    {
        "main",
        "latex",
        "text",
        "python",
        "bash",
        " ```latex ",
        "```latex",
    }
)

MATH_BACKTICK_RE = re.compile(
    r"^[a-zA-Z0-9_^{}\\=,\.\-\(\)\[\]≈∈/:\s|']+$"
)


def backtick_to_latex(content: str) -> str | None:
    raw = content.strip()
    if not raw or raw in BACKTICK_PLAIN_ONLY:
        return None
    if "http://" in raw or "https://" in raw or raw.startswith("```"):
        return None
    if raw in BACKTICK_TO_LATEX:
        mapped = BACKTICK_TO_LATEX[raw]
        return mapped if mapped else None
    if MATH_BACKTICK_RE.fullmatch(raw):
        return raw.replace("≈", r"\approx").replace("->", r"\to").replace("in (", r"\in (")
    return None


def inline_equation(expression: str) -> dict:
    return {"type": "equation", "equation": {"expression": expression.strip()}}


def text_segment(content: str, *, bold: bool = False) -> dict:
    part: dict = {"type": "text", "text": {"content": content}}
    if bold:
        part["annotations"] = {"bold": True}
    return part


def rich_text(text: str) -> list[dict]:
    text = text[:1900]
    if not text:
        return []

    parts: list[dict] = []
    cursor = 0
    for match in RICH_SEGMENT_RE.finditer(text):
        if match.start() > cursor:
            parts.append(text_segment(text[cursor : match.start()]))

        groups = match.groupdict()
        if groups.get("math") is not None:
            parts.append(inline_equation(groups["math"]))
        elif groups.get("backtick") is not None:
            latex = backtick_to_latex(groups["backtick"])
            if latex:
                parts.append(inline_equation(latex))
            else:
                parts.append(text_segment(groups["backtick"]))
        elif groups.get("bold") is not None:
            parts.append(text_segment(groups["bold"], bold=True))
        elif groups.get("link_label") is not None:
            label = groups["link_label"]
            target = groups["link_url"].strip()
            url = target if target.startswith(("http://", "https://")) else INTERNAL_LINKS.get(target)
            if url:
                parts.append(
                    {"type": "text", "text": {"content": label, "link": {"url": url}}}
                )
            else:
                parts.append(text_segment(label))
        elif groups.get("autolink") or groups.get("autolink_bare"):
            url = groups.get("autolink") or groups.get("autolink_bare")
            parts.append({"type": "text", "text": {"content": url, "link": {"url": url}}})
        cursor = match.end()

    if cursor < len(text):
        parts.append(text_segment(text[cursor:]))

    return parts


def paragraph(text: str) -> dict:
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": rich_text(text)}}


def heading(level: int, text: str) -> dict:
    block_type = {1: "heading_1", 2: "heading_2"}.get(level, "heading_3")
    return {"object": "block", "type": block_type, block_type: {"rich_text": rich_text(text)}}


def bullet(text: str) -> dict:
    return {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": rich_text(text)}}


def todo(text: str, checked: bool) -> dict:
    return {
        "object": "block",
        "type": "to_do",
        "to_do": {"rich_text": rich_text(text), "checked": checked},
    }


def code_block(lines: list[str], language: str = "plain text") -> dict:
    return {
        "object": "block",
        "type": "code",
        "code": {
            "rich_text": rich_text("\n".join(lines)),
            "language": normalize_notion_code_language(language),
        },
    }


def is_equation_fence(language: str) -> bool:
    return language.strip().lower() in EQUATION_FENCE_LANGUAGES


def latex_lines_to_expression(lines: list[str]) -> str:
    """Merge fenced LaTeX lines into one Notion/KaTeX expression."""
    parts: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("%"):
            continue
        parts.append(stripped)
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return r" \\ ".join(parts)


def equation_block(expression: str) -> dict:
    expression = expression.strip()
    if len(expression) > MAX_EQUATION_EXPRESSION_LEN:
        expression = expression[:MAX_EQUATION_EXPRESSION_LEN]
    return {
        "object": "block",
        "type": "equation",
        "equation": {"expression": expression},
    }


def equation_blocks_from_latex(lines: list[str]) -> list[dict]:
    """Build one or more equation blocks from a ```latex fenced region."""
    expr = latex_lines_to_expression(lines)
    if not expr:
        return [paragraph("(empty equation)")]

    if len(expr) <= MAX_EQUATION_EXPRESSION_LEN:
        return [equation_block(expr)]

    # Very long derivations: one Notion equation block per non-empty line.
    blocks: list[dict] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("%"):
            continue
        blocks.append(equation_block(stripped))
    return blocks or [equation_block(expr[:MAX_EQUATION_EXPRESSION_LEN])]


def is_table_line(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 2


def parse_table_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def is_table_separator(cells: list[str]) -> bool:
    return all(re.fullmatch(r":?-{3,}:?", cell.strip()) for cell in cells)


def table_block(lines: list[str]) -> dict:
    rows = [parse_table_row(line) for line in lines]
    rows = [row for row in rows if not is_table_separator(row)]
    width = max((len(row) for row in rows), default=1)

    children = []
    for row in rows:
        padded = row + [""] * (width - len(row))
        children.append(
            {
                "object": "block",
                "type": "table_row",
                "table_row": {"cells": [rich_text(cell) for cell in padded]},
            }
        )

    return {
        "object": "block",
        "type": "table",
        "table": {
            "table_width": width,
            "has_column_header": bool(rows),
            "has_row_header": False,
            "children": children,
        },
    }


def markdown_to_blocks(markdown: str) -> list[dict]:
    blocks: list[dict] = [paragraph(f"{SYNC_MARK}: synced from GitHub Markdown.")]
    pending: list[str] = []
    in_code = False
    code_lines: list[str] = []
    code_language = "plain text"
    fence_language_raw = ""
    lines = markdown.splitlines()

    def flush_pending() -> None:
        if pending:
            blocks.append(paragraph(" ".join(pending).strip()))
            pending.clear()

    index = 0
    while index < len(lines):
        raw_line = lines[index]
        line = raw_line.rstrip()

        if line.startswith("```"):
            if in_code:
                if is_equation_fence(fence_language_raw):
                    blocks.extend(equation_blocks_from_latex(code_lines))
                else:
                    blocks.append(code_block(code_lines, code_language))
                in_code = False
                code_lines = []
                code_language = "plain text"
                fence_language_raw = ""
            else:
                flush_pending()
                in_code = True
                fence_language_raw = line[3:].strip().lower()
                code_language = normalize_notion_code_language(fence_language_raw)
            index += 1
            continue

        if in_code:
            code_lines.append(line)
            index += 1
            continue

        if not line.strip():
            flush_pending()
            index += 1
            continue

        if is_table_line(line):
            flush_pending()
            table_lines = []
            while index < len(lines) and is_table_line(lines[index].rstrip()):
                table_lines.append(lines[index].rstrip())
                index += 1
            blocks.append(table_block(table_lines))
            continue

        match = re.match(r"^(#{1,3})\s+(.+)$", line)
        if match:
            flush_pending()
            blocks.append(heading(len(match.group(1)), match.group(2)))
            index += 1
            continue

        match = re.match(r"^-\s+\[( |x|X)\]\s+(.+)$", line)
        if match:
            flush_pending()
            blocks.append(todo(match.group(2), match.group(1).lower() == "x"))
            index += 1
            continue

        if line.startswith("- "):
            flush_pending()
            blocks.append(bullet(line[2:]))
            index += 1
            continue

        pending.append(line)
        index += 1

    flush_pending()
    if in_code:
        if is_equation_fence(fence_language_raw):
            blocks.extend(equation_blocks_from_latex(code_lines))
        else:
            blocks.append(code_block(code_lines, code_language))
    return blocks


def chunks(items: list[dict], size: int = 100):
    for index in range(0, len(items), size):
        yield items[index : index + size]


def page_title(path: pathlib.Path) -> str:
    if path.name == "README.md" and path.parent == ROOT:
        return "Rhythm Robo DJ 设计总览"
    rel = path.relative_to(ROOT).as_posix()
    return rel.replace("/", " / ").removesuffix(".md").replace("README", "说明")


def notion_page_url(page_id: str) -> str:
    return f"https://www.notion.so/{page_id_key(page_id)}"


def page_object_title(page: dict) -> str:
    props = page.get("properties", {})
    title_prop = props.get("title") or props.get("Name") or {}
    return "".join(part.get("plain_text", "") for part in title_prop.get("title", []))


def list_pages_under_parent() -> dict[str, list[str]]:
    """Collect direct child pages by title (may contain duplicates from older sync runs)."""
    by_title: dict[str, list[str]] = {}

    for entry in parent_child_pages():
        title = entry["title"]
        if title:
            by_title.setdefault(title, []).append(entry["id"])

    cursor = None
    while True:
        payload: dict = {"filter": {"value": "page", "property": "object"}, "page_size": 100}
        if cursor:
            payload["start_cursor"] = cursor
        result = request("POST", "/search", payload)
        for item in result.get("results", []):
            parent = item.get("parent", {})
            if parent.get("type") != "page_id":
                continue
            if page_id_key(parent.get("page_id", "")) != page_id_key(notion_parent_page_id()):
                continue
            title = page_object_title(item)
            if not title:
                continue
            page_id = item["id"]
            ids = by_title.setdefault(title, [])
            if page_id_key(page_id) not in {page_id_key(existing) for existing in ids}:
                ids.append(page_id)
        if not result.get("has_more"):
            break
        cursor = result.get("next_cursor")

    return by_title


def choose_canonical_page_id(title: str, page_ids: list[str]) -> str:
    if len(page_ids) == 1:
        return page_ids[0]
    with_mark = [pid for pid in page_ids if page_has_sync_mark(pid)]
    if with_mark:
        return with_mark[0]
    return page_ids[0]


def archive_page(page_id: str) -> None:
    request("PATCH", f"/pages/{page_id}", {"archived": True})


def rebuild_child_page_index() -> None:
    """Map each title to one child page; archive duplicate titles from previous runs."""
    global CHILD_PAGE_INDEX
    started_at = time.perf_counter()
    log_progress("Scanning existing Notion child pages...")
    CHILD_PAGE_INDEX = {}
    by_title = list_pages_under_parent()

    archived = 0
    for title, page_ids in by_title.items():
        canonical = choose_canonical_page_id(title, page_ids)
        CHILD_PAGE_INDEX[title] = canonical
        for duplicate_id in page_ids:
            if page_id_key(duplicate_id) == page_id_key(canonical):
                continue
            archive_page(duplicate_id)
            archived += 1
            print(f"Archived duplicate Notion page: {title}")
            time.sleep(0.1)
    log_progress(
        "Page index ready: "
        f"{len(CHILD_PAGE_INDEX)} titles, {archived} duplicates archived, "
        f"took {format_duration(time.perf_counter() - started_at)}."
    )


def find_existing_page(title: str) -> str | None:
    page_id = CHILD_PAGE_INDEX.get(title)
    return page_id if page_id else None


def count_block_children(page_id: str) -> int:
    total = 0
    cursor = None
    while True:
        suffix = f"?start_cursor={cursor}" if cursor else ""
        result = request("GET", f"/blocks/{page_id}/children?page_size=100{suffix}")
        total += len(result.get("results", []))
        if not result.get("has_more"):
            break
        cursor = result.get("next_cursor")
    return total


def clear_children(page_id: str) -> None:
    cursor = None
    cleared = 0
    while True:
        suffix = f"?start_cursor={cursor}" if cursor else ""
        result = request("GET", f"/blocks/{page_id}/children?page_size=100{suffix}")
        children = result.get("results", [])
        if not children:
            break
        for child in children:
            request("PATCH", f"/blocks/{child['id']}", {"archived": True})
            cleared += 1
            if cleared % 20 == 0:
                time.sleep(0.05)
        if not result.get("has_more"):
            break
        cursor = result.get("next_cursor")


def prepare_page_for_sync(page_id: str | None, title: str, new_block_count: int) -> str:
    """Return a Notion page id ready for new children (create, clear, or recreate)."""
    if not page_id:
        return create_page(title)

    old_count = count_block_children(page_id)
    if old_count > RECREATE_CHILDREN_THRESHOLD and new_block_count < max(
        80, old_count // 3
    ):
        log_progress(
            f"Recreating Notion page {title}: had {old_count} blocks, "
            f"uploading {new_block_count} (faster than clearing in place)."
        )
        archive_page(page_id)
        return create_page(title)

    if old_count:
        clear_children(page_id)
    return page_id


def create_page(title: str) -> str:
    payload = {
        "parent": {"type": "page_id", "page_id": notion_parent_page_id()},
        "properties": {"title": {"title": rich_text(title)}},
    }
    page_id = request("POST", "/pages", payload)["id"]
    CHILD_PAGE_INDEX[title] = page_id
    return page_id


def ensure_pages(files: list[pathlib.Path]) -> None:
    started_at = time.perf_counter()
    created = 0
    log_progress(f"Ensuring {len(files)} Notion pages exist...")
    for path in files:
        title = page_title(path)
        if title not in CHILD_PAGE_INDEX:
            create_page(title)
            created += 1
            print(f"Created Notion page: {title}")
            time.sleep(0.1)
    log_progress(
        f"Page ensure complete: {created} created, "
        f"took {format_duration(time.perf_counter() - started_at)}."
    )


def rebuild_internal_links(files: list[pathlib.Path]) -> None:
    INTERNAL_LINKS.clear()
    for path in files:
        title = page_title(path)
        page_id = CHILD_PAGE_INDEX.get(title)
        if not page_id:
            continue
        rel = path.relative_to(ROOT).as_posix()
        url = notion_page_url(page_id)
        INTERNAL_LINKS[rel] = url
        INTERNAL_LINKS[f"./{rel}"] = url


def block_plain_text(block: dict) -> str:
    block_type = block.get("type")
    if not block_type:
        return ""
    rich = block.get(block_type, {}).get("rich_text", [])
    return "".join(part.get("plain_text", "") for part in rich)


def page_has_sync_mark(page_id: str) -> bool:
    result = request("GET", f"/blocks/{page_id}/children?page_size=3")
    for child in result.get("results", []):
        if block_plain_text(child).startswith(SYNC_MARK):
            return True
    return False


def parent_child_pages() -> list[dict]:
    pages = []
    cursor = None
    while True:
        suffix = f"?start_cursor={cursor}" if cursor else ""
        result = request("GET", f"/blocks/{notion_parent_page_id()}/children{suffix}")
        for child in result.get("results", []):
            if child.get("type") == "child_page":
                pages.append(
                    {
                        "id": child["id"],
                        "title": child.get("child_page", {}).get("title", ""),
                    }
                )
        if not result.get("has_more"):
            break
        cursor = result.get("next_cursor")
    return pages


def archive_stale_synced_pages(active_titles: set[str]) -> None:
    started_at = time.perf_counter()
    archived = 0
    log_progress("Checking for stale synced Notion pages...")
    for page in parent_child_pages():
        title = page["title"]
        page_id = page["id"]
        if title in active_titles:
            continue
        if not page_has_sync_mark(page_id):
            continue
        request("PATCH", f"/pages/{page_id}", {"archived": True})
        archived += 1
        print(f"Archived stale Notion page: {title}")
        time.sleep(0.1)
    log_progress(
        f"Stale page check complete: {archived} archived, "
        f"took {format_duration(time.perf_counter() - started_at)}."
    )


def sync_file(
    path: pathlib.Path,
    blocks: list[dict],
    *,
    index: int,
    total: int,
    sync_started_at: float,
) -> None:
    file_started_at = time.perf_counter()
    title = page_title(path)
    page_id = find_existing_page(title)
    rel = path.relative_to(ROOT)
    block_count = len(blocks)
    chunk_count = (block_count + 99) // 100
    log_progress(
        f"[{index}/{total}] Syncing {rel} -> {title} "
        f"({block_count} blocks, {chunk_count} API append batches)"
    )
    log_progress(f"[{index}/{total}] Preparing Notion page for {title}...")
    page_id = prepare_page_for_sync(page_id, title, block_count)

    for batch_index, group in enumerate(chunks(blocks), start=1):
        request("PATCH", f"/blocks/{page_id}/children", {"children": group})
        log_progress(
            f"[{index}/{total}] Appended batch {batch_index}/{chunk_count} "
            f"for {title}"
        )
        time.sleep(0.1)
    file_elapsed = time.perf_counter() - file_started_at
    log_progress(
        f"[{index}/{total}] Done {rel} in {format_duration(file_elapsed)}; "
        f"{progress_summary(index, total, sync_started_at)}."
    )
    print(f"Synced {rel} -> {title}")


def markdown_files() -> list[pathlib.Path]:
    files = []
    for path in ROOT.rglob("*.md"):
        rel_parts = path.relative_to(ROOT).parts
        if rel_parts[0] in SKIP_DIRS:
            continue
        files.append(path)
    return sorted(files)


def build_sync_items(files: list[pathlib.Path]) -> list[tuple[pathlib.Path, list[dict]]]:
    started_at = time.perf_counter()
    log_progress(f"Parsing {len(files)} Markdown files before upload...")
    items: list[tuple[pathlib.Path, list[dict]]] = []
    total_blocks = 0
    total_equations = 0
    for index, path in enumerate(files, start=1):
        blocks = markdown_to_blocks(path.read_text(encoding="utf-8"))
        items.append((path, blocks))
        total_blocks += len(blocks)
        total_equations += sum(1 for block in blocks if block.get("type") == "equation")
        log_progress(
            f"[parse {index}/{len(files)}] {path.relative_to(ROOT)}: "
            f"{len(blocks)} blocks"
        )
    total_batches = sum((len(blocks) + 99) // 100 for _, blocks in items)
    log_progress(
        "Markdown parse complete: "
        f"{total_blocks} blocks, {total_equations} equation blocks, "
        f"{total_batches} API append batches, "
        f"took {format_duration(time.perf_counter() - started_at)}."
    )
    return items


def main() -> int:
    started_at = time.perf_counter()
    files = markdown_files()
    log_progress(f"Starting Notion sync for {len(files)} Markdown files.")
    active_titles = {page_title(path) for path in files}
    rebuild_child_page_index()
    archive_stale_synced_pages(active_titles)
    ensure_pages(files)
    rebuild_internal_links(files)
    sync_items = build_sync_items(files)
    sync_started_at = time.perf_counter()
    log_progress(f"Uploading {len(sync_items)} files to Notion...")
    for index, (path, blocks) in enumerate(sync_items, start=1):
        sync_file(
            path,
            blocks,
            index=index,
            total=len(sync_items),
            sync_started_at=sync_started_at,
        )
    log_progress(
        f"Notion sync complete in {format_duration(time.perf_counter() - started_at)}."
    )
    return 0


def _test_rich_text() -> None:
    parts = rich_text("- `pi`：策略，即控制律。$c_t$ 与 **闭环**。")
    types = [p["type"] for p in parts]
    assert types.count("equation") >= 2, types
    assert any(p["type"] == "text" and p.get("annotations", {}).get("bold") for p in parts)
    pi_eq = next(p for p in parts if p["type"] == "equation" and "\\pi" in p["equation"]["expression"])
    assert pi_eq
    print("rich_text inline equation test OK")


def _test_markdown_to_blocks() -> None:
    """Smoke-test markdown parsing (no Notion API). Run: python3 scripts/sync_notion.py --test-markdown"""
    _test_rich_text()
    sample = """### 1.2 带约束

- `pi`：策略

```latex
\\max_{\\pi} \\; \\mathbb{E}\\left[ \\sum_{t=0}^{T} \\gamma^t r(s_t,a_t,s_{t+1},c_t) \\right]
\\quad \\text{s.t.} \\quad (s_t,a_t) \\in \\mathcal{C}
```

```python
print("not equation")
```
"""
    blocks = markdown_to_blocks(sample)
    types = [b["type"] for b in blocks]
    assert "equation" in types, types
    eq = next(b for b in blocks if b["type"] == "equation")
    assert "\\max" in eq["equation"]["expression"]
    assert any(b["type"] == "code" for b in blocks)
    bullet = next(b for b in blocks if b["type"] == "bulleted_list_item")
    bullet_parts = bullet["bulleted_list_item"]["rich_text"]
    assert any(p["type"] == "equation" for p in bullet_parts)
    print("markdown_to_blocks equation test OK")


if __name__ == "__main__":
    if "--test-markdown" in sys.argv:
        _test_markdown_to_blocks()
        sys.exit(0)
    sys.exit(main())
