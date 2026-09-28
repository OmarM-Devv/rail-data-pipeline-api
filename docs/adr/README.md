# Architecture decision records

Short records of the main design decisions in this project: the context, the
options considered, what was chosen and what it costs. They follow the fields
in the UK government's
[Architectural Decision Record Framework](https://www.gov.uk/government/publications/architectural-decision-record-framework/architectural-decision-record-framework).

| ADR | Decision | Status |
|---|---|---|
| [0001](0001-deploy-with-ssm-run-command.md) | Deploy with SSM Run Command instead of SSH | Accepted |
| [0002](0002-github-oidc-with-immutable-subject.md) | Authenticate CI/CD with GitHub OIDC and the immutable subject claim | Accepted |
| [0003](0003-single-t3-micro-with-docker.md) | Run on one `t3.micro` with Docker, with a 700 MiB limit per container | Accepted |
| [0004](0004-validate-before-writing-keep-last-good-data.md) | Validate everything before writing, and keep the last good data | Accepted |
