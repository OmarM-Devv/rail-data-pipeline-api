# ADR 0002: Authenticate CI/CD with GitHub OIDC and the immutable subject claim

| | |
|---|---|
| Date recorded | 28/09/2026 |
| Status | Accepted |
| Stakeholders consulted | None (personal project) |

## Context

The deploy job needs AWS permissions to push to ECR and send SSM commands. The
usual shortcut is an IAM user's access key stored as a GitHub secret. That key
never expires unless someone rotates it, and anyone who obtains it can use it
from anywhere.

GitHub Actions can instead request a signed OIDC token that AWS exchanges for
temporary credentials. The IAM role's trust policy decides which tokens it
accepts by matching claims, chiefly `sub` (subject). The default subject used
to be built from names only (`repo:owner/repo:ref:...`). If a repository or
owner name was later reused, the new holder's tokens could match. GitHub now
builds the default subject from immutable numeric IDs for repositories created
after 15/07/2026, and this repository uses that format.

## Options considered

1. **IAM user access keys stored as GitHub secrets.** Long-lived, need manual
   rotation, and work from anywhere if leaked.
2. **OIDC with a trust policy on the name-based subject.** Short-lived
   credentials, but tied to names that can change owner.
3. **OIDC with a trust policy on the immutable subject.**

## Decision

Option 3. [`terraform/main.tf`](../../terraform/main.tf) creates the GitHub
OIDC provider (or reuses an existing one) and a deploy role whose trust policy
requires both:

- `aud` = `sts.amazonaws.com`
- `sub` = `repo:OmarM-Devv@248359882/rail-data-pipeline-api@1393259138:ref:refs/heads/main`

The numeric IDs are Terraform variables, so a fork can supply its own, or set
both to `null` for the legacy name-based format.

## Consequences

- No AWS secrets are stored in the repository or in GitHub.
- Credentials last at most one hour (the role's maximum session duration).
- Only workflows on `main` of this exact repository can assume the role.
  Pull requests and other branches cannot, even though they run the tests.
- A reused repository or owner name cannot match the policy, because the IDs
  differ.
- The match is exact, so a new deploy branch or a GitHub environment needs a
  trust policy change. A mismatch fails with a generic `Not authorized to
  perform sts:AssumeRoleWithWebIdentity` error. The README documents how to
  look up the expected subject.
- The role's permissions are limited to this project's ECR repository and SSM
  commands on this instance.

## Supporting links

- [OIDC subject claim](../../README.md#oidc-subject-claim) in the README
- GitHub changelog:
  [Immutable subject claims for GitHub Actions OIDC tokens](https://github.blog/changelog/2026-04-23-immutable-subject-claims-for-github-actions-oidc-tokens/)
- [Figure 11](../../README.md#figure-11--oidc-trust-policy) in the README
