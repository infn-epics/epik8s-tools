import os
import sys
import argparse
import shutil
import copy

from jinja2 import Template
from epik8s_tools import __version__
import yaml
from .epik8s_common import apply_ioc_defaults

# Default images for well-known services
DEFAULT_SERVICE_IMAGES = {
    "gateway": "ghcr.io/infn-epics/docker-ca-gateway:latest",
    "pvagateway": "baltig.infn.it:4567/epics-containers/docker-pva-gateway",
    "archiver": "ghcr.io/infn-epics/docker-archiver-appliance",
    "pvws": "ghcr.io/infn-epics/phoebus-pvws",
    "dbwr": "ghcr.io/infn-epics/phoebus-dbwr",
    "notebook": "ghcr.io/infn-epics/jupyter-science-epics:latest",
    "alarmserver": "ghcr.io/infn-epics/phoebus-alarm-server",
    "alarmlogger": "ghcr.io/infn-epics/phoebus-alarm-logger",
    "saveandrestore": "ghcr.io/infn-epics/phoebus-save-and-restore",
    "channelfinder": "ghcr.io/infn-epics/phoebus-channelfinder",
    "console": "ghcr.io/infn-epics/phoebus-olog",
    "webalarm": "ghcr.io/infn-epics/phoebus-alarm-screen",
    "scanserver": "ghcr.io/infn-epics/phoebus-scan-server",
}

# Well-known internal ports for ingress-enabled services
SERVICE_INTERNAL_PORTS = {
    "archiver": 17665,
    "pvws": 8080,
    "dbwr": 8080,
    "notebook": 8888,
    "console": 8080,
    "webalarm": 8080,
    "webolog": 8080,
    "saveandrestore": 8080,
    "channelfinder": 8080,
    "alarmserver": 8080,
    "scanserver": 8080,
}

DEFAULT_IOC_IMAGE = "ghcr.io/infn-epics/infn-epics-ioc-runtime"
DEFAULT_HOSTNETWORK_CA_PORT_BASE = 16064
DEFAULT_HOSTNETWORK_PVA_PORT_BASE = 17075


def parse_config(file_path):
    with open(file_path, 'r') as file:
        return yaml.safe_load(file)


def write_file(directory, content, fname):
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, fname)
    with open(path, 'w') as f:
        f.write(content)
    return path


def copy_directory(src, dest):
    if os.path.exists(dest):
        shutil.rmtree(dest)
    shutil.copytree(src, dest)


def render_j2_files(directory, config):
    """Render all .j2 templates in *directory* using *config* as Jinja2 context."""
    for root, _, files in os.walk(directory):
        for fname in files:
            if fname.endswith(".j2"):
                file_path = os.path.join(root, fname)
                with open(file_path, 'r') as f:
                    rendered = Template(f.read()).render(config)
                with open(file_path[:-3], 'w') as f:
                    f.write(rendered)


def _resolve_image(service_name, service_val):
    """Return (image_string, needs_skip) for a service entry."""
    if 'image' in service_val:
        img = service_val['image']
        if isinstance(img, dict):
            repo = img.get('repository')
            tag = img.get('tag')
            if not repo:
                return None, True
            return f"{repo}:{tag}" if tag else repo, False
        return str(img), False
    default = DEFAULT_SERVICE_IMAGES.get(service_name)
    if default:
        return default, False
    return None, True


def _is_enabled(value):
    """Interpret common truthy values from YAML/CLI content."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _ioc_discovery_target(ioc):
    """Return a resolvable target token for CA/PVA discovery env vars."""
    if _is_enabled(ioc.get('hostNetwork')):
        # With host networking, Docker DNS service names are not guaranteed to resolve.
        # Prefer an explicit override when provided.
        return str(ioc.get('hostAddress') or ioc.get('discoveryHost') or 'host.docker.internal')
    return str(ioc['name'])


def _ioc_runtime_info(ioc, hostnetwork_index):
    """Compute networking/runtime details for an IOC entry."""
    hostnetwork = _is_enabled(ioc.get('hostNetwork'))
    info = {
        'hostnetwork': hostnetwork,
        'ca_server_port': None,
        'ca_beacon_port': None,
        'pva_server_port': None,
        'ca_target': str(ioc['name']),
        'pva_target': str(ioc['name']),
    }
    if not hostnetwork:
        return info

    host = _ioc_discovery_target(ioc)
    ca_server_port = int(ioc.get('caServerPort', DEFAULT_HOSTNETWORK_CA_PORT_BASE + (hostnetwork_index * 2)))
    ca_beacon_port = int(ioc.get('caBeaconPort', ca_server_port + 1))
    pva_server_port = int(ioc.get('pvaServerPort', DEFAULT_HOSTNETWORK_PVA_PORT_BASE + hostnetwork_index))

    info.update({
        'ca_server_port': ca_server_port,
        'ca_beacon_port': ca_beacon_port,
        'pva_server_port': pva_server_port,
        'ca_target': f"{host}:{ca_server_port}",
        'pva_target': f"{host}:{pva_server_port}",
    })
    return info


# ---------------------------------------------------------------------------
# Core generation
# ---------------------------------------------------------------------------

def generate_docker_compose(config, args, caport, pvaport, ingressport):
    """Build a docker-compose dict and create per-service/IOC config dirs under *output_dir*.

    The runtime image already contains ibek-templates, ibek-support, jnjrender,
    and the IOC start.sh.  We only need to provide each IOC's config.yaml and
    let the container's `epik8s-run … --native` do the rendering at startup.
    """
    output_dir = args.output_dir
    exclude_services = args.exclude or []
    selected_services = args.services or None
    host_dir = args.host_dir  # may be None
    platform = args.platform

    epics_config = config.get('epicsConfiguration', {})
    docker_compose = {'services': {}}

    # ---- Collect IOC names/targets for env vars and startup ordering ----
    ioc_service_names = []
    ioc_runtime = {}
    epics_ca_addr_list = []
    epics_pva_addr_list = []
    has_hostnetwork_ioc = False
    hostnetwork_ioc_index = 0
    for ioc in epics_config.get('iocs', []):
        name = ioc['name']
        if selected_services and name not in selected_services:
            continue
        if name in exclude_services:
            continue

        info = _ioc_runtime_info(ioc, hostnetwork_ioc_index)
        if info['hostnetwork']:
            hostnetwork_ioc_index += 1

        ioc_runtime[name] = info
        ioc_service_names.append(name)
        epics_ca_addr_list.append(info['ca_target'])
        has_hostnetwork_ioc = has_hostnetwork_ioc or info['hostnetwork']
        if ioc.get('pva'):
            epics_pva_addr_list.append(info['pva_target'])

    # Env file shared by all containers (IOC-to-IOC address discovery)
    env_content = None
    if epics_ca_addr_list:
        env_content = (
            f'EPICS_CA_ADDR_LIST="{" ".join(epics_ca_addr_list)}"\n'
            f'EPICS_PVA_NAME_SERVERS="{" ".join(epics_pva_addr_list)}"\n'
            f'EPICS_PVA_ADDR_LIST="{" ".join(epics_pva_addr_list)}"\n'
        )

    # Host-side env helper (for caget/pvget from the host)
    env_host_content = ""

    # ---- Process services (gateway, archiver, pvws, …) ----
    for service, service_val in epics_config.get('services', {}).items():
        if selected_services and service not in selected_services:
            continue
        if service in exclude_services:
            print(f"%% service {service} excluded")
            continue

        image, skip = _resolve_image(service, service_val)
        if skip:
            print(f"%% service {service} skipped (no image)")
            continue

        svc = {'image': image}
        if platform:
            svc['platform'] = platform

        # --- Loadbalancer ports (gateway / pvagateway) ---
        if 'loadbalancer' in service_val:
            if service == "gateway":
                svc['ports'] = [
                    f"{caport}:5064/tcp", f"{caport}:5064/udp",
                    f"{caport+1}:5065/tcp", f"{caport+1}:5065/udp",
                ]
                svc['depends_on'] = {n: {"condition": "service_started"} for n in ioc_service_names}
                env_host_content += f"export EPICS_CA_ADDR_LIST=localhost:{caport}\n"
                caport += 2
            if service == "pvagateway":
                svc['ports'] = [
                    f"{pvaport}:5075/tcp", f"{pvaport+1}:5076/udp",
                ]
                svc['depends_on'] = {n: {"condition": "service_started"} for n in ioc_service_names}
                env_host_content += f"export EPICS_PVA_NAME_SERVERS=localhost:{pvaport}\n"
                pvaport += 2

        # --- Ingress ports (http services) ---
        if service_val.get('enable_ingress'):
            internal = SERVICE_INTERNAL_PORTS.get(service, 8080)
            svc.setdefault('ports', []).append(f"{ingressport}:{internal}")
            print(f"  ingress {service} -> localhost:{ingressport}")
            ingressport += 1

        # --- Environment ---
        if env_content:
            svc['env_file'] = ["epics.env"]

        # --- Service-specific env vars ---
        if 'env' in service_val:
            svc_env = {}
            for e in service_val['env']:
                svc_env[e['name']] = str(e['value'])
            svc['environment'] = svc_env

        if _is_enabled(service_val.get('hostNetwork')):
            # Host networking and explicit published ports are mutually exclusive.
            svc['network_mode'] = 'host'
            svc.pop('ports', None)
        elif has_hostnetwork_ioc:
            # Ensure host.docker.internal resolves on Linux Docker engines too.
            svc['extra_hosts'] = ["host.docker.internal:host-gateway"]

        # --- Mount host-side service config if available ---
        if host_dir:
            svc_host_dir = os.path.join(host_dir, 'services', service)
            if os.path.isdir(svc_host_dir):
                dest = os.path.join(output_dir, "services", service)
                copy_directory(svc_host_dir, dest)
                # Write a service init.yaml for reference
                write_file(os.path.join(dest, "init"),
                           yaml.dump(service_val, default_flow_style=False), "init.yaml")
                render_j2_files(dest, service_val)
                svc['volumes'] = [f"./services/{service}:/mnt"]

        docker_compose['services'][service] = svc
        print(f"* added service {service}")

    # ---- Process IOCs ----
    for ioc in epics_config.get('iocs', []):
        ioc_name = ioc['name']
        if selected_services and ioc_name not in selected_services:
            continue
        if ioc_name in exclude_services:
            print(f"%% ioc {ioc_name} excluded")
            continue

        ioc_net = ioc_runtime.get(ioc_name, {'hostnetwork': False})

        image = ioc.get('image', DEFAULT_IOC_IMAGE)
        svc = {
            'image': image,
            'tty': True,
            'stdin_open': True,
        }
        if platform:
            svc['platform'] = platform

        # Build the IOC config that the container will consume
        ioc_cfg = copy.deepcopy(ioc)
        # Unroll iocparam list into top-level keys (same as epik8s-run)
        if 'iocparam' in ioc_cfg:
            for p in ioc_cfg['iocparam']:
                ioc_cfg[p['name']] = p['value']
            del ioc_cfg['iocparam']
        ioc_cfg['iocname'] = ioc_name

        # Write per-IOC config directory
        ioc_dir = os.path.join(output_dir, "iocs", ioc_name)
        config_content = yaml.dump(ioc_cfg, default_flow_style=False)
        write_file(ioc_dir, config_content, "config.yaml")

        # Also write the full beamline YAML so epik8s-run can resolve defaults
        beamline_yaml = os.path.join(output_dir, "iocs", ioc_name, "beamline.yaml")
        with open(beamline_yaml, 'w') as f:
            yaml.dump(config, f, default_flow_style=False)

        # Container volumes: mount per-IOC config + a shared workdir
        svc['volumes'] = [
            f"./iocs/{ioc_name}/beamline.yaml:/tmp/epik8s-config.yaml:ro",
            "./iocs:/workdir",
        ]

        # The container runs epik8s-run in native mode — images already have
        # ibek-templates, ibek-support, jnjrender installed.
        svc['command'] = [
            "epik8s-run", "/tmp/epik8s-config.yaml", ioc_name,
            "--native", "--workdir", "/workdir",
        ]

        # Environment
        if env_content and not ioc_net.get('hostnetwork'):
            svc['env_file'] = ["epics.env"]
        if 'env' in ioc:
            svc_env = {}
            for e in ioc['env']:
                svc_env[e['name']] = str(e['value'])
            svc['environment'] = svc_env

        if ioc_net.get('hostnetwork'):
            # Host networking and explicit published ports are mutually exclusive.
            svc['network_mode'] = 'host'
            svc.pop('ports', None)
            svc_env = svc.setdefault('environment', {})
            svc_env.setdefault('EPICS_CAS_SERVER_PORT', str(ioc_net['ca_server_port']))
            svc_env.setdefault('EPICS_CAS_BEACON_PORT', str(ioc_net['ca_beacon_port']))
            if ioc.get('pva'):
                svc_env.setdefault('EPICS_PVAS_SERVER_PORT', str(ioc_net['pva_server_port']))
        elif has_hostnetwork_ioc:
            # Ensure host.docker.internal resolves on Linux Docker engines too.
            svc['extra_hosts'] = ["host.docker.internal:host-gateway"]

        # Copy host-side IOC config if available
        if host_dir:
            ioc_host_dir = os.path.join(host_dir, 'iocs', ioc.get('iocdir', ioc_name))
            if os.path.isdir(ioc_host_dir):
                dest = os.path.join(ioc_dir, "config")
                copy_directory(ioc_host_dir, dest)
                render_j2_files(dest, ioc_cfg)
                svc['volumes'].append(f"./iocs/{ioc_name}/config:/epics/ioc/config")

        docker_compose['services'][ioc_name] = svc
        print(f"* added ioc {ioc_name}")

    # ---- Write shared files ----
    if env_content:
        env_content += "export EPICS_CA_AUTO_ADDR_LIST=NO\n"
        write_file(output_dir, env_content, "epics.env")
        print(f"* wrote {output_dir}/epics.env")

    if env_host_content:
        env_host_content += "export EPICS_CA_AUTO_ADDR_LIST=NO\n"
        write_file(output_dir, env_host_content, "epics-channel.env")
        print(f"* wrote {output_dir}/epics-channel.env  (source this on the host to reach the beamline)")
    else:
        print("%% no host environment file generated (no gateway with loadbalancer)")

    return docker_compose


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main_compose():
    parser = argparse.ArgumentParser(
        description="Generate a ready-to-use docker-compose directory from an EPIK8S beamline YAML.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument('--config', help="Path to the beamline configuration file (YAML).")
    parser.add_argument('--host-dir', default=None, help="Base directory with host-side service/IOC configs to mount.")
    parser.add_argument('--output', help="Output directory (default: <beamline>-compose).")
    parser.add_argument('--services', nargs='+', help="Only include these services/IOCs (default: all).")
    parser.add_argument('--exclude', nargs='+', help="Exclude these services/IOCs.")
    parser.add_argument('--platform', default="linux/amd64", help="Docker platform for all containers.")

    parser.add_argument('--caport', type=int, default=5064, help="Starting CA port to map on host.")
    parser.add_argument('--pvaport', type=int, default=5075, help="Starting PVA port to map on host.")
    parser.add_argument('--htmlport', type=int, default=8090, help="Starting HTTP/ingress port on host.")

    parser.add_argument("--version", action="store_true", help="Show the version and exit.")

    args = parser.parse_args()
    if args.version:
        print(f"epik8s-compose version {__version__}")
        exit(0)

    if not args.config:
        parser.error("the following arguments are required: --config")

    config = parse_config(args.config)
    apply_ioc_defaults(config)

    output_dir = args.output
    if output_dir is None and 'beamline' in config:
        output_dir = f"{config['beamline']}-compose"
    if output_dir is None:
        output_dir = "compose-output"
    os.makedirs(output_dir, exist_ok=True)
    args.output_dir = output_dir

    # Resolve host-dir to an absolute path if provided
    if args.host_dir:
        args.host_dir = os.path.abspath(args.host_dir)

    print(f"* output directory: {output_dir}")

    docker_compose = generate_docker_compose(
        config, args,
        caport=args.caport,
        pvaport=args.pvaport,
        ingressport=args.htmlport,
    )

    dcf = os.path.join(output_dir, 'docker-compose.yaml')
    with open(dcf, 'w') as f:
        yaml.dump(docker_compose, f, default_flow_style=False)

    print(f"* docker-compose file: {dcf}")
    print(f"\nTo start the beamline:\n  cd {output_dir} && docker compose up")


if __name__ == "__main__":
    main_compose()
