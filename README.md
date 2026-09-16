# OpenAlex skill

A reusable Codex/ChatGPT skill for literature discovery, entity resolution, citation tracing, research counts, and bibliographic exports. The Python client uses the standard library and supports Python 3.10 or newer.

## Use the skill

Ask: **“Use @openalex to find recent papers on post-quantum cryptography, identify the main research groups, and return cited findings.”**

The instructions live in [SKILL.md](SKILL.md). Reference guides cover query construction and safe credential recovery. Install the skill folder with your environment's skill installer, or run the client directly.

## Run the client

Keep the key file outside this repository. Pass only its path:

```bash
python3 scripts/openalex.py --key-file /absolute/private/path/apiopenalex status
export OPENALEX_API_KEY_FILE=/absolute/private/path/apiopenalex
python3 scripts/openalex.py search works --query 'post-quantum cryptography' --limit 20 --abstracts
python3 scripts/openalex.py get works doi:10.7717/peerj.4375
python3 scripts/openalex.py --output /absolute/output/works.csv --format csv search works --query 'zero trust architecture' --limit 50
```

Copying `.env.example` does not load it automatically; configure the environment or pass `--key-file`. Never commit the actual key. This public repository contains no personal upload identifiers or credentials. Configure your own OpenAlex key; installed personal copies may retain a private pointer to the owner's uploaded key file.

The client sends credentials in a bearer header to the OpenAlex API, blocks redirects to other origins, redacts credentials in responses, preserves query metadata, and limits pagination and retries. JSON is the default output; CSV and JSONL file exports include metadata sidecars. Existing output files are preserved. CSV is intended for compact bibliographic tables; use JSON for complete author, institution, or other entity records.

Metadata supports discovery. Read the actual papers before presenting substantive research findings. OpenAlex results can have missing fields, merged identities, incomplete citation coverage, and version differences.

## Check the code

```bash
python3 -m unittest discover -s tests -v
```

Tests run offline with invented credentials. GitHub Actions runs the same suite and never requires the user's API key. Live API calls consume the account's available OpenAlex budget; no recurring searches or paid-content downloads are configured.

Official documentation: [OpenAlex API](https://help.openalex.org/api/).
