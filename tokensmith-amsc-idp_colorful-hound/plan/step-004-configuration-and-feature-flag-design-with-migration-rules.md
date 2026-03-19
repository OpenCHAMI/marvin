# Step 4: Configuration and feature-flag design with migration rules

## Status
complete

## What changed
- Introduced pkg/tokenservice/runtime_config.go defining:
  - FeatureFlags (EnableIssuerMetadata, EnableJWKS, AllowGETTokenEndpoint, OIDCPreferLocalValidation)
  - JWKSOptions (Path, CacheControl, KIDStrategy [stable|volatile])
  - ServiceTokenOptions (TTL)
  - ExchangeOptions (AllowScopeOverride, AllowTargetServiceOverride)
  - ConfigExtensions with ApplyDefaults() and Validate() methods
- Integrated ConfigExtensions into FileConfig (pkg/tokenservice/config.go) under an `ext` field, optional and backward compatible.
- Applied defaults and validation during LoadFileConfig and in serve path.
- Added unit tests for defaults/validation (pkg/tokenservice/runtime_config_test.go).

## Compatibility and migration
- Existing FileConfig.groupScopes preserved; `ext` is optional and defaults are backward-compatible.
- Default signing algorithm remains PS256 (no behavior change).
- JWKS remains enabled by default to preserve current endpoint; KID strategy defaults to volatile to match current unstable kid; this will be migrated in Step 5.
- GET /oauth/token remains allowed by default; body handling cleanup will be controlled in later steps.
- OIDC local validation remains disabled by default; will be addressed when provider validation is corrected.
- Project/accounting context default claim field set to `project_accounting_context` but only used by typed models in later steps.

## Next
- Step 5 will wire metadata and a standards-compliant JWKS, honoring these config knobs.
