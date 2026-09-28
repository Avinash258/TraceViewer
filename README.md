# TraceViewer â€” Playwright Trace â†’ Postman

Turns a Playwright `trace.zip` into a **sequenced API breakdown** and an importable **Postman Collection v2.1**.

> [Portfolio](https://avinash258.github.io/Protfolio/) Â· related: [AI Accessibility Auditor](https://github.com/Avinash258/AI-accessibilty-Auditor)

## Overview

When UI tests fail or you need to replay the API traffic behind a journey, this Python tool reads a Playwright trace archive and reconstructs the network story â€” starting from access-token fetch where present â€” then exports a Postman collection you can run independently of the browser.

## Outputs

1. **Sequenced API breakup** â€” ordered requests with values used at runtime  
2. **Postman Collection v2.1 JSON** â€” import and replay in the same order  

## Stack

- Python
- Playwright trace format
- Postman Collection schema v2.1

## Getting started

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt

# Place a Playwright trace.zip under Trace/ (or path expected by extract.py)
python extract.py
```

Generated artefacts land under `output/`. See `apiextracter/` and `tests/` for library and validation code. Optional packaging metadata is in `pyproject.toml`.

## Author

**Avinash Sharma** â€” QA Automation Architect / Lead SDET  
[GitHub](https://github.com/Avinash258) Â· [LinkedIn](https://www.linkedin.com/in/p-avinash-sharma-8b0203b9/) Â· [Portfolio](https://avinash258.github.io/Protfolio/)
