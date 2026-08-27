# Playwright Trace to Postman Collection

Python app that reads a Playwright `trace.zip` and writes a Postman Collection v2.1 JSON file you can import.

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
| **Output** | `output\` | Generated `*.postman_collection.json` (created automatically) |
| App | `extract.py` | Run this from the project root |

```
apiextracter\
  extract.py                 <- run this
  Trace\
    my-flow.zip              <- INPUT: drop Playwright traces here
  output\
    my-flow.postman_collection.json   <- OUTPUT: import this in Postman
```

## How to execute

1. Copy your Playwright trace into the input folder:

   `c:\Project\apiextracter\Trace\`

   Example already in this project:

   `Trace\4e6fd384-d651-4ca4-ae15-ae87b8dcfd0c-attachment.zip`

2. Open PowerShell in the project root:

   ```powershell
   cd c:\Project\apiextracter
   python extract.py
   ```

   That processes **every** `.zip` in `Trace\`.

3. Open the JSON from the output folder:

   `c:\Project\apiextracter\output\<trace-name>.postman_collection.json`

4. In Postman: **Import → File** and select that JSON.

### One specific trace

```powershell
cd c:\Project\apiextracter
python extract.py Trace\4e6fd384-d651-4ca4-ae15-ae87b8dcfd0c-attachment.zip
```

Output for that file:

`output\4e6fd384-d651-4ca4-ae15-ae87b8dcfd0c-attachment.postman_collection.json`

### Custom output path

```powershell
python extract.py Trace\my-trace.zip -o output\checkout.postman_collection.json --name "Checkout flow"
```

### If `python` is not found

Use the Python launcher or a full path, for example:

```powershell
py -3 extract.py
```

or:

```powershell
python3 extract.py
```

Python 3.10+ is required. No pip packages are needed.

## What the JSON contains

By default the collection is **API traffic only**, in capture order:

- Method, URL, query, headers, body
- Example responses from the trace
- Auth tokens as collection variables when present

Filtered out unless you opt in: JS/CSS/images, HTML page loads, CORS `OPTIONS`.

## Options

| Flag | Meaning |
|---|---|
| `-o`, `--output` | Output JSON path (one input only) |
| `--name` | Collection name in Postman |
| `--group-by host\|path\|none` | Folder grouping (default: `host`) |
| `--include-static` | Keep JS/CSS/images |
| `--include-preflight` | Keep OPTIONS requests |
| `--include-browser-headers` | Keep `sec-ch-*` / `sec-fetch-*` headers |
| `--no-responses` | Skip example responses |
| `--dedupe` | Drop identical method + URL + body repeats |
| `--no-variables` | Keep raw auth header values |

```powershell
python extract.py --help
```

## Input types

The input can be:

- a Playwright `trace.zip` (usual case)
- an unzipped trace folder
- a standalone `.network` or `.har` file

## How Playwright traces are read

A `trace.zip` is a zip of JSONL files. Network activity is in `*.network` (HAR-style `resource-snapshot` events). Large bodies live under `resources/` and are referenced by `_sha1` / `_file`. This app reads the zip in memory and rebuilds those bodies into Postman requests.

## Notes

- Tokens and cookies from the recording expire. Update collection variables before replaying.
- Replaying write APIs can change data. Prefer a test environment.
