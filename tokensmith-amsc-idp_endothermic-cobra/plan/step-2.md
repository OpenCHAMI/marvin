# Step 2 — Implementation proposal (scope, sequencing, non-goals)

Date: 2026-03-19
Status: Completed (proposal ready for execution)

Goals
- Standards-align issuer metadata and JWKS; introduce stable key identifiers (kid) and set kid on minted tokens.
- Keep the project/accounting context claim configurable, defaulting to `project_accounting_context`. Preserve backward compatibility where practical.
- Harden and clarify minting boundaries (policy vs. request shaping) and service identity issuance.
- Implement an RFC 8693-aligned token exchange surface with explicit client authorization and error mapping, within a bounded support matrix.
- Add minimal, testable audit events and a scoped revocation hook (jti denylist interface).
- Maintain backward compatibility: existing routes, GroupScopes behavior, and JWT middleware interfaces.

Non-goals
- Full-featured distributed revocation or dynamic key rotation management.
- Replacing Casbin-first AuthZ; GroupScopes continues to map upstream groups to embedded scopes for exchanged tokens only.
- Complex actor/chain token semantics beyond the documented support matrix.
- mTLS provisioning or full sender-constrained enforcement; only scaffold interfaces in this phase.

Standards targets and metadata scope
- JWKS: RFC 7517-compliant JWKs with base64url n/e, stable kid derived from key material (SHA-256 thumbprint per RFC 7638).
- Issuer metadata: OAuth 2.0 Authorization Server Metadata (RFC 8414) minimal set; optional OIDC Discovery alias if issuer is used by OIDC clients.
- Token exchange: RFC 8693 request/response/error fields; map unsupported features to invalid_request/unsupported_token_type/etc.
- JWT: FIPS-approved algs already enforced; document cache-control for JWKS.

Phased execution plan
1) Foundations (Step 4)
   - Add configuration struct for token/profile and naming: issuer, audiences, signing alg, key-id strategy, and context claim name (default `project_accounting_context`), with env/CLI override and startup validation.
   - Introduce typed profile for service vs. user token shaping (no policy logic here). Unit tests for config validation.

2) Metadata and keys (Step 5)
   - Implement stable kid: compute RFC 7638 thumbprint for RSA and ECDSA; set token header kid when signing.
   - JWKS endpoint returns spec-compliant keys; Cache-Control: max-age=300, must-revalidate. Document rotation assumptions.
   - Add /.well-known/oauth-authorization-server and /.well-known/openid-configuration (reduced) exposing issuer, jwks_uri, token_endpoint (existing /oauth/token), and supported token exchange_types.
   - Tests verifying JWK shape, kid stability across process restarts with the same key, and metadata coherence.

3) Minting boundary and project-scoped shaping (Step 6)
   - Define accepted mint request DTO and server-side policy input/result boundary. Enforce that untrusted inputs cannot set iss/sub/aud/jti/exp beyond caps.
   - Add configurable project/accounting context claim name to minted tokens when provided by policy; default key `project_accounting_context`. Provide opt-in legacy alias mapping if previously named differently (no private doc name exposure).
   - Tests for allowed/rejected mint paths and context claim naming.

4) Service identity issuance and sender-constraint scaffolding (Step 7)
   - Enforce client authentication for /service/token (replace placeholder API-key with pluggable interface; keep API-key adapter for backward compatibility). Separate policy path for service tokens (audience rules, scopes).
   - Add optional cnf thumbprint field plumbing for future DPoP/MTLS sender constraints; no enforcement yet. Tests for clear separation and auth requirements.

5) RFC 8693 token exchange (Step 8)
   - Implement POST /oauth/token (grant_type=urn:ietf:params:oauth:grant-type:token-exchange) with subject_token handling for upstream OIDC ID tokens; explicit authorization of callers; map errors to RFC semantics. Maintain legacy handler compatibility: continue accepting current body shape with deprecation notice, but prefer standards body when present.
   - Support matrix: subject_token_type=urn:ietf:params:oauth:token-type:jwt; actor_token optional: not supported (return unsupported_token_type); audience param allowed (single); scope optional. Tests for success, unsupported types, malformed requests, and unauthorized callers.

6) Audit and revocation hooks (Step 9)
   - Emit structured audit events on mint and exchange (issuer, sub, aud, jti, kid). Add Revoker interface with Denylist(jti) or ShouldReject(jti) hook; wire into Parse/Validate.
   - Tests for audit emit and denylist behavior; document bounded scope.

7) Migration, ops, docs (Step 10)
   - Validate compatibility with existing clients: unchanged routes, GroupScopes preserved, stricter JWKS now correct; document breaking changes (kid now set; JWKS e/n encoding changed to spec; OIDC simple provider local validation fixed).
   - Add docs: metadata endpoints, JWKS rotation, config reference including `project_accounting_context`, examples.

Compatibility/migration matrix
- JWKS
  - Before: ephemeral kid; n/e not base64url; no kid in tokens. After: stable kid in tokens; spec-compliant JWKS. Existing custom validators may need adjustment; standards-compliant verifiers improve.
- Token exchange
  - Before: ad hoc body with {scope,target_service}. After: standard RFC 8693 body preferred; legacy body accepted with warning.
- Context claim name
  - New: configurable name defaulting to `project_accounting_context` in minted tokens; no change to existing ClusterID/OpenCHAMIID. If legacy code expects a previous different key, allow optional alias emission via config.

Acceptance criteria
- JWKS and metadata endpoints are spec-coherent and match actual signing behavior; tests enforce kid stability and encoding.
- Minting and exchange behaviors enforce boundaries; untrusted input cannot override policy-derived values.
- Service identities require client auth; sender-constraint fields plumbed behind interfaces.
- Security tests from threat model are represented and pass.
- Documentation and examples updated.

Out-of-scope clarifications
- No dynamic in-process key rotation orchestrator; operators rotate by replacing key files and restarting, with documented cache semantics.
- No full-fledged DPoP/MTLS sender-binding enforcement; only extension points.
