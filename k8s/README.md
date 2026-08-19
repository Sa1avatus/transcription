# Kubernetes deployment guide

The manifests in this directory describe the production topology:

```text
client -> API (replicas) -> Redis queue -> GPU worker
              |                            |
              +-------- PostgreSQL --------+
Telegram bot --+                            |
              +---- shared audio volume ----+
```

## What is shared and why

`transcription-data` is a `ReadWriteMany` (RWX) volume. API and bot write
uploaded audio there; the worker reads and deletes the same temporary file.
`transcription-models` is another RWX volume mounted read-only into workers.
PostgreSQL and Redis are each single-replica stateful services and use their
own `ReadWriteOnce` volumes.

This is intentional: a local file path placed in a Redis job is meaningful
only when the producer and worker share storage. For a larger production
deployment, replace this hand-off with S3-compatible object storage and put
an object key in the job instead of a local path.

## Production cluster prerequisites

1. A StorageClass supporting RWX, e.g. NFS, Longhorn RWX or CephFS.
2. NVIDIA drivers, NVIDIA Container Toolkit and the NVIDIA Kubernetes device
   plugin on GPU nodes. Verify with:

   ```bash
   kubectl get nodes -o json | jq '.items[].status.allocatable["nvidia.com/gpu"]'
   ```

3. Published images. Replace the three `ghcr.io/your-org/...:TAG` references
   in `application.yaml`; a Kubernetes node cannot reliably use a developer's
   unpushed Docker image.
4. Local Whisper/NLLB model folders copied to the models PVC.
5. Set `WEBAPP_URL` in `configmap.yaml` to the public HTTPS API address plus
   `/miniapp`; the bot will expose it as the **Statistics** menu button.

## Apply safely

```powershell
kubectl apply -f k8s/namespace.yaml
Copy-Item k8s/secret.example.yaml k8s/secret.yaml
# Fill real values, including DATABASE_URL with the same PostgreSQL password.
kubectl -n transcription apply -f k8s/secret.yaml
kubectl apply -k k8s/
kubectl -n transcription get pods -w
```

The secret template is deliberately excluded from `kustomization.yaml`; do
not apply it with placeholder values and do not commit `secret.yaml`.

## Docker Desktop Kubernetes: what it is good for

Docker Desktop created a healthy two-node kind cluster in this workspace:

```text
desktop-control-plane  Ready
desktop-worker         Ready
```

It is appropriate for learning `kubectl`, validating manifests and testing
stateless services. Its default `local-path` StorageClass is RWO-only, and the
cluster currently exposes no `nvidia.com/gpu` resource. This was verified in
Docker Desktop kubeadm mode with NVIDIA's official device-plugin DaemonSet:
the plugin starts but reports `No devices found`. Therefore it cannot run this
project's GPU worker plus shared audio/model volumes as the production
manifests require.

Do not remove the worker's GPU resource request just to force scheduling: that
would make NLLB/Whisper run without the intended hardware and hide a real
deployment requirement. Use a GPU-enabled Kubernetes cluster with RWX storage
for the full system.
