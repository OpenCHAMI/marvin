# Step 1: Repository inspection and architecture baseline

Scope: Identify token issuance/validation flows, HTTP routing, configuration, key management, policy hooks, and claim structures. Document legacy/compat notes and additive extension points.

Repo layout (key paths)
- cmd/tokenservice: CLI (generate-config, serve).
- pkg/tokenservice: HTTP service (/.well-known/jwks.json, /oauth/token exchange, /service/token, /health).
- pkg/token: Claims (TSClaims), TokenManager (sign/parse), errors.
- pkg/keys: KeyManager, FIPS alg validation, signing method helpers.
- pkg/oidc: Simple provider, introspection middleware.
- middleware: Standalone chi JWT middleware with Casbin policy integration.

Endpoints observed
- GET /health: basic health data.
- GET /.well-known/jwks.json: ad-hoc JWKS generation (kid generated per request; alg hardcoded RS256; modulus exponent serialization ad-hoc).
- /oauth/token [POST,GET]: TokenExchangeHandler; requires OIDC bearer token via Authorization header. Body: {scope:[], target_service:string}. Returns {access_token, token_type}.
- POST /service/token: service-to-service issuance (very basic authentication via X-Service-API-Key stub).

Token issuance/validation flows
- pkg/tokenservice.ExchangeToken: validates upstream OIDC via oidc.Provider.IntrospectToken; maps upstream claims to TSClaims, sets ClusterID/OpenCHAMIID, derives Scope from groups via Config.GroupScopes, overrides scope/audience from request payload via context. Calls TokenManager.GenerateToken to mint.
- pkg/tokenservice.GenerateServiceToken: builds TSClaims with service semantics and fixed NIST-like extras, then TokenManager.GenerateToken.
- pkg/token.TokenManager.GenerateToken(+WithClaims): validates TSClaims via TSClaims.Validate(enforce), signs with configured algorithm (default PS256) via keys.GetSigningMethod; generates jti and nonce. ParseToken verifies signature with KeyManager public key and re-validates TSClaims.

Configuration and knobs
- cmd/tokenservice serve flags: issuer, port, cluster-id, openchami-id, oidc-issuer, oidc-client-id/secret, key-file, key-dir, non-enforcing.
- pkg/tokenservice.FileConfig: groupScopes mapping for deriving scopes from upstream groups. Backward compatible default when no file.

Key management and JWKS
- pkg/keys: FIPS-approved alg set; signing method mapping. KeyManager supports RSA and EC generation, save/load PEM. Min RSA 2048.
- JWKS endpoint in pkg/tokenservice builds "kid" from modulus bytes and timestamp; alg is hardcoded to RS256; public components are serialized as N and E (E as int) but without base64url per JWK; Key selection not wired into signing header (TokenManager doesn't set kid). Rotation/overlap semantics not enforced.

Claims and formats
- TSClaims embeds jwt.RegisteredClaims and a wide set of additional fields (OIDC-like + OpenCHAMI fields). No explicit token type/profile field.
- Validation in TSClaims enforces many NIST-style requirements, including: auth_level present, >=2 auth_factors, auth_methods not empty, session_id/exp present, session duration <= 24h. Service tokens fill these accordingly.

Legacy/compat notes
- Middleware tests build tokens with RS256; TokenManager defaults PS256. Keep RS256 accepted to preserve existing consumers; already allowed via keys.ValidateAlgorithm.
- JWKS handler uses RS256 in metadata even if signing uses PS256; kid ephemeral. Needs harmonization to avoid validation failures for JWKS-based clients.
- No explicit project/accounting context claim exists today. Adding must be configurable and default to key name `project_accounting_context`, with migration options to read legacy names if any are later discovered. Downstream must read via normalized accessor, not raw claim name.

Extension points for additive changes
- Add a typed token profile discriminator in TSClaims (e.g., token_type) with backward-compat default and non-breaking behavior.
- Introduce normalized accessor package for project/accounting context; support config for claim naming and dual-read/write modes.
- Add Key ID (kid) assignment in TokenManager header and stable JWKS representation; document allowed algorithms and rotation windows.
- Formalize error model for /oauth/token and /service/token to avoid plain text http.Error bodies.

Compatibility expectations
- Preserve RS256 verification in middleware.
- Preserve groupScopes mapping and file schema.
- Preserve existing endpoints and request/response shapes; layering typed profiles must not break parsing.

Identified risks
- JWKS non-compliant fields (n/e encoding) and ephemeral kid lead to cache misses and failed verification.
- TSClaims.Validate is very strict; ExchangeToken assumes upstream claims include all NIST-style fields; real OIDC providers may not provide those. NonEnforcing exists but endpoint returns 401 on missing fields.

Additive change loci
- pkg/token: define typed profiles; add project context normalization helper.
- pkg/tokenservice: wire config for project context claim; integrate adapters; set kid in token header; implement stable JWKS and issuer metadata.
- middleware: prefer reading normalized project context rather than raw claim.

This baseline will drive the proposal and subsequent steps.
