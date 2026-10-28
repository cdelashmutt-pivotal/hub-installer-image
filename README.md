# Tanzu Hub installer environment

Local kind cluster + a Linux container for running the (Linux-only) Tanzu Hub
installer against it from macOS/Windows.

## Quickstart

### 0. Create the Podman machine (macOS)

```bash
./setup-podman-machine.sh
```

Safe to rerun. Requires `rosetta = true` under `[machine]` in
`~/.config/containers/containers.conf` — the script checks for this and
tells you what to add if it's missing.

### 1. Create the cluster and generate the install config

```bash
./setup-cluster.sh # Install the TSE version of Hub
HUB_SKU=full ./setup-cluster.sh # Install the full Hub
```

Safe to rerun.

Then add `hub.local` to `/etc/hosts` (the installer's ingress hostname —
see **Known issue: use a dotted ingress hostname, not `localhost`** below
for why it isn't `localhost`). The warning at the top of `/etc/hosts` is
only about not touching the existing `127.0.0.1 localhost` / `::1 localhost`
lines — anywhere else in the file is fine to add to:

```
127.0.0.1 hub.local
```

Then flush your DNS cache so it takes effect immediately:

```bash
sudo dscacheutil -flushcache; sudo killall -HUP mDNSResponder
```

Once installed (step 3), run `./enable-port-443-redirect.sh` once (requires `sudo`).  See **Known issue: binding host port 443** below.

### 2. Build the installer container

```bash
docker build -t hub-installer-shell .
```

### 3. Run the installer

You'll need the installer tarball (e.g. `hub-installer-online-11.0.0.tar`)
downloaded already — it isn't bundled in this repo. The command below
assumes it's sitting in `~/Downloads`; change that mount if yours is
somewhere else.

```bash
docker run -it --rm \
  -v ~/.kube/config:/host-kube/config:ro \
  -v ~/Downloads:/workspace \
  -v "$(pwd)/hub-install-config.yaml:/workspace/hub-install-config.yaml:ro" \
  -v "$(pwd)/run-install.sh:/workspace/run-install.sh:ro" \
  -e TANZU_HUB_TOKEN \
  -e TANZU_HUB_USERNAME \
  hub-installer-shell
```

`-e TANZU_HUB_TOKEN`/`-e TANZU_HUB_USERNAME` with no `=value` forward
whatever those vars are set to in your host shell — harmless if unset,
`run-install.sh` just prompts instead. If the cluster's load balancer IP
may have changed since you last ran `setup-cluster.sh` (e.g. you recreated
the cluster), rerun it first so `hub-install-config.yaml` is up to date.

Inside the container, `kubectl`/`k` are already pointed at the kind cluster —
extract the installer tarball under `/workspace` if you haven't already,
then run the installer from its own directory:

```bash
tar -xf hub-installer-online-11.0.0.tar
cd hub-installer-online-11.0.0
cp /workspace/run-install.sh .   # tanzu-sm-installer must be run from its own directory
./run-install.sh
```

`run-install.sh` prompts for your Broadcom support portal username and
registry token (the token expires after ~2 days), then runs
`tanzu-sm-installer install` for you. It never writes the token to shell
history — see **Background: why `run-install.sh` prompts for the token**
below for the full reasoning. Other ways to invoke it:

```bash
./run-install.sh                                    # prompts for the token
TANZU_HUB_TOKEN=xxx ./run-install.sh
TANZU_HUB_USERNAME=someone@broadcom.com TANZU_HUB_TOKEN=xxx ./run-install.sh
```

If you're installing a different bundle version than the default
(`11.0.0-350-europa+va99b428`), set `INSTALL_VERSION` rather than editing
the script.

## Optional: skip the token prompt with direnv

Since you have to fetch a fresh token from the Broadcom support portal every
~2 days anyway, you can skip the interactive prompt by keeping it in a
direnv-managed `.envrc` instead:

```bash
cp .envrc.example .envrc
# edit .envrc, paste the real token in
direnv allow
```

`cd` back into this directory (or just stay in it) and `TANZU_HUB_TOKEN` is
exported in your shell automatically; the `-e TANZU_HUB_TOKEN` in the
`docker run` command above forwards it straight into the container, where
`run-install.sh` picks it up and skips the prompt entirely. `.envrc` is
gitignored, so the real token never gets committed — just overwrite that one
line every time you generate a new one.

Trade-off worth knowing: this puts the plaintext token on disk (`.envrc`)
instead of only ever living in your terminal's memory for a prompt. For a
token that already expires in 2 days and never leaves your laptop, that's a
reasonable trade for not re-typing it — just don't widen that to anything
longer-lived without reconsidering.

## Optional: trust Hub's self-signed cert for Application Advisor

Only needed if you're also using the Application Advisor CLI against this
Hub instance. Hub's ingress uses a self-signed cert
(`ingress.host: hub.local` in
[hub-install-config.yaml](hub-install-config.yaml)), so the Advisor CLI
rejects it by default. This is a client-side step, separate from the
installer — run it once Hub is up and reachable:

```bash
./trust-advisor-cert.sh                 # defaults to hub.local:443
```

This writes `server-cert.pem` in this directory (gitignored). Point the
Advisor CLI at it via `APP_ADVISOR_OPTIONS_CERT_PATH`:

```bash
APP_ADVISOR_OPTIONS_CERT_PATH="$PWD/server-cert.pem" advisor build-config publish
```

or export it once to cover every command in the session:

```bash
export APP_ADVISOR_OPTIONS_CERT_PATH="$PWD/server-cert.pem"
```

`.envrc`/`.envrc.example` already set this (see **Optional: skip the token
prompt with direnv** above) — `direnv allow` once and it's automatic for the
rest of this directory's lifetime.

**Note:** the techdocs page linked above documents a different approach
(`-Djavax.net.ssl.trustStore=...` plus a `keytool`-built JKS truststore),
which assumes `advisor` is launched via `java` and therefore also honors
`JDK_JAVA_OPTIONS`. It doesn't — `advisor` is a GraalVM native-image binary,
so `JDK_JAVA_OPTIONS` is silently ignored (confirmed by testing). The
`APP_ADVISOR_OPTIONS_CERT_PATH` env var above isn't in the official docs
either, but it's read directly by the `advisor` binary itself (confirmed by
pointing it at a missing file and getting an error that names the var), and
it's simpler — no truststore/keytool step needed.

Hub's cert is regenerated every time the kind cluster is recreated, so rerun
`trust-advisor-cert.sh` after every `teardown-cluster.sh` +
`setup-cluster.sh` cycle — safe to rerun any time. `server-cert.pem` is
overwritten in place, so nothing needs updating in `.envrc` when that
happens.

## Teardown

```bash
./teardown-cluster.sh
```

Deletes the kind cluster (`kind delete cluster --name hub-cluster`) and
removes `hub-install-config.yaml`, since its `loadBalancerIP` becomes invalid
once the cluster is gone. Rerun `setup-cluster.sh` to stand everything back
up — it's a fresh cluster each time, not a resume.

`kind delete cluster` does not hit the podman labels bug described below (it
targets containers by name/cluster, not a global labeled list), so it's safe
to use even though `kind get clusters` isn't.

## Reference

### Files

| File | Purpose |
|---|---|
| `hub-cluster.yaml` | kind cluster config — 1 control-plane + 3 workers (anti-affinity), one worker exposes NodePort 30443 on host port 8443 |
| `sc.yaml` | StorageClass (`tp-storage-class`) applied after cluster creation |
| `setup-podman-machine.sh` | Creates/starts the Podman machine (12 CPU, 44.7 GiB, 93 GiB disk, rootful, Rosetta) and sets `pids_limit=0` for root in the VM — run this before `setup-cluster.sh` |
| `setup-cluster.sh` | Creates the cluster, applies the storage class, resolves the ingress load balancer IP, renders `hub-install-config.yaml` |
| `enable-port-443-redirect.sh` | One-time (per reboot) `sudo pf` setup redirecting host port 443 -> 8443, so Hub is reachable at plain `https://hub.local` — see **Known issue: binding host port 443** below |
| `disable-port-443-redirect.sh` | Removes the pf redirect above |
| `teardown-cluster.sh` | Deletes the kind cluster and removes the now-stale `hub-install-config.yaml` |
| `trust-advisor-cert.sh` | Extracts Hub's self-signed ingress cert to `server-cert.pem` for the Application Advisor CLI — see **Optional: trust Hub's self-signed cert for Application Advisor** above |
| `hub-install-config.yaml.tmpl` | Template for the Hub installer CLI config (`loadBalancerIP` and `hubSku` are placeholders) |
| `hub-install-config.yaml` | Generated by `setup-cluster.sh` — **don't hand-edit, rerun the script instead** |
| `run-install.sh` | Prompts for the short-lived registry token and runs `tanzu-sm-installer install` without ever putting the token in shell history |
| `Dockerfile` / `entrypoint.sh` | Ubuntu container with `kubectl` (+ `k` alias) for running the installer; rewrites your kubeconfig's `127.0.0.1` cluster entries to `host.docker.internal` with TLS verification skipped |
| `.dockerignore` | Keeps the installer tarball out of the build context |

### Background: what `setup-cluster.sh` does

1. Create the kind cluster from `hub-cluster.yaml` if it doesn't already exist
   (checked via `docker ps`, not `kind get clusters` — see **Known issue**
   below).
2. `kubectl apply -f sc.yaml`.
3. Find the worker container that maps NodePort 30443 and read its container
   network IP via `docker inspect`.
4. Write `hub-install-config.yaml` from the template with that IP filled in,
   along with `hubSku` (`tse` by default — set `HUB_SKU=full` to install the
   full Hub SKU instead).

### Background: how the installer container reaches the cluster

- `~/.kube/config` is mounted **read-only**; your real file is never touched.
  The entrypoint copies it, rewrites any `127.0.0.1` cluster server to
  `host.docker.internal`, and sets `insecure-skip-tls-verify: true` (dropping
  `certificate-authority-data`, since kubectl rejects having both set) — this
  is what lets the container reach the kind cluster running on the host.
- `~/Downloads` (wherever the installer tarball actually lives) is mounted at
  `/workspace`, the container's default working directory. The tarball itself
  is intentionally **not** baked into the image (446MB, changes independently
  of this Dockerfile).
- The generated `hub-install-config.yaml` lives in this project directory, not
  `~/Downloads`, so it's mounted into `/workspace` separately alongside the
  tarball.

### Background: why `run-install.sh` prompts for the token

`tanzu-sm-installer install` only supports credentials as a CLI flag
(`-u/--authentication user:token`) — there's no environment-variable or
credentials-file alternative (checked `install --help`). Two consequences,
and what `run-install.sh` does about them:

- **Shell history**: typing the full install command with the token inline
  writes the plaintext token to `~/.bash_history`/`~/.zsh_history`.
  `run-install.sh` prompts for the token with `read -s` instead, so it's
  never part of a typed command line.
- **`ps`/`/proc` exposure**: since it's a CLI flag, the token is visible in
  the process list (`ps aux`, `/proc/<pid>/cmdline`) to anything else that
  can see into the container for as long as `tanzu-sm-installer` is running.
  There's no way around this given the tool's interface — it's a brief
  window, and this container is single-user, so the residual risk is low,
  but it's worth knowing about.

The repository path's version slug is derived from `INSTALL_VERSION`
(default `11.0.0-350-europa+va99b428`, `+` swapped for `--` to match the
registry path).

### Background: Podman machine sizing

Override sizing with `PODMAN_MACHINE_CPUS`, `PODMAN_MACHINE_MEMORY_MIB`,
`PODMAN_MACHINE_DISK_GIB` when running `setup-podman-machine.sh`. CPU,
memory, disk, rootful and Rosetta are all fixed at init; to change them,
`podman machine rm` and rerun.

## Known issue: use a dotted ingress hostname, not `localhost`

`ingress.host` is `hub.local`, not `localhost`, because the JDK's
`HttpsURLConnection` silently omits the TLS SNI extension for bare,
dot-less hostnames like `localhost` (confirmed via
`-Djavax.net.ssl.debug=ssl:handshake` — look for `ServerNameExtension:
Unable to indicate server name`). Hub's ingress (Envoy via Contour) routes
by SNI, so with no SNI presented it just closes the connection —
`SSLHandshakeException: Remote host terminated the handshake` /
`EOFException: SSL peer shut down incorrectly`. `curl` isn't affected (it
always sends SNI for hostname targets), so this only bites Java-based
clients like the Application Advisor CLI — the Hub UI in a browser and
`curl -k` both work fine against bare `localhost`. Any hostname with a dot
in it (`hub.local`, `hub.localdomain`, etc.) sidesteps the bug.

## Known issue: changing `ingress.host` on an already-installed cluster doesn't rotate the cert

`tanzu-sm-installer install` only generates Hub's self-signed ingress cert
when none already exists — on a rerun against a cluster that's already
installed, it reads back the current `ingress-cert` secret's cert/key and
carries them forward unchanged, even though every other host-derived value
(OAuth redirect URIs, launch URLs, etc.) does get re-rendered correctly.
Confirmed by diffing the installer's own `tanzu-sm-values-ver-N` secrets
across two runs with different `ingress.host` values — only the
`ingress.tls.certificate`/`privateKey` blocks stayed byte-for-byte
identical. Deleting the live `ingress-cert` Kubernetes secret doesn't help
either — kapp-controller just restores it from the same stale values on its
next reconcile.

This only matters if you change `ingress.host` on a cluster that's already
had Hub installed once (e.g. you started on `localhost` and are switching to
`hub.local` without tearing down). A `teardown-cluster.sh` + `setup-cluster.sh`
+ fresh install never hits this, since there's no previous cert to carry
forward. If you do need to fix it in place:

```bash
# 1. Find the App's current values secret name and pull it down:
kubectl -n tanzusm get app sm -o jsonpath='{.spec.template[0].ytt.valuesFrom[1].secretRef.name}'
kubectl -n tanzusm get secret <that-secret-name> -o jsonpath='{.data.values\.yaml}' | base64 -d > /tmp/values.yaml

# 2. Blank out the ingress.tls.certificate and ingress.tls.privateKey values
#    (set both to "", keep the keys) in /tmp/values.yaml, then patch it back in:
kubectl -n tanzusm patch secret <that-secret-name> --type merge \
  -p "{\"data\":{\"values.yaml\":\"$(base64 < /tmp/values.yaml | tr -d '\n')\"}}"

# 3. Force an immediate reconcile (don't wait for the 10m syncPeriod) so the
#    chart renders with the blanked cert — this will briefly break Hub's TLS,
#    since this layer has no "generate if missing" fallback of its own:
kubectl -n tanzusm patch app sm --type merge -p '{"spec":{"syncPeriod":"9m59s"}}'
kubectl -n tanzusm patch app sm --type merge -p '{"spec":{"syncPeriod":"10m0s"}}'

# 4. Rerun ./run-install.sh. With no existing cert to read back (live secret
#    is now blank too), the installer generates a fresh one for the current
#    ingress.host.
```

Verify with:

```bash
kubectl -n tanzusm get secret ingress-cert -o jsonpath='{.data.tls\.crt}' | base64 -d | openssl x509 -noout -subject -ext subjectAltName
```

Then rerun `./trust-advisor-cert.sh` to pick up the new cert.

## Known issue: `kind get clusters` fails under podman

If `docker` on your machine is actually podman (`docker --version` reports a
podman version), `kind get clusters` can fail with a podman `ps --format`
template bug indexing `.Labels` — even when the cluster is running fine.
`setup-cluster.sh` works around this by checking for the
`<cluster-name>-control-plane` container directly via `docker ps` instead of
trusting `kind get clusters`.

## Known issue: binding host port 443

`hub-cluster.yaml` maps the worker's NodePort 30443 to host port 8443, not
443. Port 443 is privileged, and on macOS the podman machine's host-side
port-forwarder (gvproxy/vfkit) runs as your normal user — even though the
podman machine itself is rootful — so it can't bind ports below 1024.
Pointing `hostPort` at 443 directly fails `kind create cluster` with:

```
Error: something went wrong with the request: "listen tcp 127.0.0.1:443: bind: permission denied"
```

There's no macOS equivalent of Linux's `net.ipv4.ip_unprivileged_port_start`
to work around this, so instead `enable-port-443-redirect.sh` installs a
`pf` anchor that redirects loopback TCP 443 to 8443:

```bash
./enable-port-443-redirect.sh    # one-time per reboot, requires sudo
```

This isn't persistent across reboots — rerun it (before or after
`setup-cluster.sh`, order doesn't matter) any time the redirect stops
working. `./disable-port-443-redirect.sh` removes it.

## Known issue: Hub pods fail with `fork: Resource temporarily unavailable`

Symptoms: UI errors, ~30s request timeouts, Hikari `Connection is not available
... total=0` in `graphql-rest-provider-service`.

Cause: `podman machine init` writes `pids_limit=0` only for the rootless `core`
user, but the machine is rootful, so kind's node containers get Podman's default
`--pids-limit 2048`. systemd inside each node then caps every container scope at
15% of that (307 tasks), and the Hub JVMs run out of threads. Check with
`kubectl -n tanzusm exec <pod> -- cat /sys/fs/cgroup/pids.max`.

`setup-podman-machine.sh` prevents this for newly created nodes, and
`setup-cluster.sh` warns and asks for confirmation if existing nodes have a PID
limit (or the VM isn't configured for new ones). It refuses to continue without a
terminal. For an existing
cluster, fix in place (no rebuild):

```bash
for n in $(podman ps --format '{{.Names}}' | grep '^hub-cluster-'); do
  podman update --pids-limit -1 "$n"
  podman exec "$n" systemctl daemon-reexec
done
kubectl -n tanzusm rollout restart deploy/graphql-rest-provider-service
```
