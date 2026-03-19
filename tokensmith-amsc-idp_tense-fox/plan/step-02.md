# Step 02 — Implementation proposal and scope phases

Status: completed
Last updated (UTC): 2026-03-19T14:50:15Z

Scope overview
- MVP delivers an RFC 8693-compliant subject-token exchange under /oauth/token with form-encoded parameters and RFC error semantics.
- Claims foundation stays typed via TSClaims; we add a configurable project/accounting context claim with default key name project_accounting_context.
- Introduce minimal policy contract for issuance authorization. Service identity minting continues to work; policy governs both flows consistently.
- JWKS is made spec-compliant with stable kid and correct JWK fields. JWTs will carry kid.
- Backward compatibility: existing JSON body exchange remains available via a compatibility mode; default is RFC 8693 behavior.

Out of scope (deferred)
- Sender-constrained and DPoP-bound tokens
- Delegated or impersonation chains beyond single subject_token exchange
- Full-featured revocation store and distributed cache
- Full OIDC server metadata for TokenSmith (we only ship JWKS and minimal issuer metadata if required by consumers)

API changes
- /oauth/token (MVP):
  - Content-Type: application/x-www-form-urlencoded
  - Inputs: grant_type, subject_token, subject_token_type, audience (optional), scope (optional), requested_token_type (optional)
  - Outputs: RFC 8693 token response JSON { access_token, issued_token_type, token_type, expires_in, scope, ... }
  - Errors: per RFC 8693/6749 mapping (invalid_request, invalid_client, invalid_grant, unauthorized_client, unsupported_grant_type)
- /.well-known/jwks.json: corrected JWKs with stable kid, base64url n/e, correct kty/alg, support for RSA; EC as follow-up if keys are EC

Compatibility strategy
- Add config: tokenservice.exchange.compat_mode = "json" | "rfc8693" (default "rfc8693"). When json, accept current JSON payload with {scope, target_service} and map into exchange.
- Continue to honor legacy middleware context key for claims; no change required.
- Context/accounting claim name configurable via claim_context_key (default "project_accounting_context"). Backward-compat aliases can be listed in config.

Policy contract (MVP)
- Location: pkg/tokenservice/policy.go
- Interface: type Policy interface { AllowExchange(ctx, Subject, Client, Requested) (Decision, error) }
- Inputs include: upstream subject (sub), upstream issuer, upstream AMR/ACR, client id (caller), requested audience, requested scopes, token type
- Decision: Allow with possibly filtered scopes/audience, or Deny with reason code

Implementation sequence
1) Claims foundation already exists; add typed field for project accounting + validation hooks (no enforcement by default)
2) Add config and mapping for context claim naming (default project_accounting_context); migration tests and docs
3) JWKS: stable kid + proper JWK fields; set kid header in signed JWTs
4) Define and wire Policy contract into TokenService for both ExchangeToken and GenerateServiceToken
5) Implement RFC 8693 handling in /oauth/token with form-encoded requests; preserve JSON compat via config flag
6) Minimal revocation hooks: ensure jti issuance, expose interface for future checks; do not implement storage
7) Back-compat validation tests and documentation updates

Decision log
- Default context claim key: project_accounting_context
- Public clients for exchange: not supported in MVP; callers must authenticate per existing OIDC middleware or configured client credentials (future)
- requested_token_type: default to access_token (JWT) when omitted; only JWT access tokens are issued in MVP
