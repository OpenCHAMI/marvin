# Step 1 — Repository reconnaissance and trust-boundary inventory

Date: 2026-03-19
Repo: repos/tokensmith
Executor: Marvin

This document inventories the current implementation relevant to token minting/validation, authn/authz boundaries, HTTP routing, configuration, key management, and tests. It also captures legacy compatibility constraints and feasible subject-token trust models.

---

## Architecture inventory

Components by concern:

- Token/claims
  - pkg/token
    - token.TSClaims: typed JWT claims. Includes RegisteredClaims (iss, sub, aud, exp, nbf, iat, jti) and many additional fields (nonce, name/email, email_verified, scope []string, NIST-centric: auth_level, auth_factors, auth_methods, session_id, session_exp, auth_events; cluster_id, openchami_id).
    - token.Validate(enforce bool): strict checks when enforce=true. Requires iss/sub/aud and all NIST/session fields; enforces session <= 24h.
    - token.TokenManager: signing/parsing using FIPS-approved algs (via pkg/keys). Supports GenerateToken, GenerateTokenWithClaims (additional map claims), ParseToken, and a helper GenerateServiceToken.

- Keys and algorithms
  - pkg/keys
    - KeyManager: load/generate/save RSA or ECDSA keys; returns private/public keys; min RSA 2048; ECDSA P-256+.
    - fips.go: FIPSApprovedAlgorithms set; ValidateAlgorithm; GetSigningMethod; GetFIPSApprovedAlgorithms (for jwt parser allowlist).

- AuthN (JWT verification)
  - pkg/authn
    - Middleware: bearer token extraction + jwt/v5 parsing, FIPS alg allowlist, iat/exp checks, optional issuer/audience checks, JWKS cache with soft/hard TTL and deterministic refresh, PrincipalMapper to authz.Principal. Stores principal and verified claims in context.
    - jwks_cache: fetches JWKS from configured URLs, parses with keyfunc, caches by KID.

- AuthZ (Casbin-first)
  - pkg/authz
    - Engine: Builder to construct casbin enforcer via presets or model/policy paths; deny-by-default contract enforced by chi middleware.
    - chi middleware: attaches route-level object/action requirements, performs authorization via Authorizer, returns structured JSON denial {code, message, request_id, policy_version, decision}. Modes: off/shadow/enforce.

- OIDC provider utilities
  - pkg/oidc
    - Provider interface; SimpleProvider with discovery + JWKS fetch + remote introspection. Local validation path exists but currently returns generic key maps (not crypto.PublicKey), so practical behavior is remote introspection.
    - Middleware helpers RequireToken and RequireValidToken, used by token exchange endpoints.

- Token exchange service (HTTP)
  - cmd/tokenservice (CLI)
    - tokensmith serve: loads config, generates or loads RSA keys, constructs tokenservice, starts HTTP.
  - pkg/tokenservice
    - Config: Issuer, GroupScopes (group→scopes mapping), ClusterID, OpenCHAMIID, NonEnforcing (relaxes claim validation in TokenManager), OIDC issuer/client creds.
    - TokenService:
      - ExchangeToken(ctx, idtoken): uses OIDC Provider IntrospectToken (remote), requires Active=true. Builds TSClaims from introspection: iss/sub/exp/iat; copies aud/name/email/email_verified when present. Requires NIST+session claims in introspection: auth_level, auth_factors, auth_methods, session_id, session_exp, auth_events; error otherwise. Derives Scope from groups via GroupScopes mapping; may be overridden by request body (scope). Audience may be overridden by request body target_service.
      - GenerateServiceToken: mints service→service tokens with 1h TTL, sets NIST-like defaults: auth_level=IAL2, auth_factors=2, auth_methods=[service, certificate], session_id/exp, auth_events=[service_auth].
      - ValidateToken: wraps TokenManager.ParseToken.
      - JWKSHandler: publishes /.well-known/jwks.json. Generates a per-request kid and emits a non-standard JWKS (n uses decimal string; e is emitted as integer). Not rollover-safe or standards-compliant today.
      - TokenExchangeHandler: HTTP handler behind /oauth/token using Authorization: Bearer <subject token> and a JSON body {scope:[], target_service:string}. Not RFC 8693 wire shape; no explicit OAuth error codes.
      - ServiceTokenHandler: POST /service/token, authenticates via placeholder API key, validates requested target+scopes via placeholder allow-lists, returns access_token in JSON.

- Context helpers (compatibility)
  - tokensmith/context.go: canonical principal context key and legacy JWT-claims context key string "jwt_claims" for back-compat with the historical middleware submodule. PrincipalFromContext prefers new principal; falls back to legacy TSClaims by mapping sub→ID and scope→roles.

- Tests
  - pkg/token: tests for signing/alg selection, claim round-trips, required-claim failures.
  - pkg/authn: middleware issuer/audience checks, JWKS cache behaviors.
  - pkg/authz: builder + chi middleware behaviors, deny-by-default, error schema.
  - pkg/tokenservice: ExchangeToken with mocked OIDC provider (expects NIST/session claims in introspection), service token generation, validation errors.

---

## Current authn/authz boundaries

- Subject token (external):
  - Authentication: In HTTP, middleware stack uses pkg/oidc.RequireToken + RequireValidToken(SimpleProvider). The SimpleProvider path effectively performs remote introspection against the configured OIDC issuer; local JWKS validation path is present but not effectively wired for crypto keys yet.
  - Authorization: None applied before exchange beyond introspection Active flag. No client authentication to bind the caller to a client identity.

- Minted internal token (output of exchange or service issuance):
  - Signing: via TokenManager and local private key.
  - Authorization for scopes/audience: Derived from group→scope mapping or overridden by request; no policy engine consulted. Deny-by-default semantics are not present in issuance; there is no policy hook today.

- Service-to-service issuance:
  - Authentication: Placeholder API key check (X-Service-API-Key) mapped to a mock service ID; no mTLS or JWT client auth.
  - Authorization: Placeholder allow-lists in code for allowed targets/scopes.

- Downstream service request handling:
  - Authentication: pkg/authn middleware (FIPS algs, JWKS or static key validation) produces authz.Principal and verified claims.
  - Authorization: pkg/authz chi middleware enforces deny-by-default route requirements and structured error responses, using Casbin policies.

---

## Token formats, claims, and compatibility constraints

- Internal token format: JWT signed with local key (default alg PS256; tests use RS256). Claims per token.TSClaims. The following are enforced when TokenManager.enforce=true (default unless NonEnforcing is set in service config):
  - Required: iss, sub, aud, auth_level, auth_factors>=2, auth_methods (non-empty), session_id, session_exp (<= 24h from iat).
  - Optional but supported: nonce, name/email/email_verified, amr/acr, scope []string, cluster_id, openchami_id, auth_events.
- Subject token input format: validated remotely via OIDC introspection. Code expects many NIST/session claims present in introspection to shape the minted token; missing fields cause exchange to fail. This is stricter than most OIDC providers by default.
- Scope representation: []string claim under key "scope" (array), not space-delimited string. Downstream authz Principal roles derive from this array in legacy compatibility.
- Context/accounting claim: Not present today. Requirement: introduce a configurable project/accounting context claim whose field name defaults to "project_accounting_context" and remains configurable. Backward compatibility: initial rollout should treat it as optional unless explicitly required by policy or endpoint.
- JWKS publication today: non-standard encoding and non-stable kid; downstream validators relying on JWKS may fail. Needs standards-compliant shape and stable key IDs.

Legacy/compat risks:
- Tight TSClaims.Validate and exchange path requiring NIST/session claims will break callers whose upstream tokens lack those claims. NonEnforcing mode exists but only affects TokenManager.Validate, not the exchange’s own required-claim checks.
- JWKS shape is incompatible with standard JWT libraries; changing it may impact any existing consumers relying on current behavior (unlikely but must be noted). Kid must stabilize before clients key on it.
- Service issuance and exchange wire formats are not RFC 8693; migrating to standards-compliant flows will require new endpoints or transitional behavior.

---

## Feasible trust-boundary options and client authentication methods (first pass assessment)

Subject-token trust models we can support in the near term:
- Local issuer only (recommended starting point): accept and validate subject tokens issued by a configured external OIDC issuer via remote introspection, optionally enabling local JWT validation using the issuer’s JWKS when properly implemented. Simpler rollout; clear trust boundary.
- Multiple trusted external issuers (deferred): would require trust store and per-issuer metadata/JWKS, plus claim normalization. Not currently wired.
- Introspection hooks (possible): SimpleProvider already models remote introspection; can abstract to support pluggable introspection backends.

Client authentication for RFC 8693 token exchange:
- Private clients only in first pass. Supported methods:
  - HTTP Basic (client_id/client_secret) at token endpoint.
  - Optionally, mTLS-bound clients (deferred unless already in deployment).
- Public clients: out-of-scope for first pass; must be rejected with invalid_client.

How client identity feeds policy:
- Authenticated client_id, requested audience/resource/scope, subject-token properties, and optional project_accounting_context feed a policy engine with deny-by-default semantics for both minting and exchange decisions.

---

## Initial conclusions guiding design

- Add a standards-compliant issuer metadata and JWKS surface with stable issuer value and KIDs.
- Implement RFC 8693-aligned exchange endpoint alongside (or replacing) the ad-hoc /oauth/token JSON body. Enforce private client authentication.
- Introduce typed, minimally-scoped claim extensions for service identity and project/accounting context; keep the context claim name configurable with default "project_accounting_context"; treat as optional initially for backward compatibility.
- Add policy hooks (deny-by-default) to issuance and exchange paths, taking authenticated client identity, requested scope/audience, subject-token attributes, and optional project context as inputs.
- Maintain backward-compatible behaviors where possible: keep current endpoints until standards-compliant ones are proven and documented; gate stricter behaviors behind config flags with safe defaults.

---

## Success criteria trace

- Components needed for minting, validation, metadata, and exchange identified: yes.
- Authn/authz decision points documented: yes.
- Legacy token and caller compatibility risks listed: yes.
- Feasible trust-boundary options clear enough to drive design choices: yes.
