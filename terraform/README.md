# Deploying to AWS with Terraform

Deploys the FastAPI backend (not the Streamlit UI -- see note at the
bottom) as a containerized Lambda function behind an API Gateway HTTP API.
Chosen specifically because **both have a perpetual free tier with zero
idle cost** -- unlike a managed Kubernetes cluster (EKS/GKE charge an
hourly control-plane fee even at zero traffic), Lambda and API Gateway only
bill per request. At portfolio-demo traffic levels, this should cost $0.

## Prerequisites

- AWS account with the CLI configured (`aws configure`)
- Terraform >= 1.5
- Docker

## Deploy workflow (two phases -- this is a real Lambda-container gotcha, not an oversight)

Terraform can't create a Lambda function pointing at an image that doesn't
exist yet, but the ECR repo it needs to push to doesn't exist until
Terraform creates it. So:

**Phase 1 -- create just the ECR repo:**
```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars
# edit terraform.tfvars with your real API keys

terraform init
terraform apply -target=aws_ecr_repository.api
```

**Build and push the image:**
```bash
cd ..
aws ecr get-login-password --region us-east-1 | \
  docker login --username AWS --password-stdin <account-id>.dkr.ecr.us-east-1.amazonaws.com

docker build -f Dockerfile.lambda -t nl2sql-agentic-rag-api:latest .
docker tag nl2sql-agentic-rag-api:latest <ecr_repository_url_from_phase_1>:latest
docker push <ecr_repository_url_from_phase_1>:latest
```

**Phase 2 -- create everything else:**
```bash
cd terraform
terraform apply
```

Grab the URL:
```bash
terraform output api_invoke_url
```

## Cost reality check

- **Lambda**: 1M free requests/month + 400,000 GB-seconds compute, forever
  (not just first 12 months). A portfolio demo won't come close.
- **API Gateway HTTP API**: 1M free requests/month for the first 12 months,
  then $1.00/million after -- again, far more than a demo will see.
- **ECR**: 500 MB-month free for 12 months, then ~$0.10/GB-month. One
  container image is small enough this is effectively free.
- **CloudWatch Logs**: retention capped at 14 days in `lambda.tf` to keep
  storage near zero.
- **What's NOT free**: if you also stand up a managed Redis (ElastiCache)
  or an EKS/GKE cluster, those bill hourly regardless of traffic. Neither
  is provisioned here -- Redis caching stays local/Docker-only (see
  `docker-compose.yml`), and the Kubernetes manifests in `k8s/` are
  designed for a real cluster but validated locally via `kind` instead
  (see `k8s/README.md`) to avoid that cost.

## Tearing down

```bash
terraform destroy
```
Always run this when you're done experimenting -- an unused Lambda +
API Gateway costs nothing sitting idle, but it's good practice regardless.

## On the Streamlit UI

Lambda's request/response model doesn't fit Streamlit's long-lived server
process well. The UI is deployed separately via a free PaaS tier (Streamlit
Community Cloud, Hugging Face Spaces, or Render) pointed at this Lambda's
`api_invoke_url` as its `API_URL` -- a deliberate "right tool for the
workload type" choice, not a gap in the Terraform config.
