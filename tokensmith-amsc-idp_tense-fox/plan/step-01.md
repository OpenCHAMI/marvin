# Step 01 — Architecture inventory and RFC 8693 gap analysis

Status: completed
Last updated (UTC): 2026-03-19T14:50:00Z

Summary
- Repository: tokensmith (Go)
- Focus areas reviewed: authentication flows, token issuance/parsing, claims, policy/authorization, JWKS/metadata, HTTP routes, tests

Key modules and files
- pkg/token
  - claims.go: TSClaims (typed JWT claims) + Validate(enforce bool)
  - token_manager.go: TokenManager (generate/parse tokens, ExtendedClaims for additional/dynamic claims, JTI/nonce generation)
  - errors.go: token error set
- pkg/keys
  - key.go: RSA/ECDSA keypair generation, PEM load/save, getters
  - fips.go: allowed signing algs (PS*, RS*, ES*) and mapping to jwt.SigningMethod
- pkg/oidc
  - provider.go: Provider interface (IntrospectToken, metadata, JWKS)
  - simple.go: minimal OIDC discovery + JWKS fetch + basic local “validation” and remote introspection
  - middleware.go: RequireToken / RequireValidToken hooks
  - mock.go: test double
- pkg/authn, pkg/authz
  - Middleware and Casbin-first authorization primitives (not directly used by token exchange path yet)
- pkg/tokenservice
  - service.go: HTTP server (chi), routes, ExchangeToken flow, Service token minting, JWKS handler, /oauth/token handler (JSON body), health
  - config.go: File-config for groupScopes only
  - client.go: (present; not used in server path)
- cmd/tokenservice/main.go: CLI skeleton, generate-config; serve command not wired yet
- context.go: cross-module context helpers for Principal and legacy claims

HTTP routes present
- GET /.well-known/jwks.json — ad hoc JWKS emission (RSA only; non-spec JWK fields for n/e, ephemeral kid)
- /oauth/token — TokenExchangeHandler (POST/GET) guarded by oidc.RequireToken and oidc.RequireValidToken
  - Expects Authorization: Bearer <subject token>
  - Expects JSON body: {"scope": [..], "target_service": "..."}
  - Emits: {"access_token":"...","token_type":"Bearer"}
- POST /service/token — service-to-service token mint w/ stubbed API key auth
- GET /health — health JSON

Current exchange/issuance behavior
- ExchangeToken(ctx, idtoken) calls OIDC provider IntrospectToken, requires Active, builds TSClaims with:
  - iss, sub, exp/iat, maybe aud/name/email/email_verified
  - Requires NIST-ish claims in the introspection: auth_level, auth_factors, auth_methods[], session_id, session_exp, auth_events[]
  - Derives Scope from upstream groups via GroupScopes mapping (config-backed)
  - Optional overrides via context values for scope and target_service
  - Signs via TokenManager (PS256 default unless overridden)
- Service token minting: GenerateServiceToken(serviceID, targetService, scopes) with short lifetime and fixed NIST-ish claims

Tests and coverage indicators
- pkg/token: unit tests for alg selection, generate/parse behavior, required claims
- pkg/tokenservice: tests for ExchangeToken happy/invalid paths, service token minting, validate token
- pkg/oidc: mock provider used extensively in tests

Observed constraints / issues
- JWKS handler is not spec-compliant: kid is time-based and non-stable; n/e are not base64url strings; alg fixed to RS256; no EC support; token header kid is never set.
- /oauth/token handler is JSON body driven and not RFC 8693-compliant (should accept application/x-www-form-urlencoded and defined parameters: grant_type, subject_token, subject_token_type, etc.).
- No issuer metadata (/.well-known/openid-configuration) for TokenSmith itself.
- No explicit policy contract for issuance/exchange authorization; service has stubbed API key auth for service tokens; exchange path implicitly trusts any caller with a present/valid upstream token via middleware.
- Claims typing exists (TSClaims), but there is no explicit project/accounting context claim. No configurability for the claim key name.
- ParseToken uses TSClaims but does not propagate unknown/dynamic claims into TSClaims (only returned via raw map).
- CLI: serve command flags for issuer/keys/oidc are not implemented; only generate-config is wired.

Gap analysis vs RFC 8693 (subject-token exchange)
- Missing: form-encoded request handling with grant_type=urn:ietf:params:oauth:grant-type:token-exchange
- Missing: subject_token and subject_token_type validation; requested_token_type handling (MVP may default to access_token/JWT)
- Missing: RFC-aligned error codes (invalid_request, invalid_client, invalid_grant, unauthorized_client, unsupported_grant_type)
- Missing: well-defined client authentication and authorization for exchange (who may exchange, for what audiences/scopes)
- Partially present: subject token verification via upstream provider introspection; needs hardening and mapping
- Present: token issuance/signing foundation

Integration points for minimal-disruption implementation
- Add RFC 8693 endpoint behavior into existing /oauth/token route (preserve JSON path under compatibility switch, prefer form-encoded per RFC)
- Reuse TokenManager for output token minting
- Introduce Policy interface under pkg/tokenservice for exchange authorization
- Extend TSClaims to include a typed project/accounting context; implement configurable emission/acceptance mapping without breaking existing tokens
- Fix JWKS to be spec-compliant and set stable kid; include kid in JWT header

Open questions
- What exact policy inputs are required for exchange authorization (e.g., source audience, requested audience, scopes, upstream AMR/ACR)?
- Should public clients ever be allowed for exchange? Default posture will be “no”.
- Backward compatibility strategy for current JSON /oauth/token behavior: keep behind a compatibility flag and default to RFC 8693.
