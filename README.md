# Epik8s Project Generator

**Version:** 0.2.14

This script automates the creation of Kubernetes project structures and Helm charts for EPICS-based applications. It generates required configuration files, templates, and directory structures, along with a customized `README.md` for documenting the project.

## Features
- Creates a project directory structure for managing EPICS applications in Kubernetes.
- Generates `Chart.yaml`, `values.yaml`, and `README.md` files based on templates.
- Copies IOC, application, and service configurations from templates based on user inputs.
- Supports additional backend services and OpenShift.

## Prerequisites
- **Python 3.6 or greater +**
- **PyYAML**: `pip install pyyaml`
- **Jinja2**: `pip install jinja2`

## Usage
Run the script using:
```bash
python epik8s-gen.py <project_name> --beamlinerepogit <git_url> --dnsnamespace <dns> [options]
```


## Arguments

| Argument             | Type     | Default           | Required | Description                                                                                     |
|----------------------|----------|-------------------|----------|-------------------------------------------------------------------------------------------------|
| `project_name`       | String   | None              | Yes      | Name of the project.                                                                            |
| `--beamline`         | String   | `project_name`    | No       | Beamline name. Defaults to the project name if not provided.                                    |
| `--namespace`        | String   | `beamline`        | No       | Namespace for the beamline. Defaults to the beamline name if not provided.                      |
| `--beamlinerepogit`  | String   | None              | Yes      | Git URL of the beamline repository.                                                             |
| `--beamlinereporev`  | String   | `main`            | No       | Git revision to use (e.g., branch or tag).                                                      |
| `--iocbaseip`        | CIDR     | None              | No       | Base IP for static IPs on IOCs. Format: `CIDR` (e.g., `10.152.183.0/24`).                      |
| `--iocstartip`       | Integer  | `2`               | No       | Starting IP offset for IOC static IP addressing.                                               |
| `--cagatewayip`      | IP       | None              | Yes      | Load balancer IP for Channel Access Gateway.                                                    |
| `--pvagatewayip`     | IP       | None              | Yes      | Load balancer IP for PV Access Gateway.                                                         |
| `--dnsnamespace`     | String   | None              | Yes      | DNS or IP for ingress definitions.                                                              |
| `--targetRevision`   | String   | `experimental`    | No       | Target revision for the deployment.                                                             |
| `--serviceAccount`   | String   | `default`         | No       | Service account name to use in Kubernetes.                                                      |
| `--nfsserver`        | String   | None              | No       | NFS server IP or hostname for storage.                                                          |
| `--nfsdirdata`       | Path     | `/epik8s/data`    | No       | Directory path on the NFS server for data storage.                                              |
| `--nfsdirautosave`   | Path     | `/epik8s/autosave`| No       | Directory path on the NFS server for autosave files.                                            |
| `--nfsdirconfig`     | Path     | `/epik8s/config`  | No       | Directory path on the NFS server for configuration files.                                       |
| `--backend`          | Boolean  | None              | No       | Activates backend services if set.                                                              |
| `--openshift`        | Boolean  | `False`           | No       | Activates OpenShift-specific configurations if set to `True`.                                   |
| `--version`          | Flag     | None              | No       | Displays the version of the script and exits.                                                   |
| `--help`             | Flag     | None              | No       | Displays help information about the script.                                                     |

### Argument Details
- **project_name**: Primary argument; creates a Kubernetes project structure under this name.
- **beamline & namespace**: Allow customization of Kubernetes names; defaults provide convenience.
- **beamlinerepogit**: Required Git repository URL for the beamline’s source code.
- **cagatewayip & pvagatewayip**: Define gateway IPs for Channel Access and PV Access, ensuring network access.
- **NFS Directories**: Specify paths for persistent storage directories (data, autosave, config).
- **backend & openshift**: Optional flags for additional service configurations and OpenShift compatibility.

## Example
```bash
epik8s-gen testbeamline --beamlinerepogit https://baltig.infn.it/epics-containers/epik8s-testbeamline.git --dnsnamespace "pldanteco101.lnf.infn.it --cagatewayip 192.168.114.200 --pvagatewayip 192.168.114.201 
```