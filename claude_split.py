#!/usr/bin/env python3
"""List and export individual conversations from a Claude data export."""

from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


Conversation = dict[str, Any]


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="claude_split",
        description="List and export individual chats from a Claude conversations.json or export ZIP.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List conversations in an export.")
    add_common_input_args(list_parser)
    list_parser.add_argument("--search", help="Case-insensitive search across title and messages.")
    list_parser.add_argument("--title", help="Case-insensitive title filter.")
    list_parser.add_argument("--limit", type=int, default=100, help="Maximum rows to print. Default: 100.")
    list_parser.add_argument(
        "--sort",
        default="index-asc",
        choices=(
            "index-asc",
            "index-desc",
            "date-asc",
            "date-desc",
            "title-asc",
            "title-desc",
            "messages-asc",
            "messages-desc",
        ),
        help="Sort rows before applying --limit. Default: index-asc.",
    )
    list_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON lines.")
    list_parser.set_defaults(func=run_list)

    export_parser = subparsers.add_parser("export", help="Export one conversation to Markdown or JSON.")
    add_common_input_args(export_parser)
    selector = export_parser.add_mutually_exclusive_group(required=True)
    selector.add_argument("--id", help="Conversation UUID/id shown by the list command.")
    selector.add_argument("--index", type=int, help="Zero-based index shown by the list command.")
    selector.add_argument("--title", help="Exact case-insensitive title to export.")
    selector.add_argument("--search", help="Export the only conversation matching this search.")
    export_parser.add_argument("-o", "--out", default="exports", help="Output file or directory. Default: exports.")
    export_parser.add_argument("--format", choices=("md", "json"), default="md", help="Output format. Default: md.")
    export_parser.add_argument("--overwrite", action="store_true", help="Overwrite an existing output file.")
    export_parser.set_defaults(func=run_export)

    args = parser.parse_args()
    try:
        return args.func(args)
    except CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def add_common_input_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("input", help="Path to Claude export ZIP or conversations.json.")


def run_list(args: argparse.Namespace) -> int:
    conversations = sorted_conversations(filtered_conversations(load_conversations(Path(args.input)), args), args.sort)
    if args.json:
        for index, conversation in conversations[: args.limit]:
            print(json.dumps(summary(index, conversation), ensure_ascii=False))
        return 0

    rows = [summary(index, conversation) for index, conversation in conversations[: args.limit]]
    if not rows:
        print("No conversations matched.")
        return 0

    print(f"{'idx':>5}  {'date':<10}  {'messages':>8}  {'id':<36}  title")
    print(f"{'-' * 5}  {'-' * 10}  {'-' * 8}  {'-' * 36}  {'-' * 40}")
    for row in rows:
        print(
            f"{row['index']:>5}  {row['date']:<10}  {row['messages']:>8}  "
            f"{row['id']:<36}  {row['title']}"
        )

    remaining = len(conversations) - len(rows)
    if remaining > 0:
        print(f"\nShowing {len(rows)} of {len(conversations)} matches. Use --limit to show more.")
    return 0


def run_export(args: argparse.Namespace) -> int:
    conversations = load_conversations(Path(args.input))
    selected_index, selected = select_conversation(conversations, args)
    out_path = resolve_output_path(Path(args.out), selected, args.format)

    if out_path.exists() and not args.overwrite:
        raise CliError(f"{out_path} already exists; pass --overwrite to replace it")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    if args.format == "json":
        out_path.write_text(json.dumps(selected, indent=2, ensure_ascii=False), encoding="utf-8")
    else:
        out_path.write_text(render_markdown(selected_index, selected), encoding="utf-8")

    print(f"Exported index {selected_index}: {conversation_title(selected)}")
    print(out_path)
    return 0


def load_conversations(path: Path) -> list[Conversation]:
    if not path.exists():
        raise CliError(f"input does not exist: {path}")

    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as archive:
            names = [name for name in archive.namelist() if name.endswith("conversations.json")]
            if not names:
                raise CliError("ZIP does not contain conversations.json")
            with archive.open(names[0]) as handle:
                data = json.load(handle)
    else:
        data = json.loads(path.read_text(encoding="utf-8-sig"))

    if isinstance(data, dict) and isinstance(data.get("conversations"), list):
        data = data["conversations"]
    if not isinstance(data, list):
        raise CliError("expected conversations.json to contain an array of conversations")
    return data


def filtered_conversations(
    conversations: list[Conversation], args: argparse.Namespace
) -> list[tuple[int, Conversation]]:
    search = normalize(getattr(args, "search", None))
    title_filter = normalize(getattr(args, "title", None))
    matches: list[tuple[int, Conversation]] = []

    for index, conversation in enumerate(conversations):
        title = normalize(conversation_title(conversation))
        if title_filter and title_filter not in title:
            continue
        if search and search not in normalize(search_blob(conversation)):
            continue
        matches.append((index, conversation))
    return matches


def sorted_conversations(
    conversations: list[tuple[int, Conversation]], sort: str
) -> list[tuple[int, Conversation]]:
    field, direction = sort.rsplit("-", 1)
    reverse = direction == "desc"

    if field == "index":
        key = lambda item: item[0]
    elif field == "date":
        key = lambda item: conversation_sort_timestamp(item[1])
    elif field == "title":
        key = lambda item: normalize(conversation_title(item[1]))
    elif field == "messages":
        key = lambda item: len(conversation_messages(item[1]))
    else:
        raise CliError(f"unsupported sort: {sort}")

    return sorted(conversations, key=key, reverse=reverse)


def select_conversation(
    conversations: list[Conversation], args: argparse.Namespace
) -> tuple[int, Conversation]:
    if args.index is not None:
        if args.index < 0 or args.index >= len(conversations):
            raise CliError(f"index {args.index} is out of range; valid range is 0..{len(conversations) - 1}")
        return args.index, conversations[args.index]

    if args.id:
        wanted = args.id.lower()
        matches = [
            (index, conversation)
            for index, conversation in enumerate(conversations)
            if any(str(conversation.get(key, "")).lower() == wanted for key in ("uuid", "id", "conversation_id"))
        ]
        return require_one(matches, f"id {args.id!r}")

    if args.title:
        wanted_title = normalize(args.title)
        matches = [
            (index, conversation)
            for index, conversation in enumerate(conversations)
            if normalize(conversation_title(conversation)) == wanted_title
        ]
        return require_one(matches, f"title {args.title!r}")

    matches = filtered_conversations(conversations, args)
    return require_one(matches, f"search {args.search!r}")


def require_one(matches: list[tuple[int, Conversation]], description: str) -> tuple[int, Conversation]:
    if not matches:
        raise CliError(f"no conversation matched {description}")
    if len(matches) > 1:
        sample = ", ".join(str(index) for index, _ in matches[:10])
        raise CliError(f"{len(matches)} conversations matched {description}; use --index or --id. Matching indexes: {sample}")
    return matches[0]


def summary(index: int, conversation: Conversation) -> dict[str, Any]:
    messages = conversation_messages(conversation)
    return {
        "index": index,
        "date": short_date(first_value(conversation, "created_at", "created", "create_time", "updated_at")),
        "messages": len(messages),
        "id": conversation_id(conversation),
        "title": conversation_title(conversation),
    }


def conversation_sort_timestamp(conversation: Conversation) -> float:
    value = first_value(conversation, "created_at", "created", "create_time", "updated_at")
    return timestamp_number(value)


def render_markdown(index: int, conversation: Conversation) -> str:
    title = conversation_title(conversation)
    cid = conversation_id(conversation)
    created = timestamp_text(first_value(conversation, "created_at", "created", "create_time"))
    updated = timestamp_text(first_value(conversation, "updated_at", "updated", "update_time"))
    project = project_name(conversation)

    lines = [
        "---",
        f"title: {yaml_scalar(title)}",
        f"conversation_id: {yaml_scalar(cid)}",
        f"index: {index}",
        f"created_at: {yaml_scalar(created)}",
        f"updated_at: {yaml_scalar(updated)}",
    ]
    if project:
        lines.append(f"project: {yaml_scalar(project)}")
    lines.extend(["---", "", f"# {title}", ""])

    for message in conversation_messages(conversation):
        role = message_role(message)
        stamp = timestamp_text(first_value(message, "created_at", "created", "timestamp"))
        heading = f"## {role}"
        if stamp:
            heading += f" ({stamp})"
        lines.extend([heading, "", message_text(message).strip() or "_No text content found._", ""])

    return "\n".join(lines).rstrip() + "\n"


def conversation_messages(conversation: Conversation) -> list[dict[str, Any]]:
    for key in ("chat_messages", "messages"):
        value = conversation.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def conversation_title(conversation: Conversation) -> str:
    value = first_value(conversation, "name", "title", "summary")
    return clean_one_line(str(value)) if value else "Untitled conversation"


def conversation_id(conversation: Conversation) -> str:
    value = first_value(conversation, "uuid", "id", "conversation_id")
    return str(value) if value else ""


def project_name(conversation: Conversation) -> str:
    project = conversation.get("project")
    if isinstance(project, dict):
        value = first_value(project, "name", "uuid", "id")
        return str(value) if value else ""
    value = first_value(conversation, "project_name", "project_uuid", "project_id")
    return str(value) if value else ""


def message_role(message: dict[str, Any]) -> str:
    raw = first_value(message, "sender", "role", "author")
    if isinstance(raw, dict):
        raw = first_value(raw, "role", "name")
    text = str(raw or "message").lower()
    if text in ("human", "user"):
        return "User"
    if text in ("assistant", "claude"):
        return "Claude"
    return clean_one_line(text).title()


def message_text(message: dict[str, Any]) -> str:
    for key in ("text", "content", "message"):
        if key in message:
            return content_to_text(message[key])
    return content_to_text(message)


def content_to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = [content_to_text(item) for item in value]
        return "\n\n".join(part for part in parts if part)
    if isinstance(value, dict):
        for key in ("text", "content", "value", "body"):
            if key in value:
                return content_to_text(value[key])

        block_type = value.get("type")
        if block_type in ("tool_use", "tool_result"):
            return f"[{block_type}: {value.get('name') or value.get('tool_name') or 'tool'}]"

        useful = []
        for key, item in value.items():
            if key in {"uuid", "id", "created_at", "updated_at", "sender", "role"}:
                continue
            rendered = content_to_text(item)
            if rendered:
                useful.append(rendered)
        return "\n\n".join(useful)
    return str(value)


def search_blob(conversation: Conversation) -> str:
    parts = [conversation_title(conversation), conversation_id(conversation), project_name(conversation)]
    parts.extend(message_text(message) for message in conversation_messages(conversation))
    return "\n".join(parts)


def resolve_output_path(out: Path, conversation: Conversation, fmt: str) -> Path:
    if out.suffix.lower() == f".{fmt}":
        return out
    stem = "__".join(
        part for part in [short_date(first_value(conversation, "created_at", "created", "create_time")), safe_slug(conversation_title(conversation)), short_id(conversation_id(conversation))] if part
    )
    return out / f"{stem or 'conversation'}.{fmt}"


def first_value(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return None


def short_date(value: Any) -> str:
    text = timestamp_text(value)
    return text[:10] if text else ""


def timestamp_text(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 10_000_000_000 else value
        return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()
    text = str(value)
    return text


def timestamp_number(value: Any) -> float:
    if value in (None, ""):
        return 0
    if isinstance(value, (int, float)):
        return value / 1000 if value > 10_000_000_000 else value

    text = str(value).strip()
    if not text:
        return 0
    if text.isdigit():
        number = float(text)
        return number / 1000 if number > 10_000_000_000 else number

    try:
        normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
        return datetime.fromisoformat(normalized).timestamp()
    except ValueError:
        return 0


def safe_slug(value: str, max_length: int = 80) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-._").lower()
    return (slug or "untitled")[:max_length].rstrip("-._")


def short_id(value: str) -> str:
    return value[:8] if value else ""


def clean_one_line(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def normalize(value: Any) -> str:
    return str(value or "").casefold()


def yaml_scalar(value: str) -> str:
    return json.dumps(value or "", ensure_ascii=False)


class CliError(Exception):
    pass


if __name__ == "__main__":
    raise SystemExit(main())
