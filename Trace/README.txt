INPUT FOLDER
============
Put Playwright trace.zip files in this folder.

Then from the project root run:

    python extract.py

Generated files are written to:

    ..\output\

    *.api_sequence.md     API breakup in call order, from token fetch
    *.api_sequence.json
    *.postman_collection.json
