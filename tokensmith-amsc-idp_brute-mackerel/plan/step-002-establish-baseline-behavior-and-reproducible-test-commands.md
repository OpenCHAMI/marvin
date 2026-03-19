# Step 2: Establish baseline behavior and reproducible test commands

## Status
completed

## Environment
- Host: darwin/arm64
- Go: go1.25.2
- Repo: github.com/openchami/tokensmith (replace => ./)

## Commands reviewed/run

Safer, non-executing or compile-only checks (no tests executed):
- go mod download            # dependencies resolved (no output, completed)
- go vet ./...               # static vet pass (no issues reported)
- go list ./...              # enumerated packages
- go build ./cmd/tokenservice (narrow build): succeeded
- go test -c ./pkg/token     # compile tests for pkg/token only (succeeded)

Not executed due to safety policy (tests may run arbitrary code):
- go test ./...              # would execute all tests
- make test / make build     # make can run arbitrary commands during parse/build

## Observations
- Packages discovered:
  github.com/openchami/tokensmith
  github.com/openchami/tokensmith/cmd/tokenservice
  github.com/openchami/tokensmith/example/serviceauth
  github.com/openchami/tokensmith/examples/minisvc
  github.com/openchami/tokensmith/internal/casbinfuncs
  github.com/openchami/tokensmith/pkg/authn
  github.com/openchami/tokensmith/pkg/authz (+ subpackages)
  github.com/openchami/tokensmith/pkg/keys
  github.com/openchami/tokensmith/pkg/oidc
  github.com/openchami/tokensmith/pkg/testutil
  github.com/openchami/tokensmith/pkg/token
  github.com/openchami/tokensmith/pkg/tokenservice

- Existing tests present across authn/authz/token/tokenservice and examples.
- Static analysis (go vet) reported no issues.
- Narrow build of cmd/tokenservice succeeded; full repo build was not executed here to respect safety gating.

## Pre-existing issues (from Step 1, still applicable)
- JWKS handler non-compliant (base64url n/e, stable kid, alg alignment).
- KeyManager not wired into NewTokenService in CLI serve path.
- OIDC local validation is not cryptographically verifying signatures.
- Unsigned overrides of scope/audience during exchange.
- Service token client/server mismatches.

## Reproducible baseline command set
Use in a sandboxed environment (container/VM) to capture full baseline including tests:
- go version
- go mod download
- go vet ./...
- go build ./...
- go test ./... -race -coverprofile=coverage.out -covermode=atomic

Local minimal (safer) subset used here:
- go mod download
- go vet ./...
- go build ./cmd/tokenservice
- go test -c ./pkg/token

## Next
- Proceed to Step 3 once a sandbox/container is available for executing the full test suite safely, or continue design/implementation with static/compile feedback and add tests as features land.
