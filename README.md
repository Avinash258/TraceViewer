# Playwright Trace to Postman Collection

Python app that reads a Playwright `trace.zip` and writes:

1. A **sequenced API breakup** starting from the **access-token fetch**, with the values used
2. A **Postman Collection v2.1 JSON** you can import and replay in that same order

Repository: [github.com/Avinash258/TraceViewer](https://github.com/Avinash258/TraceViewer)

```powershell
git clone https://github.com/Avinash258/TraceViewer.git
cd TraceViewer
python extract.py
```

## Folders

| Role | Path | What to put there |
|---|---|---|
| **Input** | `Trace\` | Playwright `trace.zip` files |
| **Output** | `output\` | Sequence + Postman JSON (created automatically) |
| App | `extract.py` | Run this from the project root |

```
apiextracter\
  extract.py
  Trace\
    my-flow.zip                         <- INPUT
  output\
    my-flow.api_sequence.md             <- readable breakup (start at token fetch)
    my-flow.api_sequence.json           <- same breakup as JSON
    my-flow.postman_collection.json     <- import in Postman
```

## How to execute

```powershell
cd c:\Project\apiextracter
python extract.py
```

That processes every `.zip` in `Trace\`.

One file:

```powershell
python extract.py Trace\my-trace.zip
```

## What you get

The sequence **starts at the access-token fetch** (oauth/token, login, etc.). Calls before that are skipped unless you pass `--full-flow`.

For each API in order it records:

- Method, URL, status
- **Values used**: query, body fields (username, password, grant_type, …), headers
- Token written from the fetch response (`access_token` / `accessToken`)
- Later APIs that send that token as `Authorization: Bearer {{accessToken}}`

The Postman collection:

- Keeps that same order
- Saves `{{accessToken}}` from the token-fetch response (Tests script)
- Uses `Bearer {{accessToken}}` on the following requests

Import in Postman: **Import → File** and select `output\<name>.postman_collection.json`. Run the collection **in order**.

## Options

| Flag | Meaning |
|---|---|
| `-o`, `--output` | Postman JSON path (one input only) |
| `--name` | Collection name in Postman |
| `--full-flow` | Keep APIs that ran before the token fetch |
| `--no-sequence` | Do not write `.api_sequence.md` / `.json` |
| `--group-by sequence\|host\|path\|none` | Folder grouping (default: `sequence`) |
| `--include-static` | Keep JS/CSS/images |
| `--include-preflight` | Keep OPTIONS requests |
| `--dedupe` | Drop identical method + URL + body repeats |

```powershell
python extract.py --help
python extract.py Trace\my-trace.zip --full-flow
```

Python 3.10+ is required. No pip packages are needed.

## Notes

- Tokens expire. Re-run the token request in Postman so `{{accessToken}}` is refreshed.
- Replaying write APIs can change data. Prefer a test environment.
