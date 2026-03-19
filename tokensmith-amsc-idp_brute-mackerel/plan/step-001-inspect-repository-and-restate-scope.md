# Step 1: Inspect repository and restate scope

## Status
completed

## Repository inspection notes

- Language/tooling: Go modules, chi router, zerolog, golang-jwt/v5, casbin. CLI via cobra.
- Key areas
  - pkg/token: TSClaims (typed), TokenManager (mint/parse), errors.
  - pkg/tokenservice: TokenService HTTP server (health, JWKS, token exchange, service token), OIDC provider wiring, group→scope mapping.
  - pkg/oidc: provider interface, simple discovery + remote introspection; “local” JWKS validation is stubby/incomplete.
  - pkg/keys: RSA/EC key generation, load/save PEM, FIPS alg validation helpers.
  - pkg/authn: JWT validation middleware with issuer/audience checks, JWKS caching, principal mapping hook.
  - pkg/authz: Casbin-first authorization contract + middleware.
  - middleware (legacy module): standalone JWT middleware exposing legacy context keys.
  - cmd/tokenservice: CLI (serve, generate-config) composing TokenService + key management.
  - tests: unit tests across packages; integration env under tests/integration/ (Authelia, Traefik, docker-compose).
  - docs: AuthZ model/ops/specs, CLI/env refs, migration notes, security notes.

- Token flows present
  - External OIDC → TokenService.ExchangeToken → internal JWT issuance using TokenManager.
  - Service-to-service issuance via TokenService.GenerateServiceToken and /service/token endpoint.
  - Verification:
    - pkg/authn middleware for services (preferred, principal mapping → authz middleware).
    - legacy middleware module exporting legacy context claims.

- Endpoints and routing (chi)
  - GET /health
  - GET /.well-known/jwks.json (JWKS exposure)
  - POST/GET /oauth/token (custom token exchange; guarded by oidc.RequireToken + RequireValidToken)
  - POST /service/token (service token minting)

- Known issues/risks noticed during inspection
  - cmd/tokenservice/serve.go constructs a keys.KeyManager but never passes it into tokenservice.NewTokenService (nil is passed), which will cause runtime nil dereference when signing.
  - JWKS handler returns non-standard fields: “n” as big.Int decimal string and “e” as int; JWKS requires base64url-encoded modulus/exponent and appropriate key material. Key ID (kid) is timestamp-based and unstable per request.
  - pkg/oidc SimpleProvider “local” validation returns the raw JWK map as a key to jwt.Parse, which won’t verify signatures; it will reliably fall back to remote introspection. Acceptable as a placeholder but not real local validation.
  - Exchange handler trusts request body scope/target_service to override derived values; this contradicts “signed claims are authoritative”. Needs tightening.
  - No issuer metadata endpoint (/.well-known/openid-configuration). Only JWKS exists.
  - No persistence layer; no revocation store; any revocation must be interface-only or reuse existing patterns without adding a datastore.

## Existing relevant modules/endpoints/config patterns

- Modules/packages
  - token: TSClaims, TokenManager with default PS256, ExtendedClaims for additional custom claims.
  - tokenservice: ExchangeToken, GenerateServiceToken, ValidateToken, JWKSHandler, TokenExchangeHandler, ServiceTokenHandler, HealthHandler, Start.
  - oidc: Provider interface; SimpleProvider with discovery, JWKS retrieval, remote introspection.
  - keys: KeyManager (RSA/EC), FIPS-approved alg set, signing method helper.
  - authn: request AuthN middleware with issuer/audience enforcement, JWKS caching, deterministic TTLs, mapper hook to authz.Principal.
  - authz: Casbin-first policy engine, middleware, route helpers, decision records.
  - middleware (legacy): JWT verification exposing legacy context keys (jwt_claims, jwt_raw_claims).

- Endpoints
  - /.well-known/jwks.json (present; needs spec-compliant JWKs and stable kid)
  - /oauth/token (present; not RFC 8693 format yet)
  - /service/token (present; authN/authorization scaffolding TODOs in code)
  - /health (present)

- Configuration patterns
  - CLI flags for issuer/port/cluster-id/openchami-id/oidc issuer+client creds, key-file/key-dir, non-enforcing.
  - FileConfig (JSON) for GroupScopes mapping via tokenservice.config.go; backward-compat exists for groupScopes.
  - Env var support only for OIDC_CLIENT_ID / OIDC_CLIENT_SECRET via serve.go fallback.
  - No setting yet for a configurable project/accounting context claim.

- Testing/docs
  - Unit tests across token, keys, authn, authz, tokenservice.
  - Integration harness present (Authelia) but not executed here.
  - Docs cover AuthZ contract, ops, CLI/env, migration, security notes.

## Scoped problem statement with assumptions and risks

Objectives (from plan and constraints):
- Introduce a configurable signed project/accounting context claim.
  - Default claim name: project_accounting_context.
  - Keep the field name configurable via config/env/flags.
  - Preserve backward compatibility where practical (aliases, transitional parsing).
  - Do not reference private design docs.
- Formalize typed claim model extensions (issuer, project scoping, service identity, exchange/delegation/actor), with clear authority rules: signed token claims win over any unsigned request data.
- Implement issuer metadata exposure and correct JWKS per repository conventions (/.well-known/*), driven by configured issuer and key manager, without inventing new key lifecycle.
- Add project-scoped minting and deny-by-default policy interfaces for minting/exchange/delegation/narrowing; no privilege expansion by default.
- Service token issuance aligned with typed claims and project/policy model, without breaking user flows.
- Minimal scaffolding for sender-constrained/proof-binding (structure only, no production support claims).
- RFC 8693-compliant token exchange subset with strict input validation and clear error mapping.
- Delegation and act claim semantics with explicit non-expansion rules; if full enforcement is too large, implement model + hooks and document boundaries.
- Logging/audit additions that redact sensitive values; revocation limited to interfaces or existing storage patterns only.
- Update docs/examples/migration notes; add compatibility tests/notes; ensure no trust in unsigned context over signed claims.

Assumptions
- No new datastore may be introduced; any revocation must be interface-only unless an existing pattern is already in-repo.
- JWKS exposure must be spec-compliant; we will stabilize kid and base64url-encode RSA fields.
- Backward compatibility is required for:
  - legacy context keys (middleware module) and root tokensmith context helpers (already present and should remain).
  - groupScopes mapping in FileConfig.
  - existing token mint/verify behavior where not explicitly changed by new validations.
- OIDC local validation remains best-effort; remote introspection continues as the reliable path unless replaced.

Repo-specific constraints/limits
- Current JWKS handler is not spec-compliant and exposes unstable kid; will be replaced/extended.
- serve.go must be corrected to pass the KeyManager to NewTokenService; otherwise server will panic when signing. This is a pre-existing defect to address before endpoint behavior can be validated at runtime.
- No persistence interfaces for revocation; add interfaces only.
- Token exchange endpoint currently deviates from RFC 8693. Migration will need to either:
  - support RFC 8693 at /oauth/token with grant_type, or
  - add a new path while keeping legacy behavior behind a compatibility flag, with deprecation notes.
- SimpleProvider local JWKS validation is incomplete; avoid claiming production-grade local introspection.

Risks
- Tightening authority rules (rejecting unsigned overrides) may affect existing clients that relied on request-supplied scope/target_service; must provide compatibility/deprecation path.
- Changing JWKS shape and kid stability impacts any consumers that cached previous values; implement stable kid and document change; tests must pin expected JWKS format.
- Introducing configurable context-claim name requires careful defaulting and aliasing to avoid breaking downstreams.

## Step outputs mapping
- Repository inspection notes: above.
- List of existing relevant modules/endpoints/config patterns: above.
- Scoped problem statement with assumptions and risks: above.

## Next
- Step 2: Establish baseline behavior and reproducible test commands; run go test, lint/vet if present; capture pre-existing failures (notably tokenservice JWKS and serve wiring likely wouldn’t affect unit tests but would break runtime).
