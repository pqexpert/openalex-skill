# Configure an OpenAlex key

Obtain a key from your own [OpenAlex API settings](https://openalex.org/settings/api). Keep the token in a plain-text file outside this repository, or inject it through an existing secret manager.

The client resolves credentials in this order:

1. The explicit `--key-file` path.
2. `OPENALEX_API_KEY` from the environment.
3. The path in `OPENALEX_API_KEY_FILE`.
4. `~/.config/openalex/api_key`.

An explicitly selected missing or invalid source is an error; the client does not silently select another account's key. Copying `.env.example` does not load it automatically. Configure the environment or pass `--key-file`.

```bash
python3 scripts/openalex.py --key-file /absolute/private/path/apiopenalex status
export OPENALEX_API_KEY_FILE=/absolute/private/path/apiopenalex
python3 scripts/openalex.py search works --query 'post-quantum cryptography' --limit 20
```

The key file can contain the raw token or a single `OPENALEX_API_KEY=...` assignment. Set file permissions to `0600` where supported. Pass only the path on the command line; never paste the token into a command, prompt, URL, report, or source file.

## Use an uploaded key in ChatGPT

When a user supplies a key file such as `apiopenalex`, reuse its current attachment path. If only a persistent file reference is available, apply the environment's Library skill to materialize that known file into a private workspace directory, using its current identifiers. Do not print the token with a content-reading tool. If no current identifier is known, use exact filename and title-only search, and resolve any ambiguity before selecting a credential. A previous temporary path expiring does not mean the original uploaded file was lost.

This public repository contains no personal upload identifiers or credentials. If the key is unavailable, request reattachment or configuration of `OPENALEX_API_KEY_FILE`.

## Verify access

Run `status` once before a large retrieval. It checks OpenAlex's returned key or masked key hint against the configured token and emits redacted usage information. HTTP 401/403 indicates authentication or access failure. Budget exhaustion and connectivity failures are separate conditions. Never silently fall back to anonymous requests or forward the authorization header to DOI, publisher, repository, or PDF URLs.

Never commit the actual key, even in a private fork. The offline tests and GitHub Actions do not need real credentials or repository secrets.
