# Claude Split

Small local CLI for listing conversations from a Claude data export and exporting the one you want.

It reads either:

- Anthropic's export ZIP
- An extracted `conversations.json`

It writes one selected conversation as Markdown or raw JSON. No network access, no dependencies.

## List Conversations

```powershell
python .\claude_split.py list .\claude-export.zip
```

Useful filters:

```powershell
python .\claude_split.py list .\conversations.json --title "tax"
python .\claude_split.py list .\conversations.json --search "database migration"
python .\claude_split.py list .\conversations.json --sort date-desc
python .\claude_split.py list .\conversations.json --limit 500
python .\claude_split.py list .\conversations.json --json
```

The list output shows a zero-based `idx` and a conversation `id`. Use either one to export.

Sort options are `index-asc`, `index-desc`, `date-asc`, `date-desc`, `title-asc`, `title-desc`, `messages-asc`, and `messages-desc`. Sorting only changes the list display; `idx` still refers to the original conversation index for export.

## Export One Conversation

By index:

```powershell
python .\claude_split.py export .\conversations.json --index 12 --out .\exports
```

By conversation id:

```powershell
python .\claude_split.py export .\claude-export.zip --id "00000000-0000-0000-0000-000000000000" --out .\exports
```

By exact title:

```powershell
python .\claude_split.py export .\conversations.json --title "My Conversation Title" --out .\exports
```

Export raw JSON instead of Markdown:

```powershell
python .\claude_split.py export .\conversations.json --index 12 --format json --out .\exports
```

If more than one conversation matches `--search`, the command refuses to guess and prints matching indexes.
