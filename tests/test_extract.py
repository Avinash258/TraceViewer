"""Build a synthetic Playwright trace.zip and convert it to a Postman collection."""

from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from apiextracter.parser import parse_trace
from apiextracter.postman import POSTMAN_SCHEMA, build_collection


def _network_jsonl() -> str:
    events = [
        {
            "type": "resource-snapshot",
            "snapshot": {
                "startedDateTime": "2026-08-27T07:00:00.000Z",
                "time": 42,
                "_apiRequest": True,
                "request": {
                    "method": "GET",
                    "url": "https://api.example.com/v1/users?page=1",
                    "headers": [
                        {"name": "Authorization", "value": "Bearer secret-token"},
                        {"name": "Accept", "value": "application/json"},
                        {"name": "sec-ch-ua", "value": "Chromium"},
                    ],
                    "queryString": [{"name": "page", "value": "1"}],
                },
                "response": {
                    "status": 200,
                    "statusText": "OK",
                    "headers": [{"name": "content-type", "value": "application/json"}],
                    "content": {
                        "mimeType": "application/json",
                        "text": '{"users":[{"id":1}]}',
                    },
                },
            },
        },
        {
            "type": "resource-snapshot",
            "snapshot": {
                "time": 80,
                "request": {
                    "method": "POST",
                    "url": "https://api.example.com/v1/users",
                    "headers": [
                        {"name": "Authorization", "value": "Bearer secret-token"},
                        {"name": "Content-Type", "value": "application/json"},
                    ],
                    "postData": {
                        "mimeType": "application/json",
                        "text": '{"name":"Ada"}',
                    },
                },
                "response": {
                    "status": 201,
                    "statusText": "Created",
                    "headers": [{"name": "content-type", "value": "application/json"}],
                    "content": {
                        "mimeType": "application/json",
                        "_sha1": "abc123created",
                    },
                },
            },
        },
        {
            "type": "resource-snapshot",
            "snapshot": {
                "time": 12,
                "request": {
                    "method": "GET",
                    "url": "https://cdn.example.com/app.js",
                    "headers": [],
                },
                "response": {
                    "status": 200,
                    "content": {"mimeType": "application/javascript", "text": "console.log(1)"},
                },
            },
        },
        {
            "type": "resource-snapshot",
            "snapshot": {
                "time": 5,
                "request": {
                    "method": "OPTIONS",
                    "url": "https://api.example.com/v1/users",
                    "headers": [],
                },
                "response": {"status": 204, "content": {"mimeType": "text/plain"}},
            },
        },
        {
            "type": "resource-snapshot",
            "snapshot": {
                "time": 30,
                "request": {
                    "method": "POST",
                    "url": "https://api.example.com/graphql",
                    "headers": [{"name": "Content-Type", "value": "application/json"}],
                    "postData": {
                        "mimeType": "application/json",
                        "text": '{"query":"query Q { me { id } }","variables":{"id":1}}',
                    },
                },
                "response": {
                    "status": 200,
                    "statusText": "OK",
                    "content": {"mimeType": "application/json", "text": '{"data":{"me":{"id":1}}}'},
                },
            },
        },
    ]
    return "\n".join(json.dumps(event) for event in events) + "\n"


def _write_trace_zip(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "trace.trace",
            json.dumps({"type": "context-options", "title": "Checkout flow", "browserName": "chromium"})
            + "\n",
        )
        zf.writestr("trace.network", _network_jsonl())
        zf.writestr("resources/abc123created", '{"id":42,"name":"Ada"}')
    return path


class ExtractTests(unittest.TestCase):
    def test_zip_to_postman_collection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "trace.zip"
            _write_trace_zip(zip_path)

            result = parse_trace(zip_path)
            self.assertEqual(result.title, "Checkout flow")
            self.assertEqual(len(result.requests), 3)
            self.assertEqual(result.filtered_out, 2)
            methods = [req.method for req in result.requests]
            self.assertEqual(methods, ["GET", "POST", "POST"])
            self.assertEqual(result.requests[1].response_body, '{"id":42,"name":"Ada"}')

            collection = build_collection(result, name="Checkout APIs")
            self.assertEqual(collection["info"]["schema"], POSTMAN_SCHEMA)
            self.assertEqual(collection["info"]["name"], "Checkout APIs")

            items = collection["item"]
            self.assertEqual(len(items), 3)
            get_req = items[0]["request"]
            self.assertEqual(get_req["method"], "GET")
            self.assertEqual(get_req["url"]["query"], [{"key": "page", "value": "1"}])
            header_keys = {h["key"].lower() for h in get_req["header"]}
            self.assertNotIn("sec-ch-ua", header_keys)
            auth = next(h for h in get_req["header"] if h["key"].lower() == "authorization")
            self.assertEqual(auth["value"], "Bearer {{bearerToken}}")
            self.assertTrue(
                any(v["key"] == "bearerToken" and v["value"] == "secret-token" for v in collection["variable"])
            )

            post_req = items[1]["request"]
            self.assertEqual(post_req["body"]["mode"], "raw")
            self.assertIn("Ada", post_req["body"]["raw"])
            self.assertEqual(items[1]["response"][0]["code"], 201)
            self.assertIn("42", items[1]["response"][0]["body"])

            gql = items[2]["request"]["body"]
            self.assertEqual(gql["mode"], "graphql")
            self.assertIn("me { id }", gql["graphql"]["query"])

    def test_include_static_and_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "trace.zip"
            _write_trace_zip(zip_path)
            result = parse_trace(zip_path, include_static=True, include_preflight=True)
            self.assertEqual(len(result.requests), 5)

    def test_cli_writes_named_output(self) -> None:
        from apiextracter.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "trace.zip"
            out = Path(tmp) / "flow.postman_collection.json"
            _write_trace_zip(zip_path)
            rc = main([str(zip_path), "-o", str(out), "--name", "CLI flow"])
            self.assertEqual(rc, 0)
            self.assertTrue(out.is_file())
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(data["info"]["name"], "CLI flow")
            self.assertTrue(data["item"])


if __name__ == "__main__":
    unittest.main()
