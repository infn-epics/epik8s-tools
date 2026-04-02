import os
import sys
import argparse
import shutil
import copy
import socket

from jinja2 import Template
from epik8s_tools import __version__
import yaml
from .epik8s_common import apply_ioc_defaults

# Default images for well-known services
DEFAULT_SERVICE_IMAGES = {
    "gateway": "ghcr.io/infn-epics/docker-ca-gateway:latest",
    "pvagateway": "ghcr.io/infn-epics/docker-pva-gateway:latest",
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
DEFAULT_PRIVATE_NETWORK_NAME = "epik8s-private"

SETTINGS_URL_PATHS = {
    'archiver': [
        ('org.csstudio.trends.databrowser3/urls', 'pbraw://{host}:{port}/retrieval'),
        ('org.csstudio.trends.databrowser3/archives', 'pbraw://{host}:{port}/retrieval'),
    ],
    'console': [
        ('org.phoebus.olog.es.api/olog_url', 'http://{host}:{port}/Olog'),
        ('org.phoebus.olog.api/olog_url', 'http://{host}:{port}/Olog'),
        ('org.phoebus.logbook/logbook_factory', 'olog-es'),
    ],
    'channelfinder': [
        ('org.phoebus.channelfinder/serviceURL', 'http://{host}:{port}/ChannelFinder'),
        ('org.phoebus.channelfinder/rawFiltering', 'false'),
    ],
    'saveandrestore': [
        ('org.phoebus.applications.saveandrestore.client/jmasar.service.url', 'http://{host}:{port}/save-restore'),
    ],
    'scanserver': [
        ('org.csstudio.scan.client/host', '{host}'),
        ('org.csstudio.scan.client/port', '{port}'),
    ],
    'alarmlogger': [
        ('org.phoebus.applications.alarm.logging.ui/service_uri', 'http://{host}:{port}'),
        ('org.phoebus.applications.alarm.logging.ui/results_max_size', '10000'),
    ],
    'alarmserver': [
        ('org.phoebus.applications.alarm/server', '{host}:{port}'),
    ],
}


def parse_config(file_path):
    with open(file_path, 'r') as file:
        return yaml.safe_load(file)


def write_file(directory, content, fname):
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, fname)
    with open(path, 'w') as f:
        f.write(content)
    return path


def _package_template_dir(*parts):
    return os.path.join(os.path.dirname(os.path.realpath(__file__)), 'template', *parts)


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


def _resolve_service_template_dir(service_name, host_dir):
    """Return the service template dir from host_dir or built-in package templates."""
    if host_dir:
        svc_host_dir = os.path.join(host_dir, 'services', service_name)
        if os.path.isdir(svc_host_dir):
            return svc_host_dir

    builtin_dir = _package_template_dir('services', service_name)
    if os.path.isdir(builtin_dir):
        return builtin_dir

    return None


def _resolve_ioc_host_dir(host_dir, ioc_subdir):
    """Return a host-side IOC config dir from either host_dir/iocs/<name> or host_dir/<name>."""
    if not host_dir:
        return None

    candidates = [
        os.path.join(host_dir, 'iocs', ioc_subdir),
        os.path.join(host_dir, ioc_subdir),
    ]
    for candidate in candidates:
        if os.path.isdir(candidate):
            return candidate
    return None


def _inject_uppercase_ioc_env(environment, ioc_cfg):
    """Expose uppercase scalar IOC config keys as environment variables.

    This lets custom host-side start scripts consume values originating from
    beamline iocparam entries such as MOTX, MOTY, or CAM.
    """
    for key, value in ioc_cfg.items():
        if not isinstance(key, str) or not key.isupper():
            continue
        if isinstance(value, (str, int, float, bool)):
            environment.setdefault(key, str(value))


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


def _ioc_supports_pva(ioc):
    """Return True when an IOC should be advertised through PVA discovery."""
    if 'pvaDiscovery' in ioc:
        return _is_enabled(ioc.get('pvaDiscovery'))

    if _is_enabled(ioc.get('pva')):
        return True

    devtype = str(ioc.get('devtype', '')).strip().lower()
    template = str(ioc.get('template', '')).strip().lower()
    return devtype == 'softioc' or template == 'softioc'


def _private_network_info(config):
    """Return compose private-network settings from epicsConfiguration.privateNetwork."""
    raw = config.get('epicsConfiguration', {}).get('privateNetwork')
    if raw is None:
        return {'enabled': False, 'name': DEFAULT_PRIVATE_NETWORK_NAME, 'internal': False}

    if isinstance(raw, dict):
        enabled = _is_enabled(raw.get('enabled', True))
        name = str(raw.get('name') or DEFAULT_PRIVATE_NETWORK_NAME)
        internal = _is_enabled(raw.get('internal', False))
    else:
        enabled = _is_enabled(raw)
        name = DEFAULT_PRIVATE_NETWORK_NAME
        internal = False

    return {'enabled': enabled, 'name': name, 'internal': internal}


def _port_is_free(port, proto):
    family = socket.SOCK_STREAM if proto == 'tcp' else socket.SOCK_DGRAM
    sock = socket.socket(socket.AF_INET, family)
    try:
        sock.bind(('0.0.0.0', port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def _port_conflicts(port, protocols, reserved):
    conflicts = []
    for proto in protocols:
        key = (port, proto)
        if key in reserved:
            conflicts.append(f"{proto} already reserved in this compose plan")
        elif not _port_is_free(port, proto):
            conflicts.append(f"{proto} already in use on this host")
    return conflicts


def _find_free_port(start_port, protocols, reserved):
    port = max(1, int(start_port))
    while True:
        if not _port_conflicts(port, protocols, reserved):
            return port
        port += 1


def _should_prompt(args):
    return args.port_conflicts == 'ask' and sys.stdin.isatty() and sys.stdout.isatty()


def _resolve_port(port, protocols, description, args, reserved):
    conflicts = _port_conflicts(port, protocols, reserved)
    if not conflicts:
        for proto in protocols:
            reserved.add((port, proto))
        return port

    suggestion = _find_free_port(port + 1, protocols, reserved)
    reason = '; '.join(conflicts)

    if args.port_conflicts == 'proceed':
        print(f"! port conflict for {description}: {port}/{','.join(protocols)} -> {reason}; proceeding anyway")
        for proto in protocols:
            reserved.add((port, proto))
        return port

    if args.port_conflicts == 'free':
        print(f"! port conflict for {description}: {port}/{','.join(protocols)} -> {reason}; using free port {suggestion}")
        for proto in protocols:
            reserved.add((suggestion, proto))
        return suggestion

    if args.port_conflicts == 'abort' or not _should_prompt(args):
        raise SystemExit(
            f"Port conflict for {description}: {port}/{','.join(protocols)} ({reason}). "
            f"Suggested free port: {suggestion}. "
            f"Use --port-conflicts free|proceed to continue non-interactively."
        )

    while True:
        try:
            answer = input(
                f"Port conflict for {description}: {port}/{','.join(protocols)} ({reason}). "
                f"[p]roceed anyway, use [f]ree port {suggestion}, or [a]bort? "
            ).strip().lower()
        except KeyboardInterrupt:
            print()
            raise SystemExit("Aborted by user")
        except EOFError:
            raise SystemExit("Aborted: no input available for interactive port prompt")
        if answer in {'p', 'proceed'}:
            for proto in protocols:
                reserved.add((port, proto))
            return port
        if answer in {'f', 'free', ''}:
            for proto in protocols:
                reserved.add((suggestion, proto))
            return suggestion
        if answer in {'a', 'abort'}:
            raise SystemExit(f"Aborted because port {port} for {description} is already taken")


def _format_port_protocols(protocols):
    return '/'.join(protocols)


def _add_published_port(report, service, host_port, container_port, protocols, purpose, url_path=''):
    report['published_ports'].append({
        'service': service,
        'host_port': host_port,
        'container_port': container_port,
        'protocols': list(protocols),
        'purpose': purpose,
        'url_path': url_path,
    })


def _add_hostnetwork_port(report, service, host_port, protocols, purpose):
    report['hostnetwork_ports'].append({
        'service': service,
        'host_port': host_port,
        'protocols': list(protocols),
        'purpose': purpose,
    })


def _build_port_summary(report, bind_host):
    lines = []
    lines.append("Published ports:")
    if report['published_ports']:
        for entry in report['published_ports']:
            extra = ''
            if entry['url_path']:
                extra = f" url=http://{bind_host}:{entry['host_port']}{entry['url_path']}"
            lines.append(
                f"  - {entry['service']}: {bind_host}:{entry['host_port']} -> "
                f"container:{entry['container_port']} ({_format_port_protocols(entry['protocols'])}) "
                f"[{entry['purpose']}]" + extra
            )
    else:
        lines.append("  - none")

    lines.append("")
    lines.append("Host-network IOC ports:")
    if report['hostnetwork_ports']:
        for entry in report['hostnetwork_ports']:
            lines.append(
                f"  - {entry['service']}: {bind_host}:{entry['host_port']} "
                f"({_format_port_protocols(entry['protocols'])}) [{entry['purpose']}]"
            )
    else:
        lines.append("  - none")

    return '\n'.join(lines) + '\n'


def _write_settings_ini(output_dir, report, bind_host):
    lines = []
    lines.append(f"org.phoebus.pv.ca/addr_list={report['settings']['ca_addr_list']}")
    lines.append("org.phoebus.pv.ca/auto_addr_list=false")
    if report['settings'].get('ca_server_port'):
        lines.append(f"org.phoebus.pv.ca/server_port={report['settings']['ca_server_port']}")
    if report['settings'].get('ca_repeater_port'):
        lines.append(f"org.phoebus.pv.ca/repeater_port={report['settings']['ca_repeater_port']}")

    lines.append(f"org.phoebus.pv.pva/epics_pva_name_servers={report['settings']['pva_name_servers']}")
    lines.append("org.phoebus.pv.pva/epics_pva_auto_addr_list=false")
    lines.append("org.phoebus.pv/default=pva")

    lines.append("")

    for service, values in report['settings']['service_urls'].items():
        for key, value in values:
            lines.append(f"{key}={value.format(host=bind_host, port=report['service_ports'][service])}")
        lines.append("")

    path = write_file(output_dir, '\n'.join(lines).rstrip() + '\n', 'settings.ini')
    print(f"* wrote {path}")
    return path


def _collect_pva_examples(config):
    """Collect configured motor and camera PVs for generated Python examples."""
    motors = []
    cameras = []

    for ioc in config.get('epicsConfiguration', {}).get('iocs', []):
        iocprefix = str(ioc.get('iocprefix', '')).strip(':')
        devgroup = str(ioc.get('devgroup', '')).strip().lower()
        devtype = str(ioc.get('devtype', '')).strip().lower()
        template = str(ioc.get('template', '')).strip().lower()

        for device in ioc.get('devices', []) or []:
            device_name = str(device.get('name', '')).strip()
            if not iocprefix or not device_name:
                continue

            base_pv = f"{iocprefix}:{device_name}"
            label = f"{ioc.get('name', 'ioc')}/{device_name}"

            if devgroup == 'mot' or template == 'motor' or devtype.endswith('motor') or devtype == 'motorsim':
                motors.append({
                    'label': label,
                    'readback_pv': f"{base_pv}.RBV",
                    'setpoint_pv': f"{base_pv}.VAL",
                })

            if devgroup == 'cam' or template.startswith('adcamera') or devtype in {'camera', 'camerasim'}:
                cameras.append({
                    'label': label,
                    'status_pv': f"{base_pv}:StatusMessage_RBV",
                    'peak_start_x_pv': f"{base_pv}:PeakStartX",
                    'peak_start_y_pv': f"{base_pv}:PeakStartY",
                })

    return {'motors': motors, 'cameras': cameras}


def _write_pva_python_examples(output_dir, config, report):
    """Generate host-side p4p examples for configured motors and cameras."""
    examples = _collect_pva_examples(config)
    pva_name_servers = report['settings'].get('pva_name_servers', 'localhost:5075')
    examples_dir = os.path.join(output_dir, 'examples', 'pva')

    common_py = f'''import os
import sys

try:
    from p4p.client.thread import Context
except ModuleNotFoundError as exc:
    raise SystemExit(
        "Missing Python dependency 'p4p' for interpreter: "
        f"{{sys.executable}}\n"
        "Install it with:\n"
        f"{{sys.executable}} -m pip install -r requirements.txt"
    ) from exc

PVA_NAME_SERVERS = {pva_name_servers!r}
LOCAL_BROADCAST_PORT = "16076"
MOTORS = {repr(examples['motors'])}
CAMERAS = {repr(examples['cameras'])}


def build_context():
    os.environ.setdefault("EPICS_PVA_NAME_SERVERS", PVA_NAME_SERVERS)
    os.environ.setdefault("EPICS_PVA_AUTO_ADDR_LIST", "NO")
    os.environ.setdefault("EPICS_PVA_BROADCAST_PORT", LOCAL_BROADCAST_PORT)
    return Context("pva")


def unwrap(value):
    return getattr(value, "value", value)
'''

    read_script = '''from common import CAMERAS, MOTORS, build_context, unwrap


def main():
    ctx = build_context()

    print("Motors")
    for motor in MOTORS:
        readback = unwrap(ctx.get(motor["readback_pv"], timeout=5.0))
        print(f"- {motor['label']}: {motor['readback_pv']} = {readback}")

    print("\\nCameras")
    for camera in CAMERAS:
        status = unwrap(ctx.get(camera["status_pv"], timeout=5.0))
        peak_x = unwrap(ctx.get(camera["peak_start_x_pv"], timeout=5.0))
        peak_y = unwrap(ctx.get(camera["peak_start_y_pv"], timeout=5.0))
        print(f"- {camera['label']}: {camera['status_pv']} = {status}")
        print(f"  {camera['peak_start_x_pv']} = {peak_x}")
        print(f"  {camera['peak_start_y_pv']} = {peak_y}")


if __name__ == "__main__":
    main()
'''

    move_script = '''import time

from common import CAMERAS, MOTORS, build_context, unwrap


def main():
    if not MOTORS:
        raise SystemExit("No configured motors found")

    motor = MOTORS[0]
    camera = CAMERAS[0] if CAMERAS else None
    ctx = build_context()

    before = unwrap(ctx.get(motor["readback_pv"], timeout=5.0))
    print(f"Before move: {motor['readback_pv']} = {before}")

    if camera:
        peak_before = unwrap(ctx.get(camera["peak_start_x_pv"], timeout=5.0))
        print(f"Before move: {camera['peak_start_x_pv']} = {peak_before}")

    target = float(before) + 1.0
    ctx.put(motor["setpoint_pv"], target, wait=False, timeout=5.0)
    time.sleep(1.5)

    after = unwrap(ctx.get(motor["readback_pv"], timeout=5.0))
    print(f"After move: {motor['readback_pv']} = {after}")

    if camera:
        peak_after = unwrap(ctx.get(camera["peak_start_x_pv"], timeout=5.0))
        print(f"After move: {camera['peak_start_x_pv']} = {peak_after}")


if __name__ == "__main__":
    main()
'''

    readme = f'''# Python PVA Examples

These examples use `p4p` to access the configured beamline through the generated PVA gateway.

Defaults:
- `EPICS_PVA_NAME_SERVERS={pva_name_servers}`
- `EPICS_PVA_AUTO_ADDR_LIST=NO`
- `EPICS_PVA_BROADCAST_PORT=16076`

Example commands:

```sh
python -m pip install -r requirements.txt
python read_configured_pvs.py
python move_first_motor.py
```

Check which interpreter you are using:

```sh
python -c "import sys; print(sys.executable)"
```

If you already have `p4p` in another environment, run the scripts with that interpreter instead.
- {', '.join(item['label'] for item in examples['motors']) or 'none'}


If you already have `p4p` in another environment, run the scripts with that interpreter instead.
Configured cameras:
- {', '.join(item['label'] for item in examples['cameras']) or 'none'}
'''

    write_file(examples_dir, common_py, 'common.py')
    write_file(examples_dir, read_script, 'read_configured_pvs.py')
    write_file(examples_dir, move_script, 'move_first_motor.py')
    write_file(examples_dir, 'p4p\n', 'requirements.txt')
    readme_path = write_file(examples_dir, readme, 'README.md')
    print(f"* wrote {readme_path}")
    return examples_dir


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
    private_network = _private_network_info(config)
    if private_network['enabled']:
        docker_compose['networks'] = {
            private_network['name']: {
                'driver': 'bridge',
                'internal': private_network['internal'],
            }
        }
    reserved_ports = set()
    report = {
        'published_ports': [],
        'hostnetwork_ports': [],
        'settings': {
            'ca_addr_list': 'localhost',
            'pva_name_servers': 'localhost',
            'ca_server_port': None,
            'ca_repeater_port': None,
            'service_urls': {},
        },
        'service_ports': {},
    }

    # ---- Collect IOC names/targets for env vars and startup ordering ----
    ioc_service_names = []
    ioc_runtime = {}
    epics_ca_addr_list = []
    epics_pva_addr_list = []
    host_visible_ca_targets = []
    host_visible_pva_targets = []
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
            info['ca_server_port'] = _resolve_port(
                info['ca_server_port'], ['tcp', 'udp'],
                f"host-network IOC CA server for {name}", args, reserved_ports
            )
            info['ca_beacon_port'] = _resolve_port(
                info['ca_beacon_port'], ['tcp', 'udp'],
                f"host-network IOC CA beacon for {name}", args, reserved_ports
            )
            _add_hostnetwork_port(report, name, info['ca_server_port'], ['tcp', 'udp'], 'IOC CA server')
            _add_hostnetwork_port(report, name, info['ca_beacon_port'], ['tcp', 'udp'], 'IOC CA beacon')
            if _ioc_supports_pva(ioc):
                info['pva_server_port'] = _resolve_port(
                    info['pva_server_port'], ['tcp', 'udp'],
                    f"host-network IOC PVA server for {name}", args, reserved_ports
                )
                _add_hostnetwork_port(report, name, info['pva_server_port'], ['tcp', 'udp'], 'IOC PVA server')

            host = _ioc_discovery_target(ioc)
            info['ca_target'] = f"{host}:{info['ca_server_port']}"
            info['pva_target'] = f"{host}:{info['pva_server_port']}" if _ioc_supports_pva(ioc) else str(name)
            host_visible_ca_targets.append(f"{args.bind_host}:{info['ca_server_port']}")
            if _ioc_supports_pva(ioc):
                host_visible_pva_targets.append(f"{args.bind_host}:{info['pva_server_port']}")
            hostnetwork_ioc_index += 1

        ioc_runtime[name] = info
        ioc_service_names.append(name)
        epics_ca_addr_list.append(info['ca_target'])
        has_hostnetwork_ioc = has_hostnetwork_ioc or info['hostnetwork']
        if _ioc_supports_pva(ioc):
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
                host_ca_port = _resolve_port(
                    caport, ['tcp', 'udp'], f"gateway CA server for {service}", args, reserved_ports
                )
                host_beacon_port = _resolve_port(
                    caport + 1, ['tcp', 'udp'], f"gateway CA repeater for {service}", args, reserved_ports
                )
                svc['ports'] = [
                    f"{host_ca_port}:5064/tcp", f"{host_ca_port}:5064/udp",
                    f"{host_beacon_port}:5065/tcp", f"{host_beacon_port}:5065/udp",
                ]
                svc['depends_on'] = {n: {"condition": "service_started"} for n in ioc_service_names}
                env_host_content += f"export EPICS_CA_ADDR_LIST={args.bind_host}:{host_ca_port}\n"
                env_host_content += f"export EPICS_CA_SERVER_PORT={host_ca_port}\n"
                env_host_content += f"export EPICS_CA_REPEATER_PORT={host_beacon_port}\n"
                _add_published_port(report, service, host_ca_port, 5064, ['tcp', 'udp'], 'CA gateway server')
                _add_published_port(report, service, host_beacon_port, 5065, ['tcp', 'udp'], 'CA repeater/beacon')
                report['settings']['ca_addr_list'] = args.bind_host
                report['settings']['ca_server_port'] = host_ca_port
                report['settings']['ca_repeater_port'] = host_beacon_port
                caport = host_beacon_port + 1
            if service == "pvagateway":
                host_pva_port = _resolve_port(
                    pvaport, ['tcp'], f"PVA name server for {service}", args, reserved_ports
                )
                host_pva_bcast = _resolve_port(
                    pvaport + 1, ['udp'], f"PVA broadcast for {service}", args, reserved_ports
                )
                svc['ports'] = [
                    f"{host_pva_port}:5075/tcp", f"{host_pva_bcast}:5076/udp",
                ]
                svc['depends_on'] = {n: {"condition": "service_started"} for n in ioc_service_names}
                env_host_content += f"export EPICS_PVA_NAME_SERVERS={args.bind_host}:{host_pva_port}\n"
                env_host_content += "export EPICS_PVA_AUTO_ADDR_LIST=NO\n"
                _add_published_port(report, service, host_pva_port, 5075, ['tcp'], 'PVA name server')
                _add_published_port(report, service, host_pva_bcast, 5076, ['udp'], 'PVA broadcast')
                report['settings']['pva_name_servers'] = f"{args.bind_host}:{host_pva_port}"
                pvaport = host_pva_bcast + 1

        # --- Ingress ports (http services) ---
        if service_val.get('enable_ingress'):
            internal = SERVICE_INTERNAL_PORTS.get(service, 8080)
            host_ingress_port = _resolve_port(
                ingressport, ['tcp'], f"HTTP ingress for {service}", args, reserved_ports
            )
            svc.setdefault('ports', []).append(f"{host_ingress_port}:{internal}")
            _add_published_port(report, service, host_ingress_port, internal, ['tcp'], 'HTTP ingress', '/')
            report['service_ports'][service] = host_ingress_port
            if service in SETTINGS_URL_PATHS:
                report['settings']['service_urls'][service] = SETTINGS_URL_PATHS[service]
            print(f"  ingress {service} -> {args.bind_host}:{host_ingress_port}")
            ingressport = host_ingress_port + 1

        # --- Environment ---
        if env_content:
            svc['env_file'] = ["epics.env"]

        # --- Service-specific env vars ---
        if 'env' in service_val:
            svc_env = {}
            for e in service_val['env']:
                svc_env[e['name']] = str(e['value'])
            svc['environment'] = svc_env

        # --- Notebook-specific: token, pip, work volume ---
        if service == 'notebook':
            svc_env = svc.get('environment', {})
            svc_env['JUPYTER_TOKEN'] = ''
            svc['environment'] = svc_env

            pip_packages = service_val.get('pip', [])
            if pip_packages:
                pip_cmd = 'pip install --quiet ' + ' '.join(pip_packages) + ' && '
            else:
                pip_cmd = ''
            svc['entrypoint'] = [
                "/bin/sh", "-c",
                pip_cmd + "start-notebook.sh --NotebookApp.token='' --NotebookApp.password=''"
            ]

            work_dir = os.path.join(output_dir, "notebook-work")
            os.makedirs(work_dir, exist_ok=True)
            svc.setdefault('volumes', []).append("./notebook-work:/home/jovyan/work")
            print(f"  notebook: token disabled, work volume at {work_dir}")
            if pip_packages:
                print(f"  notebook: pip install {' '.join(pip_packages)}")

        service_hostnetwork = _is_enabled(service_val.get('hostNetwork'))
        if private_network['enabled'] and service in {'gateway', 'pvagateway'} and service_hostnetwork:
            print(
                f"%% service {service} hostNetwork ignored because privateNetwork is enabled; "
                "using bridge networking with published host ports instead"
            )
            service_hostnetwork = False

        if service_hostnetwork:
            # Host networking and explicit published ports are mutually exclusive.
            svc['network_mode'] = 'host'
            svc.pop('ports', None)
        elif has_hostnetwork_ioc:
            # Ensure host.docker.internal resolves on Linux Docker engines too.
            svc['extra_hosts'] = ["host.docker.internal:host-gateway"]

        if private_network['enabled'] and 'network_mode' not in svc:
            svc['networks'] = [private_network['name']]

        # --- Mount service config/scripts from host override or built-in templates ---
        svc_template_dir = _resolve_service_template_dir(service, host_dir)
        if svc_template_dir:
            dest = os.path.join(output_dir, "services", service)
            copy_directory(svc_template_dir, dest)
            write_file(os.path.join(dest, "init"),
                       yaml.dump(service_val, default_flow_style=False), "init.yaml")
            render_j2_files(dest, service_val)
            svc['volumes'] = [f"./services/{service}:/mnt"]

            # The gateway images need an explicit startup script to consume the mounted config.
            start_script = os.path.join(dest, 'start.sh')
            if os.path.isfile(start_script):
                svc['entrypoint'] = ["/bin/sh", "/mnt/start.sh"]

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
        _inject_uppercase_ioc_env(svc.setdefault('environment', {}), ioc_cfg)

        if ioc_net.get('hostnetwork'):
            # Host networking and explicit published ports are mutually exclusive.
            svc['network_mode'] = 'host'
            svc.pop('ports', None)
            svc_env = svc.setdefault('environment', {})
            svc_env.setdefault('EPICS_CAS_SERVER_PORT', str(ioc_net['ca_server_port']))
            svc_env.setdefault('EPICS_CAS_BEACON_PORT', str(ioc_net['ca_beacon_port']))
            if _ioc_supports_pva(ioc):
                svc_env.setdefault('EPICS_PVAS_SERVER_PORT', str(ioc_net['pva_server_port']))
        elif has_hostnetwork_ioc:
            # Ensure host.docker.internal resolves on Linux Docker engines too.
            svc['extra_hosts'] = ["host.docker.internal:host-gateway"]

        if private_network['enabled'] and not ioc_net.get('hostnetwork'):
            svc['networks'] = [private_network['name']]

        # Copy host-side IOC config if available
        ioc_host_dir = _resolve_ioc_host_dir(host_dir, ioc.get('iocdir', ioc_name))
        if ioc_host_dir:
            dest = os.path.join(ioc_dir, "config")
            copy_directory(ioc_host_dir, dest)
            render_j2_files(dest, ioc_cfg)
            svc['volumes'].append(f"./iocs/{ioc_name}/config:/epics/ioc/config")

            custom_start = os.path.join(dest, 'start.sh')
            if os.path.isfile(custom_start):
                svc['command'] = ["/bin/sh", "/epics/ioc/config/start.sh"]

        docker_compose['services'][ioc_name] = svc
        print(f"* added ioc {ioc_name}")

    # ---- Write shared files ----
    if env_content:
        env_content += "export EPICS_CA_AUTO_ADDR_LIST=NO\n"
        write_file(output_dir, env_content, "epics.env")
        print(f"* wrote {output_dir}/epics.env")

    if env_host_content:
        env_host_content += "export EPICS_CA_AUTO_ADDR_LIST=NO\n"
        if host_visible_pva_targets and 'EPICS_PVA_NAME_SERVERS' not in env_host_content:
            env_host_content += f"export EPICS_PVA_NAME_SERVERS={' '.join(host_visible_pva_targets)}\n"
        write_file(output_dir, env_host_content, "epics-channel.env")
        print(f"* wrote {output_dir}/epics-channel.env  (source this on the host to reach the beamline)")
    elif host_visible_ca_targets or host_visible_pva_targets:
        if host_visible_ca_targets:
            env_host_content += f"export EPICS_CA_ADDR_LIST={' '.join(host_visible_ca_targets)}\n"
        if host_visible_pva_targets:
            env_host_content += f"export EPICS_PVA_NAME_SERVERS={' '.join(host_visible_pva_targets)}\n"
        env_host_content += "export EPICS_CA_AUTO_ADDR_LIST=NO\n"
        write_file(output_dir, env_host_content, "epics-channel.env")
        print(f"* wrote {output_dir}/epics-channel.env  (host-network IOC access)")
    else:
        print("%% no host environment file generated (no gateway with loadbalancer)")

    if report['settings']['ca_server_port'] is None and host_visible_ca_targets:
        report['settings']['ca_addr_list'] = ' '.join(host_visible_ca_targets)
    if report['settings']['pva_name_servers'] == 'localhost' and host_visible_pva_targets:
        report['settings']['pva_name_servers'] = ' '.join(host_visible_pva_targets)

    return docker_compose, report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main_compose():
    parser = argparse.ArgumentParser(
        description="Generate a ready-to-use docker-compose directory from an EPIK8S beamline YAML.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument('--config', help="Path to the beamline configuration file (YAML).")
    parser.add_argument('--host-dir', default=None, help="Base directory with host-side service/IOC configs to mount. Defaults to the beamline config directory.")
    parser.add_argument('--output', help="Output directory (default: <beamline>-compose).")
    parser.add_argument('--services', nargs='+', help="Only include these services/IOCs (default: all).")
    parser.add_argument('--exclude', nargs='+', help="Exclude these services/IOCs.")
    parser.add_argument('--platform', default="linux/amd64", help="Docker platform for all containers.")

    parser.add_argument('--caport', type=int, default=5164, help="Starting CA port to map on host.")
    parser.add_argument('--pvaport', type=int, default=5175, help="Starting PVA port to map on host.")
    parser.add_argument('--htmlport', type=int, default=8090, help="Starting HTTP/ingress port on host.")
    parser.add_argument('--bind-host', default='localhost', help="Host/address to write in summaries and settings.ini.")
    parser.add_argument(
        '--port-conflicts',
        choices=['ask', 'free', 'proceed', 'abort'],
        default='ask',
        help="How to handle host port conflicts: ask interactively, move to a free port, proceed anyway, or abort.",
    )

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

    config_path = os.path.abspath(args.config)

    # Resolve host-dir to an absolute path if provided; otherwise infer it
    # from the beamline configuration file location so local IOC/service
    # directories such as ./simtwin are picked up automatically.
    if args.host_dir:
        args.host_dir = os.path.abspath(args.host_dir)
    else:
        args.host_dir = os.path.dirname(config_path)
        print(f"* inferred host-dir: {args.host_dir}")

    print(f"* output directory: {output_dir}")

    docker_compose, report = generate_docker_compose(
        config, args,
        caport=args.caport,
        pvaport=args.pvaport,
        ingressport=args.htmlport,
    )

    dcf = os.path.join(output_dir, 'docker-compose.yaml')
    with open(dcf, 'w') as f:
        yaml.dump(docker_compose, f, default_flow_style=False)

    summary = _build_port_summary(report, args.bind_host)
    summary_path = write_file(output_dir, summary, 'ports-summary.txt')
    print(f"* wrote {summary_path}")

    _write_settings_ini(output_dir, report, args.bind_host)
    _write_pva_python_examples(output_dir, config, report)

    print(f"* docker-compose file: {dcf}")
    print("\nPort summary:\n")
    print(summary.rstrip())
    print(f"\nTo start the beamline:\n  cd {output_dir} && docker compose up")


if __name__ == "__main__":
    main_compose()
