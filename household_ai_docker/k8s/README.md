# Household AI on openmediavault's Kubernetes (K3s)

`recipe.yaml` runs Household AI with the **openmediavault-k8s** plugin:

- the **settings page** through the plugin's Traefik, at `https://ai.<FQDN>:8443` (or `https://ai.<IP>.sslip.io:8443`);
- the **gateway** the household apps use, straight on the NAS at `http://<NAS IP>:11434`.

## 1. Build the image and load it into K3s

Household AI has no published image, so build it once with Podman on the NAS and import it into K3s's own image
store (K3s doesn't use Podman's images). The recipe uses `localhost/household-ai:local` and never pulls it.

Copy the `household_ai_docker` folder to the NAS (a shared folder is fine), then over SSH, in that folder:

```bash
sudo podman build --format docker -t localhost/household-ai:local --build-arg OLLAMA_LIBS=cpu .
sudo podman save --format docker-archive localhost/household-ai:local | sudo k3s ctr -n k8s.io images import -
sudo k3s crictl images | grep household-ai        # should list localhost/household-ai:local
```

- `--format docker` keeps the image's health check (Podman's default format drops it).
- `OLLAMA_LIBS=cpu` makes a much smaller image for a NAS without an NVIDIA graphics card.
- The build needs the internet (Ollama's and Python's images and packages) and takes a few minutes.
- Once imported, Podman's copy isn't needed: `sudo podman rmi localhost/household-ai:local` frees the space.

No Podman on the NAS? Build on another computer with Docker or Podman using the same name
(`-t localhost/household-ai:local`, plus `--platform linux/amd64` if that computer isn't the NAS's architecture),
save it to a file (`docker save -o household-ai.tar localhost/household-ai:local`), copy it to the NAS and run
`sudo k3s ctr -n k8s.io images import household-ai.tar`.

## 2. Make the shared folder

**Storage → Shared Folders → Create**, e.g. `household-ai`, on a disk with room for the models (several GB each).
The recipe runs as `nobody` in the `users` group; a new OMV shared folder already lets `users` write.

## 3. Apply the recipe

**Services → Kubernetes → Manifest**: paste `recipe.yaml`, then change the variables at the top:

- **hostname**: keep `ai.<FQDN>` if your router or DNS resolves names under the NAS's domain; otherwise put `#` in
  front of that line and remove it from the `sslip.io` line. (OMV drops every line starting with `#` before it reads
  the template, so only the uncommented `set hostname` line counts.)
- **adminPassword**: a long password of your own. The page is open to your whole network through Traefik.
- **dataSharedFolderName**: the shared folder from step 2.
- **gatewayPort**: leave at `11434` unless something on the NAS already uses it.
- **webPort**: the settings page's port inside the pod (default `8090`). It isn't opened on the NAS, so it can't
  clash with OMV's own pages; you reach the page through Traefik on 8443.

**Apply**. **Deployments** shows `household-ai` as ready after about half a minute.

## 4. Use it

1. Open `https://ai.<…>:8443`. The browser warns about the certificate (Traefik's own); accept it. Sign in with any
   user name and your password.
2. **Models**: download a model, e.g. `qwen2.5:3b` (text) and `qwen2.5vl:7b` (receipts and statements).
3. **App settings → Access**: turn on **Require an access key**, then make one key per household app on the
   **Access keys** tab.
4. In each household app in Home Assistant, **Admin → App settings → AI**: provider **Ollama**, address
   `http://<NAS IP>:11434`, that app's key.

The gateway doesn't go through Traefik on purpose: the household apps speak plain HTTP to it, as to any Ollama, and
the page can see each caller's real address.

## Updating

Build and import the new image as in step 1 (same commands), then on the NAS:
`sudo k3s kubectl -n household-ai-app rollout restart deployment household-ai`. Settings, keys, usage and models stay
in the shared folder.

## Notes

- **Memory:** models need a lot of RAM. To stop Household AI from starving the NAS's other services, uncomment the
  `resources:` lines in the recipe and set a memory limit.
- **NVIDIA graphics card:** not covered. It needs the NVIDIA device plugin for K3s, `OLLAMA_LIBS=all`, and a
  `nvidia.com/gpu` resource in the recipe.

## Removing every Household AI container

Over SSH on the NAS. Run the parts that match how you've run Household AI; a command for something that isn't there
just says *not found*. Do this before a fresh install too, so an old copy doesn't hold port 11434.

**Kubernetes (this recipe).** Deleting the namespace removes the pod, the Deployment, the Service, the Ingress Route,
the password Secret and the volume claim; then the volume itself, and K3s's copy of the image:

```bash
sudo k3s kubectl delete namespace household-ai-app
sudo k3s kubectl delete pv household-ai-data
sudo k3s crictl rmi localhost/household-ai:local
sudo k3s kubectl get all,pv -A | grep -i household-ai     # prints nothing when it's all gone
```

**Podman** (started with `podman run`, `podman compose` or the Quadlet file in `../README.md`). Root's and each user's
containers are separate, so check both:

```bash
# Stop the Quadlet service first, if you made one (otherwise it starts the container again):
sudo systemctl disable --now household-ai 2>/dev/null; systemctl --user disable --now household-ai 2>/dev/null
sudo rm -f /etc/containers/systemd/household-ai.container; rm -f ~/.config/containers/systemd/household-ai.container
sudo systemctl daemon-reload; systemctl --user daemon-reload

# Every container made from the image or named household-ai, running or stopped:
for p in "sudo podman" "podman"; do
  $p ps -aq --filter name=household-ai --filter ancestor=localhost/household-ai:local | sort -u | xargs -r $p rm -f
  $p rmi -f localhost/household-ai:local 2>/dev/null
done
sudo podman ps -a; podman ps -a                           # household-ai shouldn't be listed
```

**Docker** (the compose file in `../docker-compose.yml`), if the NAS has it: in the `household_ai_docker` folder,
`docker compose down --rmi local`, or `docker rm -f household-ai && docker rmi household-ai:local`.

**What's kept:** the shared folder with the settings, access keys, usage and the models. A new install picks them up
again. To start from nothing, delete the folder's contents (**Storage → Shared Folders**, or `sudo rm -rf` on its
path), or remove the shared folder itself.
