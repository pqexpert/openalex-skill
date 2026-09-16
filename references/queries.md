# Query reference

Checked against official OpenAlex documentation on 2026-09-16. Run commands from this skill's directory, or use an absolute script path. Configure the credential first as described in `credentials.md`.

## Resolve entities

```bash
python3 scripts/openalex.py search authors --query 'Heather Piwowar' --limit 5
python3 scripts/openalex.py get authors https://orcid.org/0000-0003-1613-5981
python3 scripts/openalex.py search institutions --query 'Syracuse University' --limit 5
python3 scripts/openalex.py get sources issn:0028-0836
python3 scripts/openalex.py search topics --query 'quantum cryptography' --limit 10
```

Entity lists supported by the helper: works, authors, institutions, sources, topics, publishers, funders, and awards. Exact OpenAlex IDs are accepted with or without `https://openalex.org/`; DOI, ORCID, ROR, ISSN, PMID and PMCID forms are routed to the relevant entity. An ambiguous name requires comparison of candidate records before applying its ID.

## Filters, fields, and counts

| Intent | Query expression |
| --- | --- |
| Works by a resolved author | `--filter 'authorships.author.id:A123'` |
| Works from an institution | `--filter 'authorships.institutions.id:I123'` |
| Publication date bounds | `--filter 'from_publication_date:2024-01-01,to_publication_date:2026-09-16'` |
| Open-access articles | `--filter 'type:article,is_oa:true'` |
| Sort by recency | `--sort publication_date:desc` |
| Sort by citations | `--sort cited_by_count:desc` |
| Choose fields | `--select id,doi,display_name,authorships,publication_year` |
| Include expansion corpus | `--corpus all` (works only) |

Repeated `--filter` flags join with commas (AND). Use a pipe inside one filter for OR, up to 100 values, for example `type:article|preprint`. Names do not substitute for IDs. Do not use nested properties in `--select`; select their top-level parent objects. Full-text search can match text that is not returned as a readable full paper.

`group works publication_year --query 'post-quantum cryptography'` returns year buckets. Group counts can overlap for multivalued fields such as authors or institutions. Missing values may be excluded unless the API's `:include_unknown` suffix is used; do not add overlapping group counts as though they were unique works. Group pagination uses cursors, and cursor order is by key rather than impact. The helper conservatively uses at most 100 items per page for both records and groups.

Sources: [filtering](https://help.openalex.org/api/filtering/), [singleton lookups](https://help.openalex.org/api/get-single-entities/), [grouping](https://help.openalex.org/api/grouping/), [work attributes](https://help.openalex.org/data/works/attributes/).

## Citation tracing

Resolve the seed paper to an OpenAlex Work ID, e.g. `W2741809807`.

- **Incoming citations:** `search works --filter 'cites:W2741809807'` finds works that cite the seed.
- **Outgoing references:** fetch the seed's `referenced_works`, then batch those IDs with `--filter 'openalex:W123|W456'`. Batch at most 100 IDs, and retain seed-to-reference edges. Missing matches reflect incomplete indexing.
- **Related works:** `related_works` contains algorithmic recommendations; do not describe these as citations.

Limit citation expansion by depth and record count according to the question. A one-hop citation search is usually enough for an initial literature map. Consult [citations](https://help.openalex.org/data/works/citations/) for current filter semantics.

## Pagination and exports

The helper starts with `cursor=*` and follows returned cursors. `--limit` defaults to 25; `--max-pages` defaults to 10. Both are explicit bounds. Increase them only to meet the task's scope. The metadata reports why retrieval stopped and gives a resume cursor. Preserve all search, filter, sort, selection, corpus and group settings on resumption:

```bash
python3 scripts/openalex.py --output /absolute/output/research.json search works \
  --query 'post-quantum cryptography' --limit 250 --max-pages 3 --abstracts
```

Select `--format jsonl` for one complete record per line, or `--format csv` for a compact table. A `.meta.json` sidecar preserves retrieval details for file exports. CSV cells that could trigger spreadsheet formulas are escaped. Abstracts are reconstructed only when the inverted index is available, with missing positions represented explicitly.

Sources: [paging](https://help.openalex.org/api/paging/), [authentication and budget headers](https://help.openalex.org/api/authentication/), [API overview](https://help.openalex.org/api/).
