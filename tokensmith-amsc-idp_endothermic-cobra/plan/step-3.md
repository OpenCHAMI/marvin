# Step 3 — Security and threat-model checkpoint

Date: 2026-03-19
Status: Completed (guards translated to requirements/tests)

Summary of threats and trust boundaries
- Trust boundaries
  - External OIDC tokens (untrusted) vs. internal token minting boundary.
  - Service-to-service issuance callers vs. issuer.
  - Key material boundary (private keys on disk) vs. JWKS/public exposure.
  - Policy decision outputs (trusted) vs. request parameters (untrusted).
- Key risks
  - Token confusion: lack of stable kid and non-compliant JWKS can cause verifiers to mis-validate or fail-open.
  - Impersonation/delegation ambiguity: service tokens vs. user tokens not clearly separated; caller identity to /oauth/token not authorized beyond upstream token validity.
  - Claim injection: request-provided scope/audience can override derived values without policy oversight.
  - Replay/long-lived session: missing caps on iat/exp and session duration; Validate enforces 24h session cap but Exchange requires NIST claims that may not exist.
  - Key exposure: PEM on disk without rotation guidance; JWKS leaks modulus and timestamp-based kid aids fingerprinting; fix to spec and cache controls.
  - OIDC local validation bug: jwt.Parse with non-crypto key due to raw JWK map; can lead to bypass only if error handling is weak.

Required security controls and constraints
- RFC 7638 kid on all issued tokens; JWKS uses base64url n/e; set Cache-Control with reasonable max-age; document rotation procedure; tests for stability.
- Explicit caller authorization for token exchange: either audience or policy gate; deny by default for unsupported callers.
- Separate service token issuance path with strict client auth (pluggable provider; API-key adapter retained but discouraged), distinct from user token exchange; forbid ambiguous mixing of subject types.
- Harden minting input boundary: untrusted request fields cannot set issuer, subject, audiences beyond allowed; scope projection controlled by trusted policy or configured mapping (GroupScopes remain but request overrides must be validated/limited).
- Error hygiene: return RFC-compliant errors; do not reflect token strings or sensitive data.
- Key management guidance: startup validation for key availability and algorithm compatibility; log selected kid and alg; no dynamic rotation in this phase.
- Replay/abuse controls: ensure jti is present; define optional denylist hook (revocation) checked on parse/validate; bound token lifetimes for service tokens (≤5m default in TokenManager.GenerateServiceToken already set to 5m).

Security test scenarios
- JWKS correctness: consumers can verify tokens using advertised kid; wrong kid results in failure.
- Token exchange authorization: unauthorized caller receives invalid_client/unauthorized_client; malformed subject_token yields invalid_request; unsupported actor_token yields unsupported_token_type.
- Minting boundary: attempts to override issuer/subject/audience via body are ignored or rejected; scopes restricted.
- Service issuance: missing/invalid client auth rejected; audience/scope not in policy denied.
- Revocation hook: tokens with denylisted jti are rejected by middleware and service.

Ambiguities and resolutions
- Delegation vs. impersonation: actor token support deferred; user vs. service tokens are separate flows; exchange does not mint actor chains in this phase.
- NIST claim enforcement: keep strict Validate in token package; in ExchangeToken, when NonEnforcing is set, map minimum viable claims and allow minting with warnings, but do not expand beyond scoped changes in this phase.
