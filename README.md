# epik8s-tools

`epik8s-tools` is a Python-based toolset for automating project structure generation, Helm chart creation, and deployment for EPICS (Experimental Physics and Industrial Control System) applications in Kubernetes environments [*EPIK8s*](https://confluence.infn.it/x/AgDoDg). 
Designed to simplify complex deployment configurations, this package includes a command-line interface for rendering templates based on YAML configurations, making it easy to manage beamline and IOC (Input/Output Controller) configurations with a consistent structure.
A simple guide to bring up a k8s single node cluster (extensible) is [*microk8s*](https://confluence.infn.it/x/DYC2H).

## Features

- **Project Structure Generation**: Automatically create directories and files needed for EPICS-based projects.
- **Helm Chart Creation**: Generate Helm charts for Kubernetes deployments with custom values and templates.
- **OPI Generation**: Configure OPI (Operator Interface) panels for each beamline, including macros and settings.
- **Support for Ingress and Load Balancers**: Configurable settings for CA and PVA gateway IPs and ingress classes.
- **Customizable Options**: Extensive CLI options to adapt configurations to specific project needs.
- **IOC Execution**: Run IOC configurations directly using the `epik8s-run` tool.
- **Docker Compose Generation**: Build ready-to-run single-node Docker Compose deployments with `epik8s-compose`.

## Installation

Install `epik8s-tools` via pip:

```bash
pip install epik8s-tools
```

### CLI Options

| Option              | Description                                                                             |
|---------------------|-----------------------------------------------------------------------------------------|
| `--beamline`        | Name of the beamline to configure.                                                      |
| `--namespace`       | Kubernetes namespace for the beamline deployment.                                       |
| `--targetRevision`  | Target revision for Helm charts (default: `experimental`).                              |
| `--serviceAccount`  | Service account for Kubernetes.                                                         |
| `--beamlinerepogit` | Git URL of the beamline repository.                                                     |
| `--beamlinereporev` | Git revision for the repository (default: `main`).                                      |
| `--iocbaseip`       | Base IP range for IOCs (e.g., `10.96.0.0/12`).                                          |
| `--iocstartip`      | Start IP within the IOC base range (default: `2`).                                      |
| `--cagatewayip`     | IP for the CA gateway load balancer.                                                    |
| `--pvagatewayip`    | IP for the PVA gateway load balancer.                                                   |
| `--dnsnamespace`    | DNS/IP address for ingress configuration.                                               |
| `--ingressclass`    | Specify ingress class (`haproxy`, `nginx`, or empty for no ingress class).              |
| `--nfsserver`       | NFS server address.                                                                     |
| `--nfsdirdata`      | NFS directory for data partition (default: `/epik8s/data`).                             |
| `--nfsdirautosave`  | NFS directory for autosave partition (default: `/epik8s/autosave`).                     |
| `--nfsdirconfig`    | NFS directory for config partition (default: `/epik8s/config`).                         |
| `--elasticsearch`   | ElasticSearch server address.                                                           |
| `--mongodb`         | MongoDB server address.                                                                 |
| `--kafka`           | Kafka server address.                                                                   |
| `--vcams`           | Number of simulated cameras to generate (default: `1`).                                 |
| `--vicpdas`         | Number of simulated ICPDAS devices to generate (default: `1`).                          |
| `--mysqlchart`      | Use custom MySQL chart instead of Bitnami (for microk8s).                               |
| `--channelfinder`   | Enable ChannelFinder and feeder services.                                               |
| `--generate-settings-ini` | Generate `opi/settings.ini` in projects created by `epik8s-gen`.                 |
| `--openshift`       | Flag for enabling OpenShift support.                                                    |
| `--token`           | Git personal token for repository access, if required.                                  |
| `--version`         | Show version information and exit.                                                      |

---

### Examples

#### Basic Beamline Generation

Generate a new project structure for a beamline with the following command:

```bash
epik8s-tools my_project --beamline MyBeamline --iocbaseip 10.96.0.0/12 --beamlinerepogit https://github.com/beamline/repo.git
```

### Generating OPI Panels

To generate OPI panels from beamline YAML configuration files, you can use the `epik8s-opigen` tool. This tool reads a beamline configuration and produces a Phoebus project with a generated launcher and a local copy or link to the reusable `epik8s-opi` widget library. Generation of `settings.ini` is optional and disabled by default.

#### Example Command

```bash
epik8s-opigen --config deploy/values.yaml --projectdir opi-output
```
- **`--config`**: Path to the beamline YAML configuration file (e.g., `deploy/values.yaml`).
- **`--projectdir`**: Directory where the OPI files will be generated (e.g., `opi-output`).

If you also want Phoebus settings generated in the project directory, add:

```bash
epik8s-opigen --config deploy/values.yaml --projectdir opi-output \
  --generate-settings-ini
```

This command will generate the OPI panel files based on the configurations specified in the YAML file and save them in the specified output directory.

The generated `Launcher.bob` uses a **dashboard layout** rather than nested tabs:

- **Header banner** with beamline name, IOC count, and namespace
- **Device-group sections** (Motors, Cameras, Vacuum, etc.) each showing:
  - A section header with device count
  - One row per device with an **embedded compact display** (e.g., `mot_channel.bob` for motors) showing live status inline
  - An **"Open ⬈" button** per device that opens the full detail screen (e.g., `Motor_Main.bob`, `Camera_Main.bob`) in an **independent Phoebus window**
- **Services section** showing gateways and load balancer IPs

This design replaces the previous tab-locked layout, allowing operators to simultaneously view and control devices of different types (e.g., motors and cameras) in separate windows.

#### Detailed Launcher

To generate a super-detailed per-IOC interface that includes PV monitoring and control panels, use the `--detailed` flag together with `--pvlist-dir` pointing to a directory containing runtime-generated PV lists:

```bash
epik8s-opigen --config beamline.yaml --projectdir opi \
  --detailed --pvlist-dir test-compose/iocs
```

The `--pvlist-dir` directory is expected to contain one subdirectory per IOC name, each with a `pvlist.txt` file (e.g., `test-compose/iocs/motorsim/pvlist.txt`).

This produces a `Launcher_detailed.bob` (customizable via `--detailed-output`) organized as:

- **Overview** tab — beamline summary identical to the normal launcher.
- **Per-IOC tabs** (one tab per IOC), each containing:
  - **Info** — static IOC metadata from the YAML (prefix, template, devices, parameters).
  - **Per-device sub-tabs** — PV readback (`TextUpdate`) and setpoint (`TextEntry`) widgets for every PV, automatically categorized by subsystem (e.g., Main, Roi1, Stats1, Proc1).
  - **Other PVs** — PVs that could not be matched to a known device.
  - **All PVs** — flat list of every PV published by the IOC.

PVs ending in `_RBV` are rendered as read-only `TextUpdate` widgets; all others are rendered as editable `TextEntry` widgets.

---

### Specifying CA and PVA Gateway IPs

For projects that require external access to Channel Access (CA) and PV Access (PVA) gateways, you can specify the IP addresses for the respective load balancers using the `--cagatewayip` and `--pvagatewayip` options.

#### Example Command

```bash
epik8s-tools my_project --beamline MyBeamline --cagatewayip 10.96.1.10 --pvagatewayip 10.96.1.11
```

---

### Running IOCs with `epik8s-run`

The `epik8s-run` tool allows you to execute IOC configurations directly from a YAML file.

#### Example Command

```bash
epik8s-run beamline-config.yaml ioc1 ioc2 --workdir ./workdir --native
```

- **`beamline-config.yaml`**: Path to the YAML configuration file containing IOC definitions.
- **`ioc1`, `ioc2`**: Names of the IOCs to run.
- **`--workdir`**: Working directory for temporary files (default: `.`).
- **`--native`**: Run natively without using Docker.
- **`--image`**: Specify the Docker image to use (default: `ghcr.io/infn-epics/infn-epics-ioc-runtime:latest`).

This command will validate the IOC configurations, generate necessary files, and start the IOCs either natively or in a Docker container.

---

### Generating a Single-Node Docker Compose with `epik8s-compose`

The `epik8s-compose` tool converts a beamline YAML configuration into a ready-to-use directory for Docker Compose.

#### Example Command

```bash
epik8s-compose --config tests/beamline.yaml --output test-compose
```

Generated output includes:

- `docker-compose.yaml`
- `epics.env` (shared EPICS environment)
- `epics-channel.env` (host helper for CA/PVA access)
- per-IOC directories in `iocs/<iocname>/`

#### Start the Beamline

```bash
cd test-compose
docker compose up
```

#### Useful Options

- `--caport`: starting CA port for gateway mappings (default `5064`)
- `--pvaport`: starting PVA port for gateway mappings (default `5075`)
- `--htmlport`: starting HTTP port for ingress-mapped services (default `8090`)
- `--services`: include only selected services/IOCs
- `--exclude`: exclude selected services/IOCs
- `--platform`: target container platform (default `linux/amd64`)

Generated notebook services are treated specially when `--platform` remains the default `linux/amd64`: `epik8s-compose` emits `platform: ${NOTEBOOK_PLATFORM:-linux/arm64}` for the notebook container. This avoids dead Jupyter kernels on Apple Silicon hosts while preserving `amd64` as the default for the rest of the stack. Set `NOTEBOOK_PLATFORM=linux/amd64` if you explicitly want the notebook container to use `amd64` as well.

To keep notebook files persistent in compose, set `epicsConfiguration.services.notebook.dataVolume.hostPath` in the beamline YAML. Relative paths are resolved from the beamline config directory, so a config like this mounts a project folder into Jupyter workdir:

```yaml
epicsConfiguration:
  services:
    notebook:
      dataVolume:
        hostPath: notebook_examples
```

`epik8s-compose` will bind-mount that directory into `/home/jovyan/work` instead of generating the default `notebook-work` folder under the compose output.

---

## GitHub Actions

This repository includes GitHub workflows for CI, tag creation, and PyPI publishing.

### Compose CI

Workflow: `.github/workflows/compose-ci.yml`

- Runs on push and pull request to `main`
- Tests `epik8s-compose` generation on sample configurations
- Uploads generated compose artifacts for inspection

### Create Release Tag

Workflow: `.github/workflows/create-release-tag.yml`

- Manual trigger (`workflow_dispatch`)
- Input: semantic version without `v` (for example `0.10.4`)
- Creates and pushes tag `v<version>`
- Creates a GitHub Release with generated notes

### Publish to PyPI

Workflow: `.github/workflows/publish-pypi.yml`

- Triggered automatically on tag push matching `v*`
- Builds the package and uploads to PyPI via `twine`

Required GitHub secret:

- `PYPI_API_TOKEN`: PyPI API token with upload permissions for `epik8s-tools`

### Recommended Release Flow

1. Update package version in `epik8s_tools/__init__.py`.
2. Merge changes to `main`.
3. Run `Create Release Tag` workflow and provide the new version.
4. `Publish PyPI` workflow runs automatically on the pushed tag.