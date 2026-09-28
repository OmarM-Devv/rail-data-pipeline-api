# ADR 0003: Run on one `t3.micro` with Docker, with a 700 MiB limit per container

| | |
|---|---|
| Date recorded | 28/09/2026 |
| Status | Accepted |
| Stakeholders consulted | None (personal project) |

## Context

The service is small: one API container holding a 2,589-row table in memory,
plus a pipeline container that runs once per deploy. It is a demonstration
service, so running cost matters more than availability. The stack was meant
to stay within the AWS free plan.

## Options considered

1. **ECS on Fargate.** No server to manage, but a stable public address would
   normally need a load balancer, which costs more than the instance itself.
2. **Several instances behind a load balancer.** Removes the single point of
   failure, at several times the cost.
3. **One `t3.micro` (2 vCPUs, 1 GiB) running Docker.**

## Decision

Option 3, with guard rails in [`terraform/`](../../terraform/) and the deploy
script:

- A Terraform validation rule only accepts `t3.micro`, so the size cannot
  grow by accident.
- Both containers run with `--memory 700m` (700 MiB). A container that exceeds
  its limit is killed on its own, instead of exhausting the host's memory and
  taking the OS, Docker or the SSM agent down with it.
- An Elastic IP keeps the address stable across stop and start.

## Consequences

- The running cost is close to zero, and there is one server to understand.
- There is no high availability. If the instance fails, the API is down
  until it is replaced.
- Each deploy has a short outage. The old container is removed before the new
  one starts and passes its health check, which the script allows up to
  60 seconds. Avoiding that would need two containers behind a proxy or load
  balancer (blue-green).
- The limits are per container. During a deploy the pipeline container runs
  while the old API container is still serving, and together their limits
  exceed 1 GiB. The limits stop one runaway process; they do not guarantee
  that both fit at once. Figure 12 records actual usage against the limit.
- Terraform ignores newer Ubuntu images, so the server is never replaced
  silently. Moving to a newer image is a deliberate step.
- There is no alerting. CloudWatch detailed monitoring is off, and the only
  health signal is `/health`, used by the deploy and the Docker health check.

## Supporting links

- `instance_type` in [`terraform/variables.tf`](../../terraform/variables.tf)
- `start_container` in
  [`.github/workflows/deploy.yml`](../../.github/workflows/deploy.yml)
- [Figure 9](../../README.md#figure-9--ec2-instance) and
  [Figure 12](../../README.md#figure-12--container-limits-and-hardening) in the README
