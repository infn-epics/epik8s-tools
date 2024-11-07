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

To generate OPI panels from YAML configuration files, you can use the `epik8s-opigen` tool. This tool reads a YAML file with OPI configurations and outputs the generated OPI files in the specified project directory.

#### Example Command

```bash
epik8s-opigen --yaml deploy/values.yaml --projectdir opi-output
```
- **`--yaml`**: Path to the YAML configuration file (e.g., `deploy/values.yaml`).
- **`--projectdir`**: Directory where the OPI files will be generated (e.g., `opi-output`).

This command will generate the OPI panel files based on the configurations specified in the YAML file and save them in the specified output directory.

---

### Specifying CA and PVA Gateway IPs

For projects that require external access to Channel Access (CA) and PV Access (PVA) gateways, you can specify the IP addresses for the respective load balancers using the `--cagatewayip` and `--pvagatewayip` options.

#### Example Command

```bash
epik8s-tools my_project --beamline MyBeamline --cagatewayip 10.96.1.10 --pvagatewayip 10.96.1.11
```