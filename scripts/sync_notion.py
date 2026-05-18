#!/usr/bin/env python3
"""Sync repository Markdown files into Notion pages.

Environment variables:
  NOTION_TOKEN: Notion internal integration token.
  NOTION_PARENT_PAGE_ID: Parent Notion page id that will contain synced pages.
  NOTION_REQUEST_TIMEOUT: Per-request timeout in seconds (default: 60).
  NOTION_MAX_RETRIES: Retries on timeout / rate limit / 5xx (default: 5).
  NOTION_RETRY_BACKOFF_SEC: Initial backoff between retries in seconds (default: 1.0).
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


TOKEN = env("NOTION_TOKEN")
PARENT_PAGE_ID = normalize_notion_page_id(env("NOTION_PARENT_PAGE_ID"))

# title -> Notion page id for direct children of PARENT_PAGE_ID (filled each run).
CHILD_PAGE_INDEX: dict[str, str] = {}
# markdown relative path -> Notion page URL (filled each run).
INTERNAL_LINKS: dict[str, str] = {}


def request(method: str, path: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    url = f"https://api.notion.com/v1{path}"
    headers = {
        "Authorization": f"Bearer {TOKEN}",
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


LINK_PATTERN = re.compile(r"\[([^\]]+)\]\(([^)]+)\)|<(https?://[^>\s]+)>|(https?://[^\s)>,]+)")


def rich_text(text: str) -> list[dict]:
    text = text[:1900]
    if not text:
        return []

    parts: list[dict] = []
    cursor = 0
    for match in LINK_PATTERN.finditer(text):
        if match.start() > cursor:
            parts.append({"type": "text", "text": {"content": text[cursor : match.start()]}})

        if match.group(1):
            label = match.group(1)
            target = match.group(2).strip()
            url = target if target.startswith(("http://", "https://")) else INTERNAL_LINKS.get(target)
        else:
            url = match.group(3) or match.group(4)
            label = url

        if url:
            parts.append({"type": "text", "text": {"content": label, "link": {"url": url}}})
        else:
            parts.append({"type": "text", "text": {"content": label}})
        cursor = match.end()

    if cursor < len(text):
        parts.append({"type": "text", "text": {"content": text[cursor:]}})

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
                blocks.append(code_block(code_lines, code_language))
                in_code = False
                code_lines = []
                code_language = "plain text"
            else:
                flush_pending()
                in_code = True
                code_language = normalize_notion_code_language(line[3:].strip())
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
            if page_id_key(parent.get("page_id", "")) != page_id_key(PARENT_PAGE_ID):
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
    CHILD_PAGE_INDEX = {}
    by_title = list_pages_under_parent()

    for title, page_ids in by_title.items():
        canonical = choose_canonical_page_id(title, page_ids)
        CHILD_PAGE_INDEX[title] = canonical
        for duplicate_id in page_ids:
            if page_id_key(duplicate_id) == page_id_key(canonical):
                continue
            archive_page(duplicate_id)
            print(f"Archived duplicate Notion page: {title}")
            time.sleep(0.1)


def find_existing_page(title: str) -> str | None:
    page_id = CHILD_PAGE_INDEX.get(title)
    return page_id if page_id else None


def clear_children(page_id: str) -> None:
    cursor = None
    while True:
        suffix = f"?start_cursor={cursor}" if cursor else ""
        result = request("GET", f"/blocks/{page_id}/children{suffix}")
        for child in result.get("results", []):
            request("PATCH", f"/blocks/{child['id']}", {"archived": True})
            time.sleep(0.1)
        if not result.get("has_more"):
            break
        cursor = result.get("next_cursor")


def create_page(title: str) -> str:
    payload = {
        "parent": {"type": "page_id", "page_id": PARENT_PAGE_ID},
        "properties": {"title": {"title": rich_text(title)}},
    }
    page_id = request("POST", "/pages", payload)["id"]
    CHILD_PAGE_INDEX[title] = page_id
    return page_id


def ensure_pages(files: list[pathlib.Path]) -> None:
    for path in files:
        title = page_title(path)
        if title not in CHILD_PAGE_INDEX:
            create_page(title)
            print(f"Created Notion page: {title}")
            time.sleep(0.1)


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
        result = request("GET", f"/blocks/{PARENT_PAGE_ID}/children{suffix}")
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
    for page in parent_child_pages():
        title = page["title"]
        page_id = page["id"]
        if title in active_titles:
            continue
        if not page_has_sync_mark(page_id):
            continue
        request("PATCH", f"/pages/{page_id}", {"archived": True})
        print(f"Archived stale Notion page: {title}")
        time.sleep(0.1)


def sync_file(path: pathlib.Path) -> None:
    title = page_title(path)
    page_id = find_existing_page(title)
    if page_id:
        clear_children(page_id)
    else:
        page_id = create_page(title)

    blocks = markdown_to_blocks(path.read_text(encoding="utf-8"))
    for group in chunks(blocks):
        request("PATCH", f"/blocks/{page_id}/children", {"children": group})
        time.sleep(0.1)
    print(f"Synced {path.relative_to(ROOT)} -> {title}")


def markdown_files() -> list[pathlib.Path]:
    files = []
    for path in ROOT.rglob("*.md"):
        rel_parts = path.relative_to(ROOT).parts
        if rel_parts[0] in SKIP_DIRS:
            continue
        files.append(path)
    return sorted(files)


def main() -> int:
    files = markdown_files()
    active_titles = {page_title(path) for path in files}
    rebuild_child_page_index()
    archive_stale_synced_pages(active_titles)
    ensure_pages(files)
    rebuild_internal_links(files)
    for path in files:
        sync_file(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
