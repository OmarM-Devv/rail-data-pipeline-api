# Rail Data Pipeline and API

[![CI/CD](https://github.com/OmarM-Devv/rail-data-pipeline-api/actions/workflows/deploy.yml/badge.svg?branch=main)](https://github.com/OmarM-Devv/rail-data-pipeline-api/actions/workflows/deploy.yml)

A pandas pipeline that cleans the Office of Rail and Road's station usage data
([ORR Table 1410](https://dataportal.orr.gov.uk/), April 2024 to March 2025),
served by a FastAPI REST API. The API runs in Docker on AWS EC2. It was
provisioned with Terraform and is deployed by GitHub Actions on every push to
`main`.

## Live API

| | |
|---|---|
| Base URL | `http://16.61.66.47:8000` |
| Interactive docs (Swagger) | http://16.61.66.47:8000/docs |
| Health | http://16.61.66.47:8000/health |
| Example | http://16.61.66.47:8000/analytics/top-stations?n=5 |

The API is served over plain HTTP on port 8000. It is a demo deployment with
no TLS or custom domain.

## Endpoints

| Method and path | Description |
|---|---|
| `GET /health` | Service status, rows loaded and data file timestamp. Returns 503 if the data is not loaded. |
| `GET /stations` | Paginated station list. Query: `limit` (1-500), `offset`, `region`, `name` (contains), `sort` (`name` or `usage`). |
| `GET /stations/{code}` | One station by three-letter code (any case, e.g. `kgx`) or numeric NLC. |
| `GET /regions` | Station count and total entries/exits per region. |
| `GET /analytics/top-stations` | Busiest stations. Query: `n` (1-100), `region`, `ticket_type` (`all`, `full_price`, `reduced_price`, `season`). Stations without reported usage are excluded rather than counted as zero. |

```bash
curl "http://16.61.66.47:8000/analytics/top-stations?n=3"
```

## Architecture

```mermaid
flowchart LR
    dev[Push to main] --> gha[GitHub Actions]
    gha -->|ruff + 14 pytest cases| test{Tests pass?}
    test -->|yes| oidc[Assume IAM role<br/>via OIDC]
    oidc --> ecr[(Amazon ECR)]
    oidc -->|SSM Run Command| ec2[EC2 Ubuntu 24.04]
    ecr -->|pull image| ec2
    ec2 --> pipe[pipeline container<br/>refreshes ORR data]
    pipe --> vol[(/opt/rail/data)]
    vol -->|read-only| api[API container<br/>:8000]
    user[Client] --> api
```

## Project layout

```
api/                  FastAPI application (api/app.py)
pipeline/             Pandas cleaning and validation pipeline (pipeline/cleaner.py)
tests/                pytest suite and a sample ORR CSV fixture
terraform/            AWS infrastructure (EC2, ECR, IAM, GitHub OIDC)
.github/workflows/    CI/CD workflow (deploy.yml)
Dockerfile            Multi-stage, non-root production image
data/raw, processed   Local pipeline input/output (git-ignored)
```

## Running locally

Requires Python 3.14.

```bash
python -m venv venv
source venv/bin/activate          # Windows PowerShell: venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt

python -m pipeline.cleaner        # download ORR data and write data/processed/
uvicorn api.app:app --reload      # http://127.0.0.1:8000/docs
```

The pipeline also accepts a local file: `python -m pipeline.cleaner --source path/to/table-1410.csv`.
The API reads `data/processed/station_usage.csv` by default. Set
`RAIL_DATA_PATH` to point it at another file.

### Tests and linting

```bash
ruff check .
ruff format --check .
pytest
```

### Docker

```bash
docker build -t rail-api .
docker volume create rail-data
docker run --rm -v rail-data:/app/data rail-api python -m pipeline.cleaner
docker run -d -p 8000:8000 -v rail-data:/app/data:ro --read-only --tmpfs /tmp rail-api
```

The image uses `python:3.14-slim` and a two-stage build: dependencies are
installed in a separate layer that is cached until `requirements.txt` changes.
It runs as a non-root user (UID 10001) and has a `HEALTHCHECK` against
`/health`.

## Deployment

### What Terraform creates (`terraform/`, region `eu-west-2`)

- **EC2**: `t3.micro` Ubuntu 24.04 with an encrypted 20 GB gp3 disk,
  IMDSv2 only and an Elastic IP. On first boot, user data installs Docker
  and the AWS CLI.
- **Security group**: port 8000 open to the internet. SSH (22) is allowed
  only from `admin_cidr`.
- **IAM instance profile**: can pull from this project's ECR repository only,
  plus `AmazonSSMManagedInstanceCore` for SSM. The instance holds no stored
  credentials.
- **ECR repository**: immutable tags, scan on push, and a lifecycle policy that
  keeps the last 3 images.
- **GitHub OIDC provider and deploy role**: GitHub Actions gets short-lived
  AWS credentials, and there are no AWS secrets in the repository. The role
  can only be assumed by workflows running on `main` of this repository. It
  can push to the ECR repository and run SSM commands on this one instance.

### How a deploy works (`.github/workflows/deploy.yml`)

1. **Lint and test** runs on every push and pull request: `ruff check`,
   `ruff format --check` and pytest with coverage.
2. **Build, push and deploy** runs only on pushes to `main`, after the tests
   pass:
   - assumes the deploy role through OIDC;
   - builds the image and pushes it to ECR, tagged with the commit SHA (a
     re-run reuses the existing image);
   - sends a script to the instance with SSM Run Command. The script pulls
     the image and refreshes the ORR data with a one-off pipeline container
     (if that fails, it keeps the previous data file). It then replaces the
     API container, which runs with a read-only root filesystem, all Linux
     capabilities dropped and `no-new-privileges`, and waits for `/health`.
     If the health check fails, it **rolls back** to the previous image.

### Setting it up in your own AWS account

Prerequisites: an AWS account, the [AWS CLI v2](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html),
[Terraform](https://developer.hashicorp.com/terraform/install) 1.9 or later and the
[GitHub CLI](https://cli.github.com/).

1. **Sign the CLI in to AWS.** `aws login` uses your browser sign-in and short-lived credentials:

   ```bash
   aws login --region eu-west-2
   ```

2. **Configure Terraform.** Copy the example variables and set your own IP for SSH:

   ```bash
   cd terraform
   cp terraform.tfvars.example terraform.tfvars   # set admin_cidr = "<your-ip>/32"
   ```

   If you fork the repository, also set `github_owner`, `github_repo`,
   `github_owner_id` and `github_repo_id` (see
   [OIDC subject claim](#oidc-subject-claim) below). If the account already
   has a GitHub OIDC provider, set `create_github_oidc_provider = false`.

3. **Create the infrastructure.**

   ```bash
   terraform init
   terraform plan -out=tfplan
   terraform apply tfplan
   ```

   If `aws login` credentials are not picked up, export them first:
   `eval "$(aws configure export-credentials --format env)"`.

4. **Give GitHub Actions the outputs.** These are repository variables, not secrets:

   ```bash
   gh variable set AWS_DEPLOY_ROLE_ARN --body "$(terraform output -raw github_deploy_role_arn)"
   gh variable set EC2_INSTANCE_ID     --body "$(terraform output -raw ec2_instance_id)"
   gh variable set ECR_REPOSITORY      --body "$(terraform output -raw ecr_repository_name)"
   ```

5. **Push to `main`.** The workflow deploys, and `terraform output api_url`
   prints the address.

### OIDC subject claim

This repository uses GitHub's **immutable OIDC subject** format, which includes
numeric IDs. Tokens from a push to `main` therefore carry:

```
repo:OmarM-Devv@248359882/rail-data-pipeline-api@1393259138:ref:refs/heads/main
```

The IAM trust policy must match this exact string. A policy written for
`repo:OmarM-Devv/rail-data-pipeline-api:ref:refs/heads/main` fails with
`Not authorized to perform sts:AssumeRoleWithWebIdentity`. To find the prefix
for any repository:

```bash
gh api repos/<owner>/<repo>/actions/oidc/customization/sub
```

Set `github_owner_id` and `github_repo_id` to `null` for a repository that
still uses the legacy `repo:<owner>/<repo>` format.

### Operations

- **Logs**: in the AWS console, open Systems Manager > Session Manager and
  connect to the instance, then run `sudo docker logs rail-api`.
- **Terraform state** is stored locally in `terraform/terraform.tfstate`
  (git-ignored). Keep it safe, because it is needed to change or destroy the
  stack. Move it to an S3 backend if more than one person manages the
  infrastructure.
- **Windows checkouts**: `git` may convert files to CRLF line endings. The
  EC2 user data strips `\r` so this does not force an instance replacement.
  Check `terraform plan` for `must be replaced` before applying.

### Cost and teardown

The stack is sized for the AWS free plan: a `t3.micro` instance, less than
500 MB of ECR storage, and SSM Run Command, which is free. The public IPv4
address is billed at about $3.60 a month and is covered by free-plan
credits. To remove all resources:

```bash
cd terraform
terraform destroy
```

## Data source

Contains public sector information from the Office of Rail and Road, licensed
under the [Open Government Licence v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/).
