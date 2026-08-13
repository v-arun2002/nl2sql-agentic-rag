# Running this on Kubernetes

These manifests are written to be **cloud-portable** -- they'd work as-is
(or with trivial changes) on EKS, GKE, or AKS. To validate them without
paying for a managed cluster's control-plane fee, test locally with `kind`
(Kubernetes-in-Docker), which is free and runs entirely on your machine.

## One-time setup

```bash
# Install kind (see https://kind.sigs.k8s.io/docs/user/quick-start/ for your OS)
# Then create a cluster with ingress port mappings:
cat <<EOF | kind create cluster --config -
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
  - role: control-plane
    extraPortMappings:
      - containerPort: 80
        hostPort: 8080
        protocol: TCP
EOF

# Install ingress-nginx (kind-specific manifest)
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/main/deploy/static/provider/kind/deploy.yaml
kubectl wait --namespace ingress-nginx --for=condition=ready pod \
  --selector=app.kubernetes.io/component=controller --timeout=90s

# Install metrics-server, or the HPA reports `cpu: <unknown>` and never scales.
# Managed clusters (EKS/GKE/AKS) ship this already; kind does not. The extra
# flag is needed because kind's kubelets serve self-signed certs.
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
kubectl patch deployment metrics-server -n kube-system --type=json \
  -p='[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]'
```

## Build and load the image

```bash
docker build -t nl2sql-agentic-rag-api:latest .
kind load docker-image nl2sql-agentic-rag-api:latest
```

## Deploy

```bash
kubectl apply -f k8s/00-namespace-configmap.yaml

# Create the secret imperatively (never commit real keys to a manifest file):
kubectl create secret generic nl2sql-secrets -n nl2sql-agentic-rag \
  --from-literal=GEMINI_API_KEY=your_key \
  --from-literal=GROQ_API_KEY=your_key \
  --from-literal=OPENAI_API_KEY=your_key

kubectl apply -f k8s/02-redis.yaml
kubectl apply -f k8s/03-api.yaml
kubectl apply -f k8s/04-ui-ingress.yaml
```

## Verify

```bash
kubectl get pods -n nl2sql-agentic-rag
kubectl get hpa -n nl2sql-agentic-rag        # confirm HPA is reading metrics
curl http://localhost:8080/api/health         # through the ingress
```

Visit `http://localhost:8080/` for the Streamlit UI.

`http://localhost:8080` only works if the cluster was created with the
`extraPortMappings` above. On a cluster created without them, reach the
ingress with a port-forward instead:

```bash
kubectl port-forward -n ingress-nginx svc/ingress-nginx-controller 8080:80
```

## What this demonstrates vs. a real managed cluster

Everything here — the Deployment/Service/ConfigMap/Secret split, resource
requests/limits, readiness/liveness probes, and the HorizontalPodAutoscaler
— is exactly what you'd run on EKS/GKE. The only difference on a real
cluster: point `image:` at an ECR/GCR/Docker Hub tag instead of loading it
locally, and the ingress controller install is provider-specific instead of
the kind-specific manifest above. Worth being explicit about this tradeoff
if asked in an interview — it's a deliberate cost decision for a portfolio
project, not a limitation of the manifests themselves.
