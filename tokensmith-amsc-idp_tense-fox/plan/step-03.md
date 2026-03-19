# Step 03 — API surface and versioning compatibility review

Status: completed
Last updated (UTC): 2026-03-19T14:50:30Z

Endpoints and changes
- /oauth/token
  - Classification: additive behavior change, but same path. Risk: request body format switch. Mitigation: compat_mode switch with default=rfc8693.
  - Request shapes:
    - RFC: form-encoded with parameters: grant_type, subject_token, subject_token_type, audience (optional), scope (optional), requested_token_type (optional)
    - Compat JSON: { scope: [string], target_service: string }
  - Response shapes:
    - RFC: { access_token, issued_token_type, token_type, expires_in, scope }
    - Compat JSON: current shape preserved when compat_mode=json
  - Errors: map to OAuth2 error JSON { error, error_description }

- /.well-known/jwks.json
  - Classification: additive-but-corrective. Fields fixed to base64url for RSA (n,e), stable kid derived from key fingerprint; alg reflects actual signing method.
  - Backward-compat: existing consumers parsing ad hoc fields may break; documentation will state JWKS is corrected and semver-minor but compatible per spec; keep a short transition note.

Versioning decision
- No route renames. Changes are additive with compatibility guard. No semver-major required for MVP.

Operator-visible changes
- New config keys for compat mode and context claim mapping.
- Stable kid in JWT headers and JWKS response.

Breaking change risks and mitigations
- Clients expecting JSON body at /oauth/token: mitigated by compat_mode=json.
- Consumers reading old JWKS numeric e field: mitigated by spec-correct response and release notes; if necessary, a compatibility toggle could be added, but default is correct per JOSE.
