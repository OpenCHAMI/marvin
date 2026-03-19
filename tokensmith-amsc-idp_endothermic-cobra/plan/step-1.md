# Step 1 — Repository inspection and auth architecture inventory

Date: 2026-03-19
Status: Completed

This step reviewed the codebase, config, and runtime wiring to baseline existing authentication/authorization components, token flows, key management, metadata exposure, and compatibility risks.

Tools and actions
- Listed repository structure and key packages.
- Read pkg/token, pkg/keys, pkg/oidc, pkg/authn, pkg/authz, and cmd/tokenservice.
- Did not execute code or tests (sandbox policy).

Architecture inventory
- Token minting/verification
  - pkg/token:
    - TSClaims: extended JWT claims struct (RFC 7519) + OIDC/NIST style fields; includes ClusterID and OpenCHAMIID.
    - Validate(enforce bool): strict checks for exp, nbf, iss, sub, aud and NIST fields (auth_level, auth_factors≥2, auth_methods, session_id/exp <=24h).
    - TokenManager: wraps key manager; signs with default PS256 (FIPS allowed set) but can switch; adds jti and nonce; supports GenerateToken, GenerateTokenWithClaims (extends via AdditionalClaims), ParseToken; GenerateServiceToken helper.
  - pkg/authn:
    - HTTP middleware for token verification with: issuer and audience validation, leeway, FIPS alg whitelist, static keys or JWKS fetching with deterministic caching and soft/hard TTL, principal mapping hook, and context utilities.
    - Unit tests cover issuer/audience enforcement, JWKS cache behavior, and failure modes.
- Authorization (AuthZ)
  - pkg/authz: Casbin-first authorization contract, middleware, policy loader, decision recording, embedded baseline policy. Independent of token minting.
- Token exchange/service
  - pkg/tokenservice:
    - Config includes Issuer, GroupScopes (group→scopes mapping), ClusterID, OpenCHAMIID, NonEnforcing, and OIDC provider settings.
    - OIDC provider abstraction (pkg/oidc) with a SimpleProvider for discovery, JWKS, and remote introspection fallback.
    - ExchangeToken(ctx, idtoken): introspects upstream token, projects claims into TSClaims, derives Scope from upstream groups via GroupScopes, allows request-specified scope/target_service override, then mints internal token.
    - JWKSHandler: exposes /.well-known/jwks.json.
    - TokenExchangeHandler: /oauth/token guarded by RequireToken + RequireValidToken, reads optional scope/target_service from body.
    - ServiceTokenHandler: service-to-service issuance (API-key placeholder auth; TODOs for policy/targets/scopes).
    - HealthHandler.
    - Start: chi router wiring.
  - cmd/tokenservice: cobra CLI (generate-config, serve). Serve loads/creates keys, builds Config, starts HTTP server.
- Key management and algorithms
  - pkg/keys: KeyManager supports RSA and ECDSA generation, load/save PEM (RSA PKCS#1), and exposes public/private keys. FIPS-approved alg set and validation, with GetSigningMethod mapping.

Current token formats, claims, issuers, validation paths
- Internal tokens: JWT signed with PS256 by default (TokenManager), can be RS/PS/ES.
- Claims: Standard RegisteredClaims + custom TSClaims fields:
  - OIDC-ish: name, email, email_verified, auth_time, amr, acr, scope (array).
  - NIST-ish: auth_level, auth_factors, auth_methods, session_id, session_exp, auth_events.
  - OpenCHAMI: cluster_id, openchami_id.
  - jti and nonce added at mint.
- Issuer/audience: set by TokenService (Issuer from config, Audience from upstream aud and/or target_service override). AuthN middleware enforces iss/aud where configured.
- Validation:
  - During minting: TSClaims.Validate with enforce flag (enforces strict NIST fields; NonEnforcing can relax logging-only in TokenService).
  - During request verification: pkg/authn middleware with FIPS alg allowlist, issuer/audience checks, JWKS/static verification.

Key storage/rotation approach and config sources
- Key storage: on-disk PEM via KeyManager (private.pem/public.pem) or load from --key-file.
- Rotation: no rotation manager; JWKSHandler fabricates a kid per response using modulus bytes and a timestamp (unstable). No stable KID or rotation semantics. No cache-control headers.
- Config sources:
  - CLI flags/env for serve, OIDC creds via env fallbacks.
  - FileConfig JSON for GroupScopes only (generate-config command). No global schema for token/profile settings.

Metadata/JWKS exposure
- /.well-known/jwks.json implemented ad hoc.
  - Issues:
    - kid is ephemeral (timestamp-based), not tied to an actual signing key ID.
    - n and e are emitted as decimal big.Int string and raw int, not base64url per JWK spec.
    - TokenManager does not set a kid header, so verifiers can’t match JWKS entries deterministically.
- No OAuth 2.0 Authorization Server Metadata (RFC 8414) or OIDC Discovery document.

Client auth mechanisms relevant to token exchange
- /oauth/token guarded by RequireToken + RequireValidToken using upstream ID token; no client authentication of the caller beyond upstream token validity.
- Service token issuance uses an X-Service-API-Key header placeholder; TODOs for policy/targets/scopes.

Error/response conventions
- Uses net/http http.Error with plain text; not RFC 6749/8693 JSON error bodies. TokenExchangeHandler returns {access_token, token_type} on success.
- AuthN middleware returns 401 with generic "invalid token" or missing bearer messages; avoids echoing token.

Logging/audit hooks
- chi middleware includes OpenCHAMI logger; no explicit audit events for mint/exchange/revocation.

Compatibility and migration risks
- JWKS correctness/compatibility risks:
  - Non-compliant JWK fields (e, n) and unstable kid will break standard verifiers and any local JWKS validation.
  - Tokens lack kid; rotation and multi-key scenarios are unsupported.
- OIDC SimpleProvider local validation is likely non-functional:
  - findKeyByID returns a raw key map, not a crypto.PublicKey, causing jwt.Parse to fail when attempting verification.
- CLI/serve wiring bug:
  - serve generates/loads keys into a KeyManager but then calls NewTokenService(nil, cfg); JWKS and signing will nil-deref at runtime. High priority to fix.
- Strict NIST claim enforcement in TSClaims.Validate means upstream providers must supply many non-standard claims (auth_level, auth_factors, auth_methods, session_id/exp, auth_events). Real providers often won’t. NonEnforcing mitigates partially but ExchangeToken also hard-requires them while mapping, returning errors before mint.
- No OAuth/OIDC metadata document; clients cannot auto-discover JWKS or token endpoints. Migration to standards will add endpoints; we must keep /.well-known/jwks.json path.
- GroupScopes projection is bespoke; keep for backward compatibility while clarifying it’s distinct from AuthZ Casbin policy.
- Service token issuance semantics are not clearly separated from user/delegated tokens; placeholders for auth/authorization may cause confusion.

Standards-aligned areas
- JWT signing via FIPS-approved algorithms; enforcement in authn middleware for alg whitelist.
- AuthN middleware covers issuer/audience/time validation and deterministic JWKS cache with soft/hard TTLs.

New work required (high level)
- Correct JWKS formatting and stable kid derived from key material; include kid in token headers; document cache-control and rotation.
- Add OAuth AS metadata and/or OIDC Discovery (decide scope); align error bodies to OAuth error format.
- Fix serve wiring to pass KeyManager; add startup-time config validation for token/profile settings.
- Introduce configurable project/accounting context claim name (default: project_accounting_context) with backward compatibility.
- Clarify and harden service identity issuance (client auth, audiences, policy hooks); scaffold sender constraint.
- Implement RFC 8693-aligned token exchange with explicit caller authorization and support matrix.
- Add audit logging and bounded revocation hooks (e.g., jti denylist interface).

Known backward-compatibility touchpoints
- Preserve /.well-known/jwks.json path; change content to spec-compliant and introduce stable kid. Older bespoke validators may need adjustment; document.
- Keep GroupScopes and current TokenService routes; add new metadata endpoints rather than renaming.
- Default context claim name to project_accounting_context but allow override; no removal of existing ClusterID/OpenCHAMIID.

