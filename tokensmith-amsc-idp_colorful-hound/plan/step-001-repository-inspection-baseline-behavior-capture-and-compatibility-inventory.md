# Step 1: Repository inspection, baseline behavior capture, and compatibility inventory

Status: complete

Scope of inspection
- Root module: github.com/openchami/tokensmith
- Nested legacy module: github.com/openchami/tokensmith/middleware
- Primary areas inspected: token issuance/validation, configuration, endpoints, claim shapes, signing keys, OIDC handling, AuthN/AuthZ middleware, CLI

Repository architecture summary
- cmd/
  - tokenservice/
    - main.go: CLI root with generate-config and serve
    - serve.go: starts HTTP server, wires key management and TokenService config
- pkg/
  - token/
    - claims.go: TSClaims typed claim struct + Validate(enforce)
    - token_manager.go: TokenManager (issue/parse; service token helper)
    - errors.go: shared token error values
  - keys/
    - key.go: RSA/ECDSA key generation, load/save, accessors
    - fips.go: FIPS-approved alg set, ValidateAlgorithm, GetSigningMethod, approved alg listing
  - tokenservice/
    - service.go: TokenService (handlers for exchange, service token, JWKS, health; HTTP router)
    - config.go: file config schema (groupScopes) and load/save
    - client.go: example client for service tokens
  - oidc/
    - provider.go: Provider interface + types (metadata, introspection)
    - simple.go: SimpleProvider (discovery, JWKS fetch, basic introspection/local-validate attempt)
    - middleware.go: RequireToken + RequireValidToken (uses Provider)
  - authn/
    - middleware.go: new AuthN middleware (issuer/aud validation, FIPS algs, JWKS cache, principal mapping)
    - jwks_cache.go: deterministic JWKS caching
    - context.go: principal + verified-claims context helpers
  - authz/
    - contract types (Mode/Decision/ErrorCode/ErrorResponse) and Casbin-first middleware, loader, presets, etc.
- middleware/ (separate legacy Go module)
  - README + middleware.go: legacy JWT middleware with JWKS refresh, context keys, optional Casbin gate
- docs/
  - AuthZ contract/specs, guides, CLI/env refs, migration notes
- tests/
  - integration harness directory exists (contents not executed here)

Baseline behavior notes (issuance, validation, auth flows)
- Issuance
  - TokenManager default alg: PS256 (RSASSA-PSS); SetSigningAlgorithm validates against FIPS list.
  - GenerateToken(claims):
    - If nil, initializes with defaults and sets cluster_id and openchami_id.
    - Validates claims via TSClaims.Validate(enforce flag from TokenService config).
    - Adds jti (uuid v4) and nonce.
    - Signs with current alg using keys.KeyManager private key.
  - GenerateTokenWithClaims: same path with AdditionalClaims marshaled into payload.
  - Service tokens via TokenManager.GenerateServiceToken and TokenService.GenerateServiceToken set short lifetimes and include scope, cluster_id, openchami_id, plus NIST-like requirements for service tokens.
- Claims/validation
  - TSClaims embeds jwt.RegisteredClaims and adds many fields: nonce, name, email, email_verified, auth_time, amr, acr, scope, auth_level, auth_factors, auth_methods, session_id, session_exp, auth_events, cluster_id, openchami_id.
  - Validate(enforce):
    - RFC time checks exp/nbf; mandatory iss/sub/aud.
    - Additional strict rules (NIST-esque): requires auth_level, auth_methods, session_id, session_exp; requires at least 2 auth_factors; caps session duration to 24h relative to iat.
    - When enforce=false, logs violations instead of erroring.
- Validation (parse)
  - TokenManager.ParseToken uses KeyManager public key to verify; re-validates claims with same TSClaims.Validate path; attempts to provide raw claims map.
- Token exchange (TokenService.ExchangeToken)
  - Requires Authorization: Bearer upstream token; uses oidc.Provider to Introspect.
  - Maps introspection fields → TSClaims: iss (service Issuer), sub (username), exp/iat; audience if present; name/email/email_verified; copies OpenCHAMI cluster_id/openchami_id.
  - Enforces presence/types of: auth_level (string), auth_factors (number), auth_methods ([]string), session_id (string), session_exp (number), auth_events ([]string). Missing or wrong type returns 401/500 from handler.
  - Derives internal scope from upstream groups via GroupScopes mapping (deduplicated). Request body scope and target_service override derived values if provided.
  - Issues internal JWT via TokenManager.GenerateToken.
- Service-token issuance (TokenService.ServiceTokenHandler)
  - POST /service/token; authenticates requester via X-Service-API-Key (placeholder validateServiceAPIKey returns "mock-service").
  - Validates allowed targets/scopes via placeholders; issues service token via GenerateServiceToken.
- OIDC middleware (pkg/oidc)
  - RequireToken parses Authorization header and stashes raw token in context.
  - RequireValidToken(provider) calls provider.IntrospectToken; requires Active=true; stashes introspection in context.
- AuthN middleware (pkg/authn)
  - Enforce-by-default; configurable off mode; validates issuer and audience by default; ensures FIPS alg; supports static keys and JWKS URLs with caching TTLs; maps verified claims → authz.Principal via provided Mapper (default: principal.ID=sub). Adds verified claims map to context.
- Legacy JWT middleware (middleware/)
  - Provides JWT verification using JWKS (keyfunc), optional Casbin check, context keys: jwt_claims and jwt_raw_claims. Remains for backward compatibility.

Inventory: public HTTP endpoints (TokenService)
- GET /health → {status, service, issuer, cluster_id, openchami_id, oidc_issuer}
- GET /.well-known/jwks.json → JWKS (see compatibility notes about format/alg/kid)
- POST /oauth/token (also GET) → token exchange; protected by RequireToken + RequireValidToken
- POST /service/token → service-token issuance (API key–gated, placeholders today)

Inventory: CLI/config surface
- Commands: tokensmith generate-config; tokensmith serve
- Global flag: --config
- serve flags:
  - --issuer (default http://tokensmith:8080)
  - --port (default 8080)
  - --cluster-id (default cl-F00F00F00)
  - --openchami-id (default oc-F00F00F00)
  - --oidc-issuer (default http://hydra:4444)
  - --oidc-client-id (or env OIDC_CLIENT_ID)
  - --oidc-client-secret (or env OIDC_CLIENT_SECRET)
  - --key-file (private key path) | else generate to --key-dir
  - --key-dir (output dir for generated keys)
  - --non-enforcing (skip strict TSClaims validation; logs only)
- File config schema (config.json):
  - groupScopes: map[string][]string used to derive internal scopes from upstream groups (loaded by serve)
- Env vars referenced in docs:
  - OIDC_CLIENT_ID, OIDC_CLIENT_SECRET (serve)
  - TOKENSMITH_POLICY_DIR, AUTHZ_POLICY_DIR, TOKENSMITH_AUTHZ_CACHE_SIZE (AuthZ internals)

Inventory: claim contracts in use today
- Core JWT: iss, sub, aud, exp, nbf, iat, jti
- OpenCHAMI: cluster_id (string), openchami_id (string)
- OIDC-ish: name, email, email_verified, auth_time, amr, acr
- OAuth-ish: scope ([]string)
- NIST/SP800-63B-inspired: auth_level (string), auth_factors (int>=2), auth_methods ([]string non-empty), session_id (string), session_exp (int UNIX), auth_events ([]string)
- Not present today: any project/accounting context claim. No code paths reference project_accounting_context or similar.

Signing key handling and JWKS exposure
- keys.KeyManager supports RSA (min 2048) and ECDSA P-256; save/load as PKCS#1 for RSA; exposes GetPrivateKey/GetPublicKey.
- TokenManager default alg is PS256. SetSigningAlgorithm enforces FIPS-approved list.
- JWKS handler returns a minimal JWKS with fields: kty=RSA, use=sig, alg=RS256, kid=derived per request (timestamp + first 8 bytes of modulus), n=publicKey.N.String(), e=publicKey.E (int). See risks below.

Auth patterns exposed to integrators
- New stack: pkg/authn + pkg/authz middlewares and contracts
- Legacy: middleware/ JWT middleware module for direct JWT validation and scope checks; README documents context keys and usage
- Context helpers at root package (tokensmith/context.go) bridge new principal model with legacy claims context key ("jwt_claims").

Compatibility and deprecation risk list
Must-stay-stable (public contracts to preserve)
- HTTP routes already exposed:
  - /.well-known/jwks.json (path must remain; response may be fixed to standards but must not remove endpoint)
  - /oauth/token (exchange)
  - /service/token (service issuance)
  - /health
- CLI flags and behavior for serve and generate-config
- File config key groupScopes semantics (deriving internal scopes from groups)
- Go package surface:
  - pkg/token: TokenManager API, TSClaims field names (JSON keys), Validate behavior toggled by NonEnforcing
  - pkg/authn: Options fields and middleware function signature
  - pkg/authz: contract types and middleware behavior documented in docs
  - middleware/ (legacy module): context keys (jwt_claims, jwt_raw_claims), basic behavior; should continue to work
  - tokensmith/context.go: legacyClaimsContextKey string "jwt_claims" and PrincipalFromContext fallback behavior

Safe-to-extend (additive without breaking)
- Introduce new optional claim for project/accounting context. Requirements:
  - Configurable field name; default to project_accounting_context
  - Omit if not configured; ensure ignoring by existing validators unless enforce policy explicitly requires
  - Document claim mapping but do not require downstreams to read it
- Add issuer metadata endpoint (OpenID Connect Discovery or OAuth 2.0 AS Metadata) alongside existing JWKS
- Add feature flags/config keys for new capabilities (exchange policy, audit, revocation hooks) with defaults disabled/off
- Stabilize JWKS kid and algorithm reporting while keeping path stable
- Expand oidc.Provider to a real local validation using JWKs (currently incomplete); ensure remote introspection remains fallback

Migration-sensitive / deprecation candidates (need explicit plan)
- JWKS handler inconsistencies:
  - Alg mismatch: TokenManager signs PS256 by default; JWKS currently declares alg RS256 always.
  - Key material encoding: JWKS uses decimal n and integer e; should be base64url per RFC 7517/7518.
  - Unstable kid: derived with timestamp; rotates every request. Kid should be a stable fingerprint of the key material.
  - Impact: External verifiers may fail or cache keys poorly. Fixing this is a behavior change but is standards compliance; announce and version as needed.
- TSClaims.Validate strict NIST-ish requirements:
  - Exchange path fails if upstream tokens lack auth_level/auth_factors/auth_methods/session_id/session_exp/auth_events.
  - Impact: Many OIDC providers may not supply these. Operators rely on --non-enforcing to bypass. Any change here must keep NonEnforcing honoring backward behavior.
- OIDC SimpleProvider local validation returns a JWK map as a crypto key; jwt/v5 will fail with non-crypto key types. Local validation likely non-functional; service relies on remote introspection. Fixing this changes behavior for deployments pointing at providers with valid JWKS.
- Legacy middleware vs new AuthN/AuthZ: maintain both for now. Any deprecation must provide a migration guide and keep context key compatibility.

Backward-compatibility constraints for future steps
- Do not rename or remove existing flags, endpoints, or JSON field names for existing claims.
- Any new config keys must default to disabled/off and not alter current flows unless explicitly enabled.
- The project/accounting context claim must be opt-in configurable; default field name project_accounting_context; absence must not break current validation; keep TSClaims.Validate backward-compatible unless explicitly enabled via feature flag.
- Fix JWKS to standards while preserving path and avoiding sudden alg changes:
  - Options: either align TokenManager default alg to RS256 for now or make JWKS reflect actual alg. Prefer making JWKS reflect algorithm in use; PS256 remains default.
  - Provide stable kid (fingerprint of key) and proper base64url values.
- Token exchange error model currently returns http.Error strings. Introducing a structured, standards-aligned error body must be gated or limited to the exchange endpoint while keeping HTTP status compatibility.

Observed tests (baseline coverage indicators)
- pkg/token/token_manager_test.go
- pkg/keys/key_test.go
- pkg/authn: jwks_cache_test.go, middleware_test.go
- pkg/authz: authorizer_test.go, middleware_test.go, etc.
- pkg/tokenservice/service_test.go
- middleware/auth_test.go

Gaps and observations
- No endpoint for issuer metadata (/.well-known/openid-configuration or oauth-authorization-server). Step 5 will need to define a contract and implement.
- JWKS implementation is non-standard (see above). Needs correction with tests.
- Exchange handler accepts GET and POST; GET with JSON body is unusual. Consider POST only for RFC 8693 alignment; preserve GET for compatibility if it exists in the wild.
- Service token path uses placeholder API key checks and allowed scopes/targets; formal policy boundary not yet implemented.
- No explicit audit logging or revocation hooks present.

Next-step dependencies/notes
- Baseline tests should pin current behavior for: routes, CLI flags, file config groupScopes, TSClaims JSON field names, NonEnforcing toggle, current exchange handler request/response shape, legacy middleware context keys. Add regression tests around JWKS current output before changing, with plan to migrate to standards-compliant output under a guarded change.
