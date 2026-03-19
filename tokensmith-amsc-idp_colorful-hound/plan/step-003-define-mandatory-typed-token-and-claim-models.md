# Step 3: Define mandatory typed token and claim models

Status: complete

What was added
- pkg/token/models.go
  - TokenProfile enum with end_user and service.
  - DefaultProjectAccountingContextField set to `project_accounting_context`.
  - EndUserClaims and ServiceClaims wrappers around TSClaims.
    - Optional ProjectContext value and configurable ProjectContextField.
    - AdditionalMapClaims() helper to serialize context under configured field, rejecting collisions with reserved TSClaims keys.
    - Validate(enforce) pass-through to TSClaims.Validate for now.
- pkg/token/models_test.go
  - Tests for default/custom field serialization, empty behavior, reserved-collision rejection, and validate pass-through.
- docs/migration.md (under repos/tokensmith/docs)
  - Updated with typed-model and project/accounting context notes.

What remains intentionally deferred
- No issuance or middleware wiring yet; those land in later steps.
- No default emission of project/accounting context; it is opt-in and configurable by field name, defaulting to `project_accounting_context`.

Compatibility
- TSClaims remains unchanged; existing JSON field names and validation logic are preserved.
- The new typed models are additive and do not alter current token generation.

How to use (preview)
- Create an EndUserClaims or ServiceClaims value embedding TSClaims. Call AdditionalMapClaims() to get a map for GenerateTokenWithClaims when you want to include a project/accounting context field.
