# Quickstart — OpenAPI generate / check / consume

## Regenerate the OpenAPI document (after any API change)

```bash
make openapi
git add koan/openapi.yaml && git commit
```

This introspects the live Flask app (`koan/app/api/__init__.py::create_app()`) and rewrites
`koan/openapi.yaml`. It needs no running server, no API token, and no `api.enabled: true`.

## Check for drift locally (what CI runs)

```bash
make openapi-check
```

Exits non-zero if `koan/openapi.yaml` is stale, printing the exact regenerate command. Run
this before pushing an API change.

## When does CI run the check?

Only when a pull request touches an **API-defining file**:

- `koan/app/api/**` (any route module or the generator)
- `koan/openapi.yaml`
- `Makefile`
- `.github/workflows/openapi.yml`

PRs that touch none of these skip the drift job entirely — no CI minutes spent.

## Consuming the document

`koan/openapi.yaml` is a standard OpenAPI 3.1 file. Point any OpenAPI tool at it:

```bash
# Preview in a viewer, generate a client, etc. (examples)
npx @redocly/cli preview-docs koan/openapi.yaml
openapi-generator-cli generate -i koan/openapi.yaml -g python -o /tmp/koan-client
```

## Scope note

Iteration 1 documents **paths, methods, path parameters, and bearer-auth security** precisely
(derived from code, drift-guarded). Per-operation request/response **body** schemas are a
future enrichment (see spec Assumptions); they are not yet in the document.
