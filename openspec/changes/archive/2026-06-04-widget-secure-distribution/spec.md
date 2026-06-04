# Delta Spec: Widget Secure Distribution

**Change**: widget-secure-distribution
**Date**: 2026-06-04
**Artifact store**: hybrid

---

## New Capability: widget-key-gate

### Requirement: Publishable Key Validation

Every widget API request MUST carry a `X-Publishable-Key` header containing a valid `pk_`-prefixed key. The backend MUST resolve that key to a tenant. Requests missing the header or carrying an unrecognized key MUST be rejected with HTTP 403.

`allow_no_key=True` MAY be set on a per-route basis to permit same-origin demo landing requests (no key required from the same host).

#### Scenario: Valid key, allowlisted origin

- GIVEN a tenant with `publishable_key: "pk_abc"` and `embed_origins: ["https://tenant.com"]`
- WHEN a request arrives with `X-Publishable-Key: pk_abc` and `Origin: https://tenant.com`
- THEN the request is allowed to proceed (HTTP 200)

#### Scenario: Missing publishable key

- GIVEN any widget API route without `allow_no_key=True`
- WHEN a request arrives with no `X-Publishable-Key` header
- THEN the response MUST be HTTP 403

#### Scenario: Unrecognized publishable key

- GIVEN no tenant exists for key `pk_unknown`
- WHEN a request arrives with `X-Publishable-Key: pk_unknown`
- THEN the response MUST be HTTP 403

#### Scenario: Valid key, non-allowlisted origin

- GIVEN a tenant with `embed_origins: ["https://tenant.com"]`
- WHEN a request arrives with a valid key but `Origin: https://attacker.com`
- THEN the response MUST be HTTP 403

#### Scenario: Same-origin demo route with allow_no_key

- GIVEN a route configured with `allow_no_key=True`
- WHEN a request arrives with no `X-Publishable-Key` header
- THEN the request is allowed to proceed

### Requirement: CSRF and Session Token Composition

The publishable key gate MUST compose with the existing CSRF token and session token proof-of-origin checks. Neither CSRF validation nor session HMAC logic SHALL be removed or weakened. The real `csrf_secret` MUST NOT be exposed in client-side JS.

#### Scenario: All checks pass

- GIVEN a valid publishable key, allowlisted origin, valid CSRF token, and valid session token
- WHEN a request arrives at `/api/v1/chat`
- THEN all middleware passes and the request is processed

#### Scenario: Key valid but CSRF missing

- GIVEN a valid publishable key and allowlisted origin but no `X-CSRF-Token`
- WHEN a request arrives at `/api/v1/chat`
- THEN the response MUST be HTTP 403 (CSRF check fails independently)

---

## New Capability: widget-distribution

### Requirement: Versioned Immutable Widget URL

The system MUST serve the minified widget at `/widget/<version>/widget.min.js` with `Cache-Control: public, max-age=31536000, immutable`. The version string MUST be injected at build time.

#### Scenario: Versioned path served with immutable cache

- GIVEN the widget is built with `VERSION=1.0.0`
- WHEN a client requests `GET /widget/1.0.0/widget.min.js`
- THEN the response is HTTP 200, `Content-Type: application/javascript`, and includes `Cache-Control: public, max-age=31536000, immutable`

#### Scenario: Unknown version returns 404

- GIVEN no widget exists for version `9.9.9`
- WHEN a client requests `GET /widget/9.9.9/widget.min.js`
- THEN the response MUST be HTTP 404

### Requirement: Legacy URL Backward Compatibility

The legacy `/widget.js` URL MUST remain resolvable (via alias or redirect) so existing embed snippets do not break.

#### Scenario: Legacy URL resolves

- GIVEN an existing embed using `src="/widget.js"`
- WHEN the browser requests `GET /widget.js`
- THEN the response is HTTP 200 or HTTP 3xx redirect to the current versioned path

### Requirement: Widget Build Is Minified

The widget asset served at the versioned path MUST be the minified output of the esbuild build step. The unminified source MUST NOT be served at the versioned path.

#### Scenario: Minified asset served

- GIVEN the esbuild Docker build stage completes
- WHEN the built asset is requested
- THEN the response body is minified (no uncompressed source comments, significantly smaller than hand-written source)

---

## Modified Capability: embed-cors

### Requirement: Wildcard Origin Rejection

`collect_embed_origins` MUST explicitly reject `"*"` entries. If any tenant's `embed_origins` list contains `"*"`, the system MUST raise an error or omit the value — a wildcard MUST NOT be compiled into the CORS allow-origin regex.

(Previously: no explicit guard against `"*"` in `embed_origins`)

#### Scenario: Wildcard rejected at config load

- GIVEN a tenant config with `embed_origins: ["*"]`
- WHEN `collect_embed_origins` processes that tenant
- THEN `"*"` is NOT included in the resulting regex and an error or warning is raised

#### Scenario: Legitimate origins unaffected

- GIVEN a tenant config with `embed_origins: ["https://tenant.com", "http://localhost:*"]`
- WHEN `collect_embed_origins` processes that tenant
- THEN both origins are included in the CORS regex unchanged

### Requirement: Cross-Origin Embedding Permitted for Allowlisted Origins

Allowlisted cross-origin requests from `embed_origins` MUST receive correct CORS headers (`Access-Control-Allow-Origin`, `Access-Control-Allow-Credentials: true`). `connect-src 'self'` in CSP is NOT the gate for cross-origin widget embedding and MUST NOT be treated as a blocker.

#### Scenario: Allowlisted cross-origin request

- GIVEN a tenant with `embed_origins: ["https://tenant.com"]`
- WHEN a preflight `OPTIONS` request arrives from `https://tenant.com`
- THEN `Access-Control-Allow-Origin: https://tenant.com` and `Access-Control-Allow-Credentials: true` are present in the response

---

## Security Non-Goals (Explicit)

- Client-side JS MUST NOT contain any real secret (csrf_secret, private keys).
- Obfuscation of `widget.min.js` is NOT a security control. If applied, it MUST be labeled as defense-in-depth / size reduction only.

---

## Out of Scope

- Per-tenant-landings (parallel change — coordinate `tenant.config.json` schema: `publishable_key` + `embed_origins` fields must not conflict on `_template` merge).
- CSP `connect-src` changes.
- New secret handling beyond the publishable key field.
