# ADR 0001: Deploy with SSM Run Command instead of SSH

| | |
|---|---|
| Date recorded | 28/09/2026 |
| Status | Accepted |
| Stakeholders consulted | None (personal project) |

## Context

Each deploy has to run commands on the EC2 instance: pull the new image,
refresh the data, replace the API container and check its health. The
commands start from a GitHub-hosted runner, whose IP address changes from run
to run.

## Options considered

1. **SSH from the runner.** Needs port 22 open to GitHub's published runner
   IP ranges or to the whole internet, and a private key stored as a GitHub
   secret.
2. **AWS CodeDeploy.** A managed service with its own agent and application
   specification file, which is more machinery than one container on one
   instance needs.
3. **SSM Run Command.** The SSM agent on the instance polls AWS for commands,
   so no inbound port is needed, and access is controlled by IAM.

## Decision

Option 3. The deploy job in
[`.github/workflows/deploy.yml`](../../.github/workflows/deploy.yml) builds a
deploy script, base64-encodes it, and sends it with `aws ssm send-command`
using the `AWS-RunShellScript` document. It then polls
`get-command-invocation` until the command finishes, and prints the remote
stdout and stderr into the Actions log. The instance profile includes
`AmazonSSMManagedInstanceCore` ([`terraform/main.tf`](../../terraform/main.tf)).

## Consequences

- Deploys need no inbound port and no SSH key. SSH stays limited to one admin
  CIDR, and no key pair is attached unless `ssh_key_name` is set.
- The deploy role can send commands only to this instance and only with the
  `AWS-RunShellScript` document.
- The instance's output appears in the GitHub Actions log, so a failed deploy
  can be diagnosed without logging in.
- The runner has to poll for the result (up to 100 checks, 10 seconds apart)
  instead of streaming output live.
- Deploys depend on the SSM agent being registered. The first deploy can race
  the instance's first-boot setup, so the script waits for `cloud-init` to
  finish.

## Supporting links

- [How a deploy works](../../README.md#how-a-deploy-works-githubworkflowsdeployyml)
  in the README
- [Figure 5](../../README.md#figure-5--deploy-output-from-the-instance) in the README
