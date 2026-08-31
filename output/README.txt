OUTPUT FOLDER
=============
This folder is created automatically.

Each Playwright trace.zip from ..\Trace\ becomes:

    <trace-name>.api_sequence.md              readable API breakup (from token fetch)
    <trace-name>.api_sequence.json            same breakup as JSON
    <trace-name>.postman_collection.json      import in Postman

The sequence starts at the access-token fetch and lists values used on each call.
