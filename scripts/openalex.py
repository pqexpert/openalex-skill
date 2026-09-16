#!/usr/bin/env python3
"""Bounded, credential-safe OpenAlex REST client (Python 3.10+, standard library)."""

import argparse
import csv
import io
import json
import os
from pathlib import Path
import re
import sys
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

BASE = "https://api.openalex.org"
ENTITIES = {"works": "W", "authors": "A", "sources": "S", "institutions": "I",
            "topics": "T", "publishers": "P", "funders": "F", "awards": "G"}
SENSITIVE = {"api_key", "apikey", "authorization", "access_token", "token"}


class OpenAlexError(Exception):
    """An error safe to display without exposing credentials or server bodies."""


def load_key(key_file=None):
    """An explicit source wins; a broken explicit source never falls back."""
    if key_file is not None:
        path = Path(key_file).expanduser()
    elif "OPENALEX_API_KEY" in os.environ:
        return validate_key(os.environ["OPENALEX_API_KEY"])
    elif "OPENALEX_API_KEY_FILE" in os.environ:
        path = Path(os.environ["OPENALEX_API_KEY_FILE"]).expanduser()
    else:
        path = Path.home() / ".config/openalex/api_key"
    try:
        if path.stat().st_size > 4096:
            raise OpenAlexError("Key file is too large; supply the plain-text token file.")
        value = path.read_text(encoding="utf-8-sig").strip()
    except (OSError, UnicodeError):
        raise OpenAlexError("Cannot read key file. Configure --key-file or OPENALEX_API_KEY_FILE.") from None
    if value.startswith("export "):
        value = value[7:].strip()
    if value.startswith("OPENALEX_API_KEY="):
        value = value.split("=", 1)[1].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
    return validate_key(value)


def validate_key(value):
    value = value.strip()
    if not re.fullmatch(r"[!-~]{8,512}", value):
        raise OpenAlexError("Credential must contain one nonempty token without whitespace.")
    return value


def redact(value, secret):
    if isinstance(value, dict):
        return {str(k).replace(secret, "[REDACTED]"):
                "[REDACTED]" if str(k).lower() in SENSITIVE else redact(v, secret)
                for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, secret) for v in value]
    if isinstance(value, str):
        value = value.replace(secret, "[REDACTED]")
        return re.sub(r"(?i)([?&](?:api_key|access_token)=)[^&#\s]+", r"\1[REDACTED]", value)
    return value


def credential_matches(reported, key):
    """OpenAlex's usage endpoint may return a masked key hint, not the full key."""
    if not isinstance(reported, str):
        return False
    if reported == key:
        return True
    parts = re.fullmatch(r"([^.*]*)(?:\.{3,}|\*+)([^.*]*)", reported)
    if not parts:
        return False
    prefix, suffix = parts.groups()
    return (len(prefix) + len(suffix) >= 4 and len(prefix) + len(suffix) < len(key)
            and key.startswith(prefix) and key.endswith(suffix))


class SameOriginRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parts = urlsplit(newurl)
        if (parts.scheme != "https" or parts.hostname != "api.openalex.org"
                or parts.port not in (None, 443) or parts.username or parts.password):
            raise OpenAlexError("Blocked a redirect outside the authenticated OpenAlex API.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Client:
    def __init__(self, key, timeout=25, retries=2):
        self.key = validate_key(key)
        self.timeout = timeout
        self.retries = retries
        self.opener = build_opener(SameOriginRedirect())
        self.rate_headers = {}

    def get(self, endpoint, params=None, verify_identity=False):
        params = params or {}
        if any(k.lower() in SENSITIVE for k in params):
            raise OpenAlexError("Credentials must not be placed in query parameters.")
        if endpoint.split("/", 1)[0] not in {*ENTITIES, "rate-limit"}:
            raise OpenAlexError("Unsupported OpenAlex endpoint.")
        url = BASE + "/" + endpoint
        if params:
            url += "?" + urlencode(params)
        request = Request(url, headers={"Authorization": "Bearer " + self.key,
                                       "Accept": "application/json",
                                       "User-Agent": "openalex-skill/1.0"})
        for attempt in range(self.retries + 1):
            try:
                with self.opener.open(request, timeout=self.timeout) as response:
                    data = json.load(response)
                    self.rate_headers = {k: v for k, v in response.headers.items()
                                         if k.lower().startswith("x-ratelimit-")}
                if not isinstance(data, dict):
                    raise OpenAlexError("OpenAlex returned an unexpected response shape.")
                if verify_identity and not credential_matches(data.get("api_key"), self.key):
                    raise OpenAlexError("OpenAlex did not confirm the configured credential.")
                return redact(data, self.key)
            except HTTPError as exc:
                status = exc.code
                retry_after = exc.headers.get("Retry-After")
                remaining = exc.headers.get("X-RateLimit-Remaining")
                exc.close()
                if status in (401, 403):
                    raise OpenAlexError(f"OpenAlex HTTP {status}: authentication or access denied.") from None
                if status == 404:
                    raise OpenAlexError("OpenAlex HTTP 404: entity not found.") from None
                if status == 429 and remaining is not None:
                    try:
                        if float(remaining) <= 0:
                            raise OpenAlexError("OpenAlex daily budget exhausted; retry after reset.")
                    except ValueError:
                        pass
                if status not in (429, 500, 502, 503, 504) or attempt == self.retries:
                    raise OpenAlexError(f"OpenAlex HTTP {status}: request failed; check query or service status.") from None
                delay = 2 ** attempt
                if retry_after:
                    try:
                        delay = max(0, float(retry_after))
                    except ValueError:
                        try:
                            delay = max(0, (parsedate_to_datetime(retry_after) - datetime.now(timezone.utc)).total_seconds())
                        except (ValueError, TypeError):
                            pass
                if delay > 8:
                    raise OpenAlexError("OpenAlex requested a longer wait; stop and retry later.") from None
                time.sleep(delay)
            except (URLError, TimeoutError, OSError):
                if attempt == self.retries:
                    raise OpenAlexError("OpenAlex network request failed. Check connectivity or network policy.") from None
                time.sleep(2 ** attempt)
            except (json.JSONDecodeError, UnicodeError):
                raise OpenAlexError("OpenAlex returned invalid JSON; response body suppressed.") from None
        raise OpenAlexError("OpenAlex request did not complete.")


def singleton_path(entity, identifier):
    value = identifier.strip()
    value = re.sub(r"^https?://openalex\.org/", "", value, flags=re.I)
    if re.fullmatch(r"[WAISTPFG]\d+", value, re.I):
        if value[0].upper() != ENTITIES[entity]:
            raise OpenAlexError("OpenAlex ID prefix does not match the selected entity.")
        return entity + "/" + value.upper()
    replacements = {"works": [(r"^https?://(?:dx\.)?doi\.org/", "doi:")],
                    "authors": [(r"^https?://orcid\.org/", "orcid:")],
                    "institutions": [(r"^https?://ror\.org/", "ror:")]}
    for pattern, replacement in replacements.get(entity, []):
        value = re.sub(pattern, replacement, value, flags=re.I)
    if entity == "works" and value.startswith("10."):
        value = "doi:" + value
    patterns = {
        "works": r"(?:doi:10\.\d{4,9}/\S+|pmid:\d+|pmcid:PMC\d+)",
        "authors": r"orcid:\d{4}-\d{4}-\d{4}-\d{3}[\dX]",
        "institutions": r"ror:0[a-z0-9]{8}",
        "sources": r"issn:\d{4}-\d{3}[\dX]",
    }
    if not re.fullmatch(patterns.get(entity, r"(?!)"), value, re.I):
        raise OpenAlexError("Unsupported identifier; use an OpenAlex ID or documented external ID.")
    return entity + "/" + quote(value, safe=":/")


def abstract_text(index):
    if not isinstance(index, dict) or not index:
        return None
    words = {}
    for word, positions in index.items():
        if not isinstance(positions, list):
            continue
        for pos in positions:
            if type(pos) is int and 0 <= pos < 100000:
                words.setdefault(pos, str(word))
    return " ".join(words.get(i, "[missing]") for i in range(max(words) + 1)) if words else None


def add_abstracts(records):
    for record in records:
        record["abstract"] = abstract_text(record.get("abstract_inverted_index"))


def collect(client, entity, params, limit=25, per_page=100, max_pages=10, cursor="*", grouped=False):
    if limit < 1 or not 1 <= per_page <= 100 or max_pages < 1:
        raise OpenAlexError("Use limit >= 1, per-page between 1 and 100, and max-pages >= 1.")
    records, seen_ids, seen_cursors = [], set(), set()
    next_cursor, reported_count, pages, cost = cursor, None, 0, 0.0
    reason = "max_pages"
    for _ in range(max_pages):
        if next_cursor in seen_cursors:
            reason = "repeated_cursor"
            break
        seen_cursors.add(next_cursor)
        query = {**params, "per_page": min(per_page, limit - len(records)), "cursor": next_cursor}
        data = client.get(entity, query)
        pages += 1
        meta = data.get("meta") or {}
        if reported_count is None:
            reported_count = meta.get("count")
        current_cost = meta.get("cost_usd")
        if isinstance(current_cost, (int, float)):
            cost += current_cost
        batch = data.get("group_by" if grouped else "results")
        if not isinstance(batch, list) or any(not isinstance(x, dict) for x in batch):
            raise OpenAlexError("OpenAlex list response is missing the expected records.")
        for item in batch:
            identity = item.get("key" if grouped else "id")
            if identity is not None:
                if identity in seen_ids:
                    continue
                seen_ids.add(identity)
            records.append(item)
        next_cursor = meta.get("next_cursor")
        if not batch or not next_cursor:
            reason = "exhausted"
            next_cursor = None
            break
        if len(records) >= limit:
            reason = "limit"
            break
    return {"meta": {"retrieved_at": datetime.now(timezone.utc).isoformat(),
                     "endpoint": entity, "query": params, "start_cursor": cursor,
                     "reported_count": reported_count, "returned_count": len(records),
                     "pages_fetched": pages, "stop_reason": reason,
                     "next_cursor": next_cursor, "cost_usd_observed": round(cost, 8),
                     "rate_limit_headers": client.rate_headers},
            "groups" if grouped else "results": records}


def safe_cell(value):
    if value is None:
        return ""
    text = str(value)
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")) else text


def render_csv(records, grouped=False):
    output = io.StringIO(newline="")
    fields = ["key", "key_display_name", "count"] if grouped else [
        "id", "doi", "display_name", "publication_year", "publication_date", "type",
        "authors", "source", "cited_by_count", "is_oa", "oa_url", "is_retracted", "abstract"]
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for item in records:
        row = {k: item.get(k) for k in fields}
        if not grouped:
            row["authors"] = "; ".join((a.get("author") or {}).get("display_name") or ""
                                       for a in item.get("authorships") or [])
            row["source"] = ((item.get("primary_location") or {}).get("source") or {}).get("display_name")
            row["is_oa"] = (item.get("open_access") or {}).get("is_oa")
            row["oa_url"] = (item.get("open_access") or {}).get("oa_url")
        writer.writerow({k: safe_cell(v) for k, v in row.items()})
    return output.getvalue()


def write_output(payload, fmt, output=None):
    records = payload.get("results", payload.get("groups", [payload.get("result", payload)]))
    if fmt == "json":
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    elif fmt == "jsonl":
        text = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
    else:
        text = render_csv(records, "groups" in payload)
    if output:
        path = Path(output).expanduser()
        sidecar = path.with_suffix(path.suffix + ".meta.json")
        if path.exists() or (fmt != "json" and sidecar.exists()):
            raise OpenAlexError("Output already exists. Choose a new path to preserve previous results.")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8", newline="") as stream:
            stream.write(text)
        if fmt != "json":
            with sidecar.open("x", encoding="utf-8") as stream:
                json.dump(payload.get("meta", {}), stream, indent=2)
                stream.write("\n")
        print(json.dumps({"saved": str(path.resolve()), "format": fmt}))
    else:
        sys.stdout.write(text)
        if fmt != "json" and payload.get("meta"):
            print(json.dumps({"meta": payload["meta"]}), file=sys.stderr)


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--key-file", help="Path to a token file; never pass the token itself")
    p.add_argument("--output", help="New output file (existing files are preserved)")
    p.add_argument("--format", choices=["json", "jsonl", "csv"], default="json")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="Verify the configured key and show redacted budget information")
    for name in ("search", "group", "get"):
        s = sub.add_parser(name)
        s.add_argument("entity", choices=ENTITIES)
        if name == "get":
            s.add_argument("identifier")
        else:
            if name == "group":
                s.add_argument("field")
            s.add_argument("--query")
            s.add_argument("--filter", action="append", default=[])
            s.add_argument("--sort")
            s.add_argument("--limit", type=int, default=25)
            s.add_argument("--per-page", type=int, default=100)
            s.add_argument("--max-pages", type=int, default=10)
            s.add_argument("--cursor", default="*")
            s.add_argument("--corpus", choices=["core", "all", "expansion"])
        if name != "group":
            s.add_argument("--select")
            s.add_argument("--abstracts", action="store_true")
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "status" and args.format != "json":
            raise OpenAlexError("Use JSON format for credential status.")
        selection = getattr(args, "select", None)
        if selection and any("." in field for field in selection.split(",")):
            raise OpenAlexError("Select top-level fields; nested field selection is unsupported.")
        if getattr(args, "abstracts", False) and args.entity != "works":
            raise OpenAlexError("Abstracts apply to works only.")
        if getattr(args, "abstracts", False) and selection:
            selection = ",".join(dict.fromkeys(selection.split(",") + ["abstract_inverted_index"]))
        if getattr(args, "corpus", None) and args.entity != "works":
            raise OpenAlexError("Corpus selection applies to works only.")
        client = Client(load_key(args.key_file))
        if args.command == "status":
            payload = {"authenticated": True, "usage": client.get("rate-limit", verify_identity=True)}
        elif args.command == "get":
            params = {"select": selection} if selection else {}
            result = client.get(singleton_path(args.entity, args.identifier), params)
            if args.abstracts:
                add_abstracts([result])
            payload = {"meta": {"retrieved_at": datetime.now(timezone.utc).isoformat(),
                                "endpoint": args.entity, "identifier": args.identifier,
                                "query": params, "rate_limit_headers": client.rate_headers}, "result": result}
        else:
            params = {}
            for arg, name in (("query", "search"), ("sort", "sort"), ("corpus", "corpus")):
                if getattr(args, arg, None):
                    params[name] = getattr(args, arg)
            if args.filter:
                params["filter"] = ",".join(args.filter)
            if selection:
                params["select"] = selection
            if args.command == "group":
                params["group_by"] = args.field
            payload = collect(client, args.entity, params, args.limit, args.per_page,
                              args.max_pages, args.cursor, args.command == "group")
            if getattr(args, "abstracts", False):
                add_abstracts(payload["results"])
        write_output(redact(payload, client.key), args.format, args.output)
        return 0
    except OpenAlexError as exc:
        print(f"OpenAlex: {exc}", file=sys.stderr)
        return 1
    except OSError:
        print("OpenAlex: could not write output; check the destination and permissions.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
