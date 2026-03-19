# Step 2: Baseline tests and regression harness

Status: complete

What was added or tightened
- New baseline tests targeting the public HTTP handlers and file-config behavior in pkg/tokenservice.
  - pkg/tokenservice/http_handlers_test.go
    - TestHealthHandler_CurrentBehavior: pins current /health response body fields and types.
    - TestJWKSHandler_CurrentNonStandardBehavior_Baseline: captures today’s JWKS output (non-standard alg/kid/n/e) so we can safely migrate later with explicit plan.
    - TestTokenExchangeHandler_ErrorPaths_Baseline: captures current error-model for missing/invalid Authorization header, GET with no body (500), inactive token (401), and provider error (401).
    - TestTokenExchangeHandler_Success_WithOverrides: validates success path with upstream claims and that request-provided scope and target_service override derived values.
    - TestServiceTokenHandler_CurrentBehavior: captures method gate (405), missing API key (401), invalid JSON (400), and happy path returning a token with audience.
    - TestValidateToken_EndpointStyle_CurrentBehavior: creates a valid token through TokenManager and confirms service.ValidateToken returns claims (smoke test for wrapper semantics).
  - pkg/tokenservice/config_test.go
    - TestDefaultFileConfig_Baseline: pins default groupScopes.
    - TestLoadAndSaveFileConfig_Baseline: round-trips file config and compares key role-scope mappings.
    - TestLoadFileConfig_EmptyPathReturnsDefault_Baseline: confirms behavior for empty path.
    - TestSaveFileConfig_CreatesDirectory_Baseline: ensures nested directories are created when saving.

What existing tests already cover
- Token issuance and parsing: pkg/token/token_manager_test.go (FIPS alg selection, signing/parse behaviors, validations, additional claims, failure paths).
- Key manager: pkg/keys/key_test.go (RSA/ECDSA generation, save/load RSA, invalid sizes/types, accessors).
- AuthN/AuthZ middleware and engine: pkg/authn/*_test.go, pkg/authz/**/*_test.go including e2e middleware flows and policy loader/version hashing.
- Examples: examples/minisvc/minisvc_test.go ensures example builds during `go test` without starting a server.

Regression focus areas now under test
- Endpoint contracts: /health fields and types.
- JWKS current non-standard output (kty/use/alg/kid/n/e) to avoid accidental behavior changes before we deliberately fix JWKS in a later step.
- Token exchange handler error and success paths, including context overrides for scope and target_service.
- Service-token handler basic gates and success behavior.
- File configuration defaults and load/save semantics.

Gaps (known, intentional for later steps)
- No router-level tests exercising chi routes end-to-end; handler-level coverage is sufficient for baseline.
- No tests for issuer metadata endpoints (do not exist yet). Will be added in step 5.
- No tests for improved JWKS standards compliance. Current test intentionally locks the non-standard behavior until we introduce a migration path and updated tests in step 5.
- No tests for cmd/tokenservice serve flag parsing and end-to-end startup; that would require process-level harness or integration tests.
- No tests for OIDC SimpleProvider local validation via JWKS; feature is incomplete. Remote introspection path is mocked only.
- No tests for legacy middleware module behavior; existing tests in middleware/ remain as baseline.
- No tests related to the optional project/accounting context claim yet; introduction and tests will occur in a later step with feature-flagged behavior and default field name `project_accounting_context`.

How to run (guidance for maintainers)
- Use `go test ./...` in a controlled, isolated environment. The repository contains only standard Go tests; however, follow local security policies before executing untrusted code.

Outcome
- A focused regression harness now covers critical, externally visible behaviors in tokenservice and file config. This suite will catch unintended changes before we deliberately alter contracts (JWKS format, error models, metadata) in subsequent steps.
