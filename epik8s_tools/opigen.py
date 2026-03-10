import yaml
import argparse
import subprocess
import os
import shutil
from epik8s_tools.epik8s_gen import render_template, create_values_yaml
from epik8s_tools.epik8s_common import apply_ioc_defaults

from phoebusgen import screen as screen
from phoebusgen import widget as widget
from epik8s_tools import __version__

# Devgroups with YAML-driven array widgets in epik8s-opi.
# These use YAMLLoadDevicePopulateArray.py at runtime to dynamically
# populate the device list from values.yaml.
DEVGROUP_WIDGET = {
    'mag':  {'widget_dir': 'unimag-opi',  'main_bob': 'mag.bob'},
    'vac':  {'widget_dir': 'univac-opi',  'main_bob': 'vac_array.bob'},
    'mot':  {'widget_dir': 'unimot-opi',  'main_bob': 'mot_array.bob'},
    'io':   {'widget_dir': 'uniio-opi',   'main_bob': 'io_array.bob'},
    'cool': {'widget_dir': 'unicool-opi', 'main_bob': 'cool_array.bob'},
}

# Devgroup -> epik8s-opi widget directory for per-device OPI lookup
DEVGROUP_OPI_DIR = {
    'mag': 'unimag-opi', 'vac': 'univac-opi', 'mot': 'unimot-opi',
    'io': 'uniio-opi', 'cool': 'unicool-opi', 'cam': 'unicam-opi',
    'rf': 'unimod-opi', 'modulator': 'unimod-opi',
    'diag': 'tektronix-opi',
}

# Human-readable tab labels
DEVGROUP_LABELS = {
    'mag': 'Magnets', 'vac': 'Vacuum', 'mot': 'Motors', 'io': 'IO',
    'cool': 'Cooling', 'cam': 'Cameras', 'diag': 'Diagnostics',
    'rf': 'RF', 'modulator': 'Modulators', 'timing': 'Timing',
}

DEFAULT_EPIK8S_OPI_URL = 'https://baltig.infn.it/epics-containers/epik8s-opi.git'


def _resolve_opi_path(devgroup, opi_value, epik8s_opi_path):
    """Resolve an OPI field value to a relative path within epik8s-opi."""
    # Try devgroup-specific directory first
    opi_dir = DEVGROUP_OPI_DIR.get(devgroup)
    if opi_dir:
        if os.path.exists(os.path.join(epik8s_opi_path, opi_dir, opi_value)):
            return f"epik8s-opi/{opi_dir}/{opi_value}"

    # Try all known widget directories
    for d in set(DEVGROUP_OPI_DIR.values()):
        if os.path.exists(os.path.join(epik8s_opi_path, d, opi_value)):
            return f"epik8s-opi/{d}/{opi_value}"

    # Try directly in epik8s-opi root
    if os.path.exists(os.path.join(epik8s_opi_path, opi_value)):
        return f"epik8s-opi/{opi_value}"

    return None


def build_standard_group(devgroup, zones, width, height):
    """Build a Group widget with zone NavigationTabs for a standard devgroup.

    Uses the YAML-driven array display pattern where devices are populated
    at runtime from the values.yaml via YAMLLoadDevicePopulateArray.py.
    """
    wmap = DEVGROUP_WIDGET[devgroup]
    # CONFFILE relative to the display that runs the script
    # (e.g. epik8s-opi/{widget_dir}/{group}_display.bob -> ../../values.yaml)
    conffile = '../../values.yaml'
    bob_path = f"epik8s-opi/{wmap['widget_dir']}/{wmap['main_bob']}"

    grp = widget.Group(f"uni{devgroup}", 0, 0, width, height)
    grp.macro("CONFFILE", conffile)
    grp.macro("GROUP", devgroup)

    zone_list = ['ALL'] + sorted(zones - {'ALL'})
    nav = widget.NavigationTabs(f"zone-{devgroup}", 0, 0, width, height)
    nav.tab_direction_horizontal()
    for zone in zone_list:
        nav.tab(zone, bob_path, "", {"ZONE": zone})
    grp.add_widget(nav)
    return grp


def build_device_navtabs(devgroup, iocs, width, height, epik8s_opi_path):
    """Build NavigationTabs with per-device OPI for non-standard devgroups."""
    nav = widget.NavigationTabs(f"nav-{devgroup}", 0, 0, width, height)
    nav.tab_direction_horizontal()
    count = 0

    for ioc in iocs:
        iocprefix = ioc.get('iocprefix', '')
        iocroot = ioc.get('iocroot', '')
        opi_field = ioc.get('opi', '')

        devices = ioc.get('devices', [])
        if not devices:
            devices = [{'name': ioc.get('name', '')}]

        for dev in devices:
            devname = dev.get('name', '')
            if iocroot:
                full_root = f"{iocroot}:{devname}" if devname else iocroot
            else:
                full_root = devname

            dev_opi = dev.get('opi', opi_field)
            if not dev_opi:
                continue

            bob_path = _resolve_opi_path(devgroup, dev_opi, epik8s_opi_path)
            if not bob_path:
                continue

            macros = {
                "P": iocprefix, "R": full_root, "NAME": devname,
                "DEVICE": iocprefix, "CAM": full_root,
            }
            tab_name = devname if devname else ioc.get('name', '')
            nav.tab(tab_name, bob_path, "", macros)
            count += 1

    return nav if count > 0 else None


def main_opigen():
    script_dir = os.path.dirname(os.path.realpath(__file__)) + "/template/"

    parser = argparse.ArgumentParser(
        description="Generate a Phoebus display from an EPIK8s YAML configuration using epik8s-opi widgets.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--yaml", type=str,
                        help="Path to the EPIK8s YAML configuration file.")
    parser.add_argument("--version", action="store_true",
                        help="Show the version and exit")
    parser.add_argument("--output", type=str, default="Launcher.bob",
                        help="Main opi name")
    parser.add_argument("--title", type=str, default="Test Launcher",
                        help="Title for the launcher")
    parser.add_argument("--projectdir", type=str,
                        help="Directory where all project files will be generated")
    parser.add_argument("--width", type=int, default=1900,
                        help="Width of the launcher screen")
    parser.add_argument("--height", type=int, default=1400,
                        help="Height of the launcher screen")
    parser.add_argument('--controls', nargs='+',
                        help="Include just the given controls (default ALL).")
    parser.add_argument("--epik8s-opi-url", type=str, default=DEFAULT_EPIK8S_OPI_URL,
                        help="Git URL for epik8s-opi widget library")
    parser.add_argument("--epik8s-opi-branch", type=str, default="main",
                        help="Branch of epik8s-opi to clone")
    parser.add_argument("--epik8s-opi-dir", type=str, default=None,
                        help="Path to existing epik8s-opi directory (skip cloning)")

    args = parser.parse_args()
    if args.version:
        print(f"epik8s-tools version {__version__}")
        return

    if not args.yaml:
        print("# must define a valid epik8s configuration yaml --yaml <configuration>")
        return -1

    if not args.projectdir:
        print("# must define an output projectdir --projectdir <project output directory>")
        return -2

    project_dir = os.path.abspath(args.projectdir)
    os.makedirs(project_dir, exist_ok=True)

    # Load YAML configuration
    with open(args.yaml, 'r') as f:
        conf = yaml.safe_load(f)
    apply_ioc_defaults(conf)

    if 'epicsConfiguration' not in conf:
        print("## epicsConfiguration not present in configuration")
        return
    if 'iocs' not in conf['epicsConfiguration']:
        print("%% iocs not present in configuration")
        return

    iocs = conf['epicsConfiguration']['iocs']

    # Setup epik8s-opi widget library
    epik8s_opi_path = os.path.join(project_dir, 'epik8s-opi')
    if args.epik8s_opi_dir:
        src = os.path.abspath(args.epik8s_opi_dir)
        if not os.path.exists(epik8s_opi_path):
            os.symlink(src, epik8s_opi_path)
            print(f"Linked epik8s-opi from {src}")
    elif not os.path.exists(epik8s_opi_path):
        print(f"Cloning epik8s-opi from {args.epik8s_opi_url} (branch {args.epik8s_opi_branch})")
        subprocess.run(["git", "clone", "--depth", "1", "-b", args.epik8s_opi_branch,
                         "--recurse-submodules", args.epik8s_opi_url, epik8s_opi_path])
        git_dir = os.path.join(epik8s_opi_path, ".git")
        if os.path.exists(git_dir):
            shutil.rmtree(git_dir)

    # Copy values.yaml to project dir for runtime use by epik8s-opi scripts
    values_dest = os.path.join(project_dir, 'values.yaml')
    shutil.copy2(args.yaml, values_dest)
    print(f"Copied {args.yaml} to {values_dest}")

    # Collect devgroups with their zones and IOCs
    devgroups = {}
    for ioc in iocs:
        if args.controls and ioc.get('name') not in args.controls:
            continue
        dg = ioc.get('devgroup', '')
        if not dg:
            continue
        if dg not in devgroups:
            devgroups[dg] = {'zones': set(), 'iocs': []}
        zones = ioc.get('zones', 'ALL')
        if isinstance(zones, str):
            devgroups[dg]['zones'].add(zones)
        elif isinstance(zones, list):
            devgroups[dg]['zones'].update(zones)
        devgroups[dg]['iocs'].append(ioc)

    # Create Launcher screen
    launcher_screen = screen.Screen(args.title, os.path.join(project_dir, args.output))
    launcher_screen.width(args.width)
    launcher_screen.height(args.height)

    group_tabs = widget.Tabs("groups", 0, 0, args.width, args.height)

    for devgroup in sorted(devgroups.keys()):
        info = devgroups[devgroup]
        label = DEVGROUP_LABELS.get(devgroup, devgroup.title())

        if devgroup in DEVGROUP_WIDGET:
            # YAML-driven array widget: devices populated at runtime from values.yaml
            group_tabs.tab(label)
            grp = build_standard_group(devgroup, info['zones'],
                                       args.width, args.height - 50)
            group_tabs.add_widget(label, grp)
            print(f"+ {label} [{devgroup}]: YAML-driven, "
                  f"zones: {sorted(info['zones'])}, {len(info['iocs'])} IOCs")
        else:
            # Per-device NavigationTabs using device opi field resolved in epik8s-opi
            nav = build_device_navtabs(devgroup, info['iocs'],
                                       args.width, args.height - 50,
                                       epik8s_opi_path)
            if nav:
                group_tabs.tab(label)
                group_tabs.add_widget(label, nav)
                print(f"+ {label} [{devgroup}]: per-device, {len(info['iocs'])} IOCs")
            else:
                print(f"! {label} [{devgroup}]: no matching OPI in epik8s-opi, skipped")

    launcher_screen.add_widget(group_tabs)
    launcher_screen.write_screen()
    print(f"\nGenerated {os.path.join(project_dir, args.output)} titled '{args.title}'")

    # Generate settings.ini
    services = conf.get('epicsConfiguration', {}).get('services', {})
    ca_gw = services.get('gateway', {})
    pva_gw = services.get('pvagateway', {})

    replacements = {
        "beamline": conf.get('beamline', ''),
        "namespace": conf.get('namespace', ''),
        "dnsnamespace": conf.get('epik8namespace', ''),
        "cagatewayip": ca_gw.get('loadbalancer', '') if isinstance(ca_gw, dict) else '',
        "pvagatewayip": pva_gw.get('loadbalancer', '') if isinstance(pva_gw, dict) else '',
    }
    rendered_settings = render_template(script_dir + 'settings.ini', replacements)
    create_values_yaml('settings.ini', rendered_settings, f'{project_dir}/')
    print(f"Generated settings.ini in {project_dir}")
