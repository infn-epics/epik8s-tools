import yaml
import argparse
import subprocess
import os
import shutil
import xml.etree.ElementTree as ET
from epik8s_tools.epik8s_gen import render_template, create_values_yaml
from epik8s_tools.epik8s_common import apply_ioc_defaults

from phoebusgen import screen as screen
from phoebusgen import widget as widget
from epik8s_tools import __version__

DEFAULT_EPIK8S_OPI_URL = 'https://github.com/infn-epics/epik8s-opi.git'
DEFAULT_DEVICE_ORDER = 'zone-order'

# ---------------------------------------------------------------------------
# Devgroup registry
# ---------------------------------------------------------------------------
# For each devgroup we define:
#   widget_dir  : subdirectory inside epik8s-opi
#   array_bob   : the YAML-driven array display (uses YAMLLoadDevicePopulateArray.py)
#   display_bob : the YAML-driven display with zone/type/func filtering
#   channel_bob : single-device row template
#   main_bob    : per-device detail display (used for NavigationTabs per device)
#   label       : human-readable tab label
#   mode        : 'yaml_array' -> runtime YAML population
#                 'per_device' -> one NavigationTabs entry per device
#                 'both'       -> YAML array for overview + per-device detail tabs
#
DEVGROUP_REGISTRY = {
    'mag': {
        'widget_dir': 'unimag-opi',
        'array_bob': 'mag_array.bob',
        'display_bob': 'mag_display.bob',
        'channel_bob': 'mag_channel.bob',
        'detail_bob': 'mag_channel.bob',
        'label': 'Magnets',
        'mode': 'yaml_array',
    },
    'mot': {
        'widget_dir': 'unimot-opi',
        'array_bob': 'mot_array.bob',
        'display_bob': 'mot_display.bob',
        'channel_bob': 'mot_channel_asyn.bob',
        'detail_bob': 'mot_channel.bob',
        'main_bob': 'motor-asyn/Motor_Main.bob',
        'label': 'Motors',
        'mode': 'both',
    },
    'vac': {
        'widget_dir': 'univac-opi',
        'array_bob': 'vac_array.bob',
        'display_bob': 'vac_display.bob',
        'channel_bob': 'vac_channel.bob',
        'detail_bob': 'vac_channel.bob',
        'label': 'Vacuum',
        'mode': 'yaml_array',
    },
    'io': {
        'widget_dir': 'uniio-opi',
        'array_bob': 'io_array.bob',
        'display_bob': 'io_display.bob',
        'channel_bob': 'io_channel.bob',
        'detail_bob': 'io_channel.bob',
        'label': 'IO',
        'mode': 'yaml_array',
    },
    'cool': {
        'widget_dir': 'unicool-opi',
        'array_bob': 'cool_array.bob',
        'display_bob': 'cool_display.bob',
        'channel_bob': 'cool_channel.bob',
        'detail_bob': 'cool_channel.bob',
        'label': 'Cooling',
        'mode': 'yaml_array',
    },
    'cam': {
        'widget_dir': 'unicam-opi',
        'main_bob': 'Camera_Main.bob',
        'label': 'Cameras',
        'mode': 'per_device',
    },
    'diag': {
        'widget_dir': 'tektronix-opi',
        'main_bob': 'tektronix.bob',
        'label': 'Diagnostics',
        'mode': 'per_device',
    },
    'rf': {
        'widget_dir': 'unimod-opi',
        'label': 'RF',
        'mode': 'per_device',
    },
    'modulator': {
        'widget_dir': 'unimod-opi',
        'label': 'Modulators',
        'mode': 'per_device',
    },
    'timing': {
        'widget_dir': None,
        'label': 'Timing',
        'mode': 'per_device',
    },
    'bpm': {
        'widget_dir': 'unibpm-opi',
        'main_bob': 'orbit.bob',
        'label': 'BPM',
        'mode': 'per_device',
    },
}

# Ordered list controlling how tabs appear in the launcher
DEVGROUP_TAB_ORDER = [
    'mag', 'mot', 'vac', 'cam', 'io', 'cool',
    'rf', 'modulator', 'diag', 'bpm', 'timing',
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_opi_path(opi_value, epik8s_opi_path, hint_dirs=None):
    """Resolve an OPI field value to a relative path inside epik8s-opi.

    Searches *hint_dirs* first, then all known widget dirs, then the root.
    Returns a project-relative path like ``epik8s-opi/unimag-opi/foo.bob``
    or *None* if nothing is found.
    """
    search_dirs = []
    if hint_dirs:
        search_dirs.extend(hint_dirs)
    for reg in DEVGROUP_REGISTRY.values():
        d = reg.get('widget_dir')
        if d and d not in search_dirs:
            search_dirs.append(d)

    for d in search_dirs:
        candidate = os.path.join(epik8s_opi_path, d, opi_value)
        if os.path.exists(candidate):
            return f"epik8s-opi/{d}/{opi_value}"

    # Try root of epik8s-opi
    if os.path.exists(os.path.join(epik8s_opi_path, opi_value)):
        return f"epik8s-opi/{opi_value}"

    return None


def _collect_zones(iocs):
    """Return a set of all zone names that appear in *iocs*."""
    zones = set()
    for ioc in iocs:
        z = ioc.get('zones', 'ALL')
        if isinstance(z, list):
            zones.update(z)
        elif isinstance(z, str):
            zones.add(z)
        for dev in ioc.get('devices', []):
            dz = dev.get('zones', [])
            if isinstance(dz, list):
                zones.update(dz)
            elif isinstance(dz, str):
                zones.add(dz)
    return zones


def _zone_list(iocs):
    """Return launcher zone tabs with ALL first."""
    zones = {zone for zone in _collect_zones(iocs) if zone}
    return ['ALL'] + sorted(zones - {'ALL'})


def _normalize_zones(value, default=None):
    """Return a normalized list of zone names."""
    if default is None:
        default = []
    if isinstance(value, list):
        return [str(zone) for zone in value if zone not in (None, '')]
    if isinstance(value, str):
        return [value] if value else list(default)
    return list(default)


def _device_zones(ioc, dev):
    """Return device zones, falling back to IOC zones and then ALL."""
    zones = _normalize_zones(dev.get('zones'), None)
    if zones:
        return zones
    zones = _normalize_zones(ioc.get('zones'), None)
    return zones or ['ALL']


def _device_in_zone(ioc, dev, zone):
    """Return True when a device belongs to the requested zone."""
    return zone == 'ALL' or zone in _device_zones(ioc, dev)


def _device_sort_key(ioc, dev):
    """Sort devices by zone first, then by label and IOC name."""
    zones = tuple(zone.lower() for zone in _device_zones(ioc, dev))
    return (
        zones,
        _device_label(ioc, dev).lower(),
        ioc.get('name', '').lower(),
        dev.get('name', '').lower(),
    )


def _device_label_sort_key(ioc, dev):
    """Sort devices by label, then IOC name and zone."""
    return (
        _device_label(ioc, dev).lower(),
        ioc.get('name', '').lower(),
        tuple(zone.lower() for zone in _device_zones(ioc, dev)),
        dev.get('name', '').lower(),
    )


def _flatten_devices(iocs):
    """Yield ``(ioc, device_dict)`` pairs, expanding the ``devices`` list.

    If an IOC has no ``devices`` key a single synthetic entry with the IOC
    name is yielded so that every IOC produces at least one device row.
    """
    flattened = []
    for ioc in iocs:
        devices = ioc.get('devices', [])
        if devices:
            for dev in devices:
                flattened.append((ioc, dev))
        else:
            flattened.append((ioc, {'name': ioc.get('name', '')}))

    if DEVICE_ORDER == 'source-order':
        ordered = flattened
    elif DEVICE_ORDER == 'label-order':
        ordered = sorted(flattened, key=lambda item: _device_label_sort_key(item[0], item[1]))
    else:
        ordered = sorted(flattened, key=lambda item: _device_sort_key(item[0], item[1]))

    for ioc, dev in ordered:
        yield ioc, dev


def _device_root(ioc, dev):
    """Return the device root suffix used by OPIs for one IOC/device pair."""
    iocroot = ioc.get('iocroot', '')
    devname = dev.get('name', '')
    if iocroot:
        return f"{iocroot}:{devname}" if devname else iocroot
    return devname


def _device_label(ioc, dev):
    """Return the tab label to use for a single device."""
    return dev.get('alias') or dev.get('name') or ioc.get('name', '')


def _device_macros(devgroup, ioc, dev, full_root):
    """Build the macro dictionary for a direct per-device OPI."""
    iocprefix = ioc.get('iocprefix', '')
    devname = dev.get('name', '')
    zones = ioc.get('zones', [])
    macros = {
        "P": iocprefix,
        "R": full_root,
        "NAME": _device_label(ioc, dev),
        "DEVICE": iocprefix,
        "CAM": full_root,
        "TYPE": ioc.get('devtype', ''),
        "ZONE": ','.join(zones) if zones else '',
    }
    if devgroup == 'cam':
        macros.update({
            "HW": ioc.get('devtype', ''),
            "HCAM": f"{iocprefix}:{full_root}:cam1:",
            "HDEVICE": f"{iocprefix}:{full_root}:",
            "HIMAGE": f"{iocprefix}:{full_root}:image1:",
            "HPROC": f"{iocprefix}:{full_root}:Proc1:",
            "HROI": f"{iocprefix}:{full_root}:ROI1:",
            "HSTATS": f"{iocprefix}:{full_root}:Stats1:",
            "HTRANS": f"{iocprefix}:{full_root}:Trans1:",
        })
    return macros


def _collect_per_device_entries(reg, devgroup, iocs, epik8s_opi_path, device_pairs=None):
    """Collect resolved direct-OPI entries for one devgroup."""
    entries = []
    hint_dirs = [reg['widget_dir']] if reg.get('widget_dir') else []
    pairs = device_pairs if device_pairs is not None else _flatten_devices(iocs)

    for ioc, dev in pairs:
        full_root = _device_root(ioc, dev)
        dev_opi = dev.get('opi', ioc.get('opi', ''))
        if dev_opi:
            bob_path = _resolve_opi_path(dev_opi, epik8s_opi_path, hint_dirs)
        else:
            main = reg.get('main_bob')
            bob_path = _resolve_opi_path(main, epik8s_opi_path, hint_dirs) if main else None

        if not bob_path:
            continue

        macros = _device_macros(devgroup, ioc, dev, full_root)
        # OPI macro: used by embedded channel displays to open the detail screen
        main_bob = reg.get('main_bob', '')
        if main_bob:
            macros['OPI'] = main_bob
        entries.append({
            "label": _device_label(ioc, dev),
            "bob_path": bob_path,
            "macros": macros,
        })

    return entries


# ---------------------------------------------------------------------------
# PV-list parsing  (--detailed mode)
# ---------------------------------------------------------------------------

PV_ROW_H = 22
PV_GAP = 2
PV_LABEL_W = 300
PV_VALUE_W = 220
PV_X = 10


def _parse_pvlist(filepath):
    """Parse a ``pvlist.txt`` and return a sorted, deduplicated PV list."""
    pvs = []
    with open(filepath, 'r') as f:
        for line in f:
            pv = line.strip().rstrip(',').strip()
            if pv and not pv.startswith('#'):
                pvs.append(pv)
    return sorted(set(pvs))


def _assign_pvs_to_devices(pvs, iocprefix, device_names):
    """Match PVs to known device names.

    Returns ``{device_name: [(pv, field)], None: [(pv, pv)]}``.
    """
    result = {}
    sorted_devs = sorted(device_names, key=len, reverse=True)
    for pv in pvs:
        if ':AsynIO' in pv:
            continue
        assigned = False
        for devname in sorted_devs:
            prefix_colon = f"{iocprefix}:{devname}:"
            if pv.startswith(prefix_colon):
                result.setdefault(devname, []).append((pv, pv[len(prefix_colon):]))
                assigned = True
                break
            prefix_direct = f"{iocprefix}:{devname}"
            if pv.startswith(prefix_direct):
                remainder = pv[len(prefix_direct):]
                if remainder == '' or remainder[0] in '.:':
                    field = remainder.lstrip('.:') or 'VAL'
                    result.setdefault(devname, []).append((pv, field))
                    assigned = True
                    break
                if remainder[0].isupper():
                    result.setdefault(devname, []).append((pv, remainder))
                    assigned = True
                    break
        if not assigned:
            result.setdefault(None, []).append((pv, pv))
    return result


def _subcategorize_fields(pv_field_pairs):
    """Group ``(pv, field)`` pairs by subsystem prefix.

    ``Roi1:Stats1:MinValue_RBV`` → category ``Roi1:Stats1``, short ``MinValue_RBV``.
    Single-segment fields go into ``Main``.
    """
    cats = {}
    for pv, field in pv_field_pairs:
        parts = field.split(':')
        if len(parts) > 1:
            cat = ':'.join(parts[:-1])
            short = parts[-1]
        else:
            cat = 'Main'
            short = field
        cats.setdefault(cat, []).append((pv, short))
    for c in cats:
        cats[c].sort(key=lambda x: x[1].lower())
    return cats


def _is_readback(field_name):
    """Heuristic: True when *field_name* looks like a read-back value."""
    return '_RBV' in field_name or field_name in ('RBV',)


def _add_pv_row(grp, pv, label_text, x, y, uid):
    """Add label + value widget row, return next *y*."""
    lbl = widget.Label(f"{uid}-l", label_text, x, y, PV_LABEL_W, PV_ROW_H)
    lbl.font_size(11)
    grp.add_widget(lbl)
    vx = x + PV_LABEL_W + 5
    if _is_readback(label_text):
        val = widget.TextUpdate(f"{uid}-v", pv, vx, y, PV_VALUE_W, PV_ROW_H)
    else:
        val = widget.TextEntry(f"{uid}-v", pv, vx, y, PV_VALUE_W, PV_ROW_H)
    val.font_size(11)
    grp.add_widget(val)
    return y + PV_ROW_H + PV_GAP


def _build_pv_panel(pvs_list, uid_prefix, width, min_height):
    """Group of PV label + value rows.  *pvs_list*: ``[(full_pv, display_label)]``."""
    needed_h = max(len(pvs_list) * (PV_ROW_H + PV_GAP) + 20, min_height)
    grp = widget.Group(uid_prefix, 0, 0, width, needed_h)
    grp.no_style()
    y = 5
    for idx, (pv, short) in enumerate(pvs_list):
        y = _add_pv_row(grp, pv, short, PV_X, y, f"{uid_prefix}-{idx}")
    return grp


# ---------------------------------------------------------------------------
# Tab builders
# ---------------------------------------------------------------------------

# -- Legacy tab builders (kept for --detailed and fallback) --

def _build_yaml_array_tab(reg, iocs, width, height):
    """Build a Group with zone NavigationTabs for a YAML-driven devgroup.

    The actual device list is populated at Phoebus runtime by the
    ``YAMLLoadDevicePopulateArray.py`` script reading ``values.yaml``.
    """
    devgroup = [k for k, v in DEVGROUP_REGISTRY.items() if v is reg][0]
    conffile = '../../values.yaml'  # relative from epik8s-opi/<widget_dir>/
    bob_path = f"epik8s-opi/{reg['widget_dir']}/{reg['array_bob']}"

    grp = widget.Group(f"grp-{devgroup}", 0, 0, width, height)
    grp.macro("CONFFILE", conffile)
    grp.macro("GROUP", devgroup)
    grp.no_style()

    zones = _collect_zones(iocs)
    zone_list = ['ALL'] + sorted(zones - {'ALL'})

    nav = widget.NavigationTabs(f"zone-{devgroup}", 0, 0, width, height)
    nav.tab_direction_horizontal()
    for zone in zone_list:
        nav.tab(zone, bob_path, "", {"ZONE": zone, "CONFFILE": conffile, "GROUP": devgroup})

    grp.add_widget(nav)
    return grp


def _build_per_device_tab(reg, devgroup, iocs, width, height, epik8s_opi_path):
    """Build NavigationTabs with one tab per device for direct-macro devgroups."""
    nav = widget.NavigationTabs(f"nav-{devgroup}", 0, 0, width, height)
    nav.tab_direction_horizontal()
    entries = _collect_per_device_entries(reg, devgroup, iocs, epik8s_opi_path)
    for entry in entries:
        nav.tab(entry['label'], entry['bob_path'], "", entry['macros'])
    return nav if entries else None


# ---------------------------------------------------------------------------
# Dashboard-style launcher builders
# ---------------------------------------------------------------------------

# Layout constants for the dashboard
DASH_HEADER_H = 60          # top banner height
DASH_SECTION_HEADER_H = 32  # devgroup section header height
DASH_BTN_W = 100            # "Open" button width
DASH_BTN_H = 30             # "Open" button height
DASH_ROW_GAP = 8            # gap between rows
DASH_SECTION_GAP = 20       # gap between dev-group sections
DASH_LEFT_PAD = 10
DASH_BTN_GAP = 12           # horizontal gap between inline content and button
DASH_ROW_PAD_Y = 4          # top/bottom padding inside a row
DASH_MIN_ROW_H = 44         # minimum height for rows without embedded content
DASH_LABEL_W = 180          # label width for non-embedded rows
DASH_INFO_W = 520           # prefix/info width for non-embedded rows
DASH_TABS_H = 50            # reserved height for a tabs header
DEVICE_ORDER = DEFAULT_DEVICE_ORDER


def _read_bob_display_size(bob_path):
    """Return ``(width, height)`` for a .bob display, or ``(None, None)``."""
    try:
        root = ET.parse(bob_path).getroot()
    except (ET.ParseError, OSError):
        return None, None

    width = root.findtext('width') or root.findtext('./widget/width')
    height = root.findtext('height') or root.findtext('./widget/height')
    try:
        return int(width), int(height)
    except (TypeError, ValueError):
        return None, None


def _resolve_embed_spec(reg, epik8s_opi_path):
    """Resolve compact display metadata for a devgroup section."""
    channel_bob = reg.get('channel_bob')
    widget_dir = reg.get('widget_dir')
    if not (channel_bob and widget_dir):
        return None

    candidate = os.path.join(epik8s_opi_path, widget_dir, channel_bob)
    if not os.path.exists(candidate):
        return None

    width, height = _read_bob_display_size(candidate)
    return {
        'path': f"epik8s-opi/{widget_dir}/{channel_bob}",
        'width': width or 800,
        'height': height or DASH_MIN_ROW_H,
    }


def _dashboard_row_height(embed_spec=None):
    """Compute a row height large enough for its embedded content."""
    if not embed_spec:
        return DASH_MIN_ROW_H
    return max(DASH_MIN_ROW_H, embed_spec['height'] + 2 * DASH_ROW_PAD_Y)


def _add_row_background(grp, uid, row_w, row_h):
    """Add a subtle background to make device rows visually distinct."""
    bg = widget.Rectangle(f"rowbg-{uid}", 0, 0, row_w, row_h)
    bg.background_color(246, 248, 252)
    bg.line_color(220, 225, 235)
    grp.add_widget(bg)


def _build_dashboard_header(conf, iocs, width):
    """Build a fixed header with beamline name and summary."""
    beamline = conf.get('beamline', '?')
    namespace = conf.get('namespace', '?')

    grp = widget.Group("header", 0, 0, width, DASH_HEADER_H)
    grp.no_style()

    # Background bar
    bg = widget.Rectangle("header-bg", 0, 0, width, DASH_HEADER_H)
    bg.background_color(30, 60, 110)
    grp.add_widget(bg)

    # Title
    title = widget.Label("header-title",
                          f"{beamline.upper()} — {len(iocs)} IOCs",
                          DASH_LEFT_PAD, 8, width - 300, 28)
    title.font_size(20)
    title.font_style_bold()
    title.foreground_color(255, 255, 255)
    grp.add_widget(title)

    # Subtitle
    n_devs = sum(len(ioc.get('devices', [])) for ioc in iocs)
    sub = widget.Label("header-sub",
                        f"namespace: {namespace}  |  devices: {n_devs}",
                        DASH_LEFT_PAD, 34, width - 300, 20)
    sub.font_size(12)
    sub.foreground_color(200, 210, 230)
    grp.add_widget(sub)

    return grp


def _build_device_row_with_embed(uid, entry, embed_bob, x, y, row_w):
    """Build one device row: embedded compact display + Open-in-window button.

    *entry* has keys: label, bob_path, macros.
    *embed_bob* is the compact .bob metadata to embed inline.
    """
    row_h = _dashboard_row_height(embed_bob)
    grp = widget.Group(f"row-{uid}", x, y, row_w, row_h)
    grp.no_style()
    _add_row_background(grp, uid, row_w, row_h)

    if embed_bob:
        embed_x = 8
        embed_y = max(DASH_ROW_PAD_Y, (row_h - embed_bob['height']) // 2)
        emb = widget.EmbeddedDisplay(f"emb-{uid}", embed_bob['path'],
                                      embed_x, embed_y,
                                      embed_bob['width'], embed_bob['height'])
        emb.no_resize()
        for mk, mv in entry['macros'].items():
            emb.macro(mk, str(mv))
        grp.add_widget(emb)
    else:
        lbl = widget.Label(f"lbl-{uid}", entry['label'],
                           12, 8, DASH_LABEL_W, 28)
        lbl.font_size(14)
        lbl.font_style_bold()
        lbl.foreground_color(30, 60, 110)
        grp.add_widget(lbl)

        if entry['macros'].get('P'):
            info = widget.Label(
                f"inf-{uid}",
                f"{entry['macros']['P']}:{entry['macros'].get('R', '')}",
                12 + DASH_LABEL_W + 8, 8, DASH_INFO_W, 28)
            info.font_size(12)
            info.foreground_color(100, 100, 100)
            grp.add_widget(info)

    # "Open" button → opens detail OPI in independent window
    if embed_bob:
        btn_x = min(row_w - DASH_BTN_W - 8,
                    8 + embed_bob['width'] + DASH_BTN_GAP)
    else:
        btn_x = min(row_w - DASH_BTN_W - 8,
                    12 + DASH_LABEL_W + 8 + DASH_INFO_W + DASH_BTN_GAP)
    btn_y = (row_h - DASH_BTN_H) // 2
    btn = widget.ActionButton(f"btn-{uid}", "Open ⬈", "",
                               btn_x, btn_y, DASH_BTN_W, DASH_BTN_H)
    btn.action_open_display(entry['bob_path'], 'window',
                            f"Open {entry['label']}",
                            entry['macros'])
    btn.background_color(60, 120, 200)
    btn.foreground_color(255, 255, 255)
    grp.add_widget(btn)

    return grp


def _build_device_row_label_only(uid, label, bob_path, macros, x, y, row_w):
    """Build a simple label + Open button row (no embedded display)."""
    entry = {
        'label': label,
        'bob_path': bob_path,
        'macros': macros,
    }
    grp = _build_device_row_with_embed(uid, entry, None, x, y, row_w)
    return grp


def _build_section_header(label, devgroup, n_devices, x, y, width):
    """Build a devgroup section header bar."""
    grp = widget.Group(f"sh-{devgroup}", x, y, width, DASH_SECTION_HEADER_H)
    grp.no_style()

    bg = widget.Rectangle(f"shbg-{devgroup}", 0, 0, width, DASH_SECTION_HEADER_H)
    bg.background_color(220, 225, 235)
    grp.add_widget(bg)

    lbl = widget.Label(f"shl-{devgroup}",
                        f"  {label}  ({n_devices} devices)",
                        0, 4, 500, 24)
    lbl.font_size(15)
    lbl.font_style_bold()
    lbl.foreground_color(30, 60, 110)
    grp.add_widget(lbl)

    return grp


def _section_panel_height(n_devices, embed_bob=None):
    """Return the content height needed for a section device panel."""
    if n_devices <= 0:
        return _dashboard_row_height(embed_bob)
    row_h = max(_dashboard_row_height(embed_bob), _dashboard_row_height())
    return n_devices * row_h + max(0, n_devices - 1) * DASH_ROW_GAP


def _filtered_device_pairs(iocs, zone='ALL'):
    """Return sorted device pairs for a single zone."""
    return [
        (ioc, dev)
        for ioc, dev in _flatten_devices(iocs)
        if _device_in_zone(ioc, dev, zone)
    ]


def _build_device_rows_panel(devgroup, reg, device_pairs, epik8s_opi_path, width, panel_id):
    """Build a panel containing all device rows for one zone tab."""
    embed_bob = _resolve_embed_spec(reg, epik8s_opi_path)
    panel_h = _section_panel_height(len(device_pairs), embed_bob)
    grp = widget.Group(panel_id, 0, 0, width, panel_h)
    grp.no_style()

    y = 0
    for idx, (ioc, dev) in enumerate(device_pairs):
        full_root = _device_root(ioc, dev)
        entry = _collect_per_device_entries(reg, devgroup, [], epik8s_opi_path,
                                            device_pairs=[(ioc, dev)])
        if entry:
            row = _build_device_row_with_embed(
                f"{devgroup}-{panel_id}-{idx}", entry[0], embed_bob, 0, y, width)
            row_h = _dashboard_row_height(embed_bob)
        else:
            devname = _device_label(ioc, dev)
            macros = _device_macros(devgroup, ioc, dev, full_root)
            row = _build_device_row_label_only(
                f"{devgroup}-{panel_id}-{idx}", devname, "", macros, 0, y, width)
            row_h = _dashboard_row_height()
        grp.add_widget(row)
        y += row_h + DASH_ROW_GAP

    return grp


def _build_zone_tabs(devgroup, reg, dg_iocs, epik8s_opi_path, x, y, width):
    """Build section-local tabs to switch device rows by zone."""
    zone_list = _zone_list(dg_iocs)
    embed_bob = _resolve_embed_spec(reg, epik8s_opi_path)
    panel_h = max(
        _section_panel_height(len(_filtered_device_pairs(dg_iocs, zone)), embed_bob)
        for zone in zone_list
    )
    tabs = widget.Tabs(f"zone-tabs-{devgroup}", x, y, width, panel_h + DASH_TABS_H)
    for zone in zone_list:
        zone_pairs = _filtered_device_pairs(dg_iocs, zone)
        tabs.tab(zone)
        tabs.add_widget(
            zone,
            _build_device_rows_panel(
                devgroup, reg, zone_pairs, epik8s_opi_path, width,
                f"{devgroup}-{zone.lower()}"
            )
        )
    return tabs, panel_h + DASH_TABS_H


def _build_dashboard_body(conf, iocs, devgroups, epik8s_opi_path, width, body_h):
    """Build the scrollable body with all device groups and device rows."""
    # Calculate the total height needed
    total_h = 10
    for devgroup in _ordered_devgroups(devgroups):
        dg_iocs = devgroups[devgroup]
        n_dev = sum(1 for _ in _flatten_devices(dg_iocs))
        embed_spec = _resolve_embed_spec(DEVGROUP_REGISTRY.get(devgroup, {}), epik8s_opi_path)
        total_h += DASH_SECTION_HEADER_H + DASH_SECTION_GAP
        zone_list = _zone_list(dg_iocs)
        if len(zone_list) > 2:
            panel_h = max(
                _section_panel_height(len(_filtered_device_pairs(dg_iocs, zone)), embed_spec)
                for zone in zone_list
            )
            total_h += panel_h + DASH_TABS_H
        else:
            row_h = _dashboard_row_height(embed_spec)
            total_h += n_dev * (row_h + DASH_ROW_GAP)
        total_h += DASH_SECTION_GAP

    # The body group may be taller than the screen — Phoebus will scroll
    content_h = max(total_h, body_h)
    body = widget.Group("dashboard-body", 0, DASH_HEADER_H, width, content_h)
    body.no_style()

    y = 10
    for devgroup in _ordered_devgroups(devgroups):
        dg_iocs = devgroups[devgroup]
        reg = DEVGROUP_REGISTRY.get(devgroup, {})
        label = reg.get('label', devgroup.title())
        n_dev = sum(1 for _ in _flatten_devices(dg_iocs))

        # Section header
        sec_hdr = _build_section_header(label, devgroup, n_dev,
                                         DASH_LEFT_PAD, y, width - 2 * DASH_LEFT_PAD)
        body.add_widget(sec_hdr)
        y += DASH_SECTION_HEADER_H + DASH_ROW_GAP

        zone_list = _zone_list(dg_iocs)
        section_w = width - 2 * DASH_LEFT_PAD
        if len(zone_list) > 2:
            tabs, tabs_h = _build_zone_tabs(devgroup, reg, dg_iocs, epik8s_opi_path,
                                            DASH_LEFT_PAD, y, section_w)
            body.add_widget(tabs)
            y += tabs_h
        else:
            # Resolve embedded compact display
            embed_bob = _resolve_embed_spec(reg, epik8s_opi_path)
            row_h = _dashboard_row_height(embed_bob)

            # Collect per-device entries with resolved OPI paths
            entries = _collect_per_device_entries(reg, devgroup, dg_iocs, epik8s_opi_path)

            if entries:
                for idx, entry in enumerate(entries):
                    uid = f"{devgroup}-{entry['label']}-{idx}"
                    row = _build_device_row_with_embed(
                        uid, entry, embed_bob,
                        DASH_LEFT_PAD, y, section_w)
                    body.add_widget(row)
                    y += row_h + DASH_ROW_GAP
            else:
                # No resolved OPI — fall back to label-only rows
                for ioc, dev in _flatten_devices(dg_iocs):
                    devname = _device_label(ioc, dev)
                    uid = f"{devgroup}-{devname}"
                    full_root = _device_root(ioc, dev)
                    macros = _device_macros(devgroup, ioc, dev, full_root)
                    row = _build_device_row_label_only(
                        uid, devname, "", macros,
                        DASH_LEFT_PAD, y, section_w)
                    body.add_widget(row)
                    y += _dashboard_row_height() + DASH_ROW_GAP

        y += DASH_SECTION_GAP

    # Services section
    services = conf.get('epicsConfiguration', {}).get('services', {})
    if services:
        svc_hdr = _build_section_header("Services", "svc", len(services),
                                         DASH_LEFT_PAD, y, width - 2 * DASH_LEFT_PAD)
        body.add_widget(svc_hdr)
        y += DASH_SECTION_HEADER_H + DASH_ROW_GAP

        for svc_name, svc_conf in services.items():
            desc = ''
            if isinstance(svc_conf, dict):
                desc = svc_conf.get('desc', '')
                lb_ip = svc_conf.get('loadbalancer', '')
            else:
                lb_ip = ''
            info_text = f"{svc_name}"
            if desc:
                info_text += f" — {desc}"
            if lb_ip:
                info_text += f"  [{lb_ip}]"
            svc_lbl = widget.Label(f"svc-{svc_name}", info_text,
                                    DASH_LEFT_PAD, y, width - 20, 24)
            svc_lbl.font_size(12)
            body.add_widget(svc_lbl)
            y += 28

    return body


def _ordered_devgroups(devgroups):
    """Return devgroup keys in canonical order."""
    ordered = [dg for dg in DEVGROUP_TAB_ORDER if dg in devgroups]
    ordered += [dg for dg in sorted(devgroups) if dg not in ordered]
    return ordered


def _build_overview_tab(conf, iocs, width, height):
    """Build a summary Group showing beamline name, IOC count, and devgroup breakdown."""
    grp = widget.Group("overview", 0, 0, width, height)
    grp.no_style()

    beamline = conf.get('beamline', '?')
    namespace = conf.get('namespace', '?')

    y = 10
    title = widget.Label("title", f"Beamline: {beamline}  (namespace: {namespace})", 10, y, width - 20, 40)
    title.font_size(22)
    title.font_style_bold()
    grp.add_widget(title)
    y += 50

    subtitle = widget.Label("subtitle", f"Total IOCs: {len(iocs)}", 10, y, 400, 25)
    subtitle.font_size(14)
    grp.add_widget(subtitle)
    y += 35

    # Devgroup summary table
    devgroup_counts = {}
    devgroup_devices = {}
    for ioc in iocs:
        dg = ioc.get('devgroup', 'other')
        devgroup_counts[dg] = devgroup_counts.get(dg, 0) + 1
        ndev = len(ioc.get('devices', [{'_': 1}]))
        devgroup_devices[dg] = devgroup_devices.get(dg, 0) + ndev

    # Header
    hdr = widget.Label("hdr", "Group            IOCs   Devices", 10, y, 500, 22)
    hdr.font_style_bold()
    grp.add_widget(hdr)
    y += 25

    sep = widget.Rectangle("sep", 10, y, 500, 2)
    grp.add_widget(sep)
    y += 8

    for dg in DEVGROUP_TAB_ORDER:
        if dg not in devgroup_counts:
            continue
        reg = DEVGROUP_REGISTRY.get(dg, {})
        label_text = reg.get('label', dg.title())
        row_text = f"{label_text:<16} {devgroup_counts[dg]:>4}   {devgroup_devices[dg]:>4}"
        row = widget.Label(f"row-{dg}", row_text, 10, y, 500, 20)
        grp.add_widget(row)
        y += 22

    # Unknown devgroups
    for dg in sorted(devgroup_counts):
        if dg in DEVGROUP_REGISTRY:
            continue
        row_text = f"{dg:<16} {devgroup_counts[dg]:>4}   {devgroup_devices.get(dg, 0):>4}"
        row = widget.Label(f"row-{dg}", row_text, 10, y, 500, 20)
        grp.add_widget(row)
        y += 22

    # Services
    services = conf.get('epicsConfiguration', {}).get('services', {})
    if services:
        y += 15
        svc_title = widget.Label("svc-title", "Services", 10, y, 300, 22)
        svc_title.font_style_bold()
        grp.add_widget(svc_title)
        y += 25
        for svc_name, svc_conf in services.items():
            desc = ''
            if isinstance(svc_conf, dict):
                desc = svc_conf.get('desc', '')
            txt = f"  {svc_name}: {desc}" if desc else f"  {svc_name}"
            svc_lbl = widget.Label(f"svc-{svc_name}", txt, 10, y, 600, 20)
            grp.add_widget(svc_lbl)
            y += 22

    # Gateway info
    gw = services.get('gateway', {})
    if isinstance(gw, dict) and gw.get('loadbalancer'):
        y += 10
        gw_lbl = widget.Label("ca-gw", f"CA Gateway: {gw['loadbalancer']}", 10, y, 400, 20)
        grp.add_widget(gw_lbl)
        y += 22
    pva_gw = services.get('pvagateway', {})
    if isinstance(pva_gw, dict) and pva_gw.get('loadbalancer'):
        gw_lbl = widget.Label("pva-gw", f"PVA Gateway: {pva_gw['loadbalancer']}", 10, y, 400, 20)
        grp.add_widget(gw_lbl)
        y += 22

    return grp


# ---------------------------------------------------------------------------
# Detailed-launcher builders  (--detailed mode)
# ---------------------------------------------------------------------------

def _build_ioc_info_panel(ioc, width, height):
    """Static IOC metadata panel (from beamline YAML, no PVs)."""
    grp = widget.Group("ioc-info", 0, 0, width, height)
    grp.no_style()
    y = 10
    fields = [
        ("IOC Name",    ioc.get('name', '')),
        ("Prefix",      ioc.get('iocprefix', '')),
        ("Template",    ioc.get('template', '')),
        ("Device Type", ioc.get('devtype', '')),
        ("Devgroup",    ioc.get('devgroup', '')),
        ("Zones",       ', '.join(ioc.get('zones', []))),
        ("PVA",         str(ioc.get('pva', False))),
        ("Chart URL",   ioc.get('charturl', '')),
        ("Autosync",    str(ioc.get('autosync', ''))),
    ]
    for key, val in fields:
        if not val or val in ('False', ''):
            continue
        kl = widget.Label(f"ik-{key}", f"{key}:", 10, y, 160, 22)
        kl.font_style_bold()
        kl.font_size(13)
        grp.add_widget(kl)
        vl = widget.Label(f"iv-{key}", str(val), 180, y, width - 200, 22)
        vl.font_size(13)
        grp.add_widget(vl)
        y += 26

    # Devices
    devices = ioc.get('devices', [])
    if devices:
        y += 10
        dh = widget.Label("dh", "Devices:", 10, y, 200, 24)
        dh.font_style_bold()
        dh.font_size(14)
        grp.add_widget(dh)
        y += 28
        for dev in devices:
            parts = [f"{k}={v}" for k, v in dev.items() if k != 'iocinit']
            dl = widget.Label(f"d-{dev.get('name','')}",
                              f"  {', '.join(parts)}", 10, y, width - 20, 20)
            dl.font_size(11)
            grp.add_widget(dl)
            y += 22

    # IOC parameters
    for section, heading in [('iocparam', 'IOC Parameters'), ('iocinit', 'IOC Init')]:
        items = ioc.get(section, [])
        if not items:
            continue
        y += 10
        sh = widget.Label(f"sh-{section}", f"{heading}:", 10, y, 250, 24)
        sh.font_style_bold()
        sh.font_size(14)
        grp.add_widget(sh)
        y += 28
        for p in items:
            txt = f"  {p.get('name', '')} = {p.get('value', '')}"
            pl = widget.Label(f"sp-{section}-{p.get('name','')}", txt,
                              10, y, width - 20, 20)
            pl.font_size(11)
            grp.add_widget(pl)
            y += 22
    return grp


def _build_ioc_detail_tabs(ioc, pvlist_path, width, height):
    """Full detail Tabs for one IOC: Info | <device>… | Other PVs | All PVs."""
    iocname = ioc.get('name', 'ioc')
    iocprefix = ioc.get('iocprefix', '')
    device_names = [d.get('name', '') for d in ioc.get('devices', []) if d.get('name')]

    tabs = widget.Tabs(f"dt-{iocname}", 0, 0, width, height)
    inner_h = height - 50

    # --- Info ---
    tabs.tab("Info")
    tabs.add_widget("Info", _build_ioc_info_panel(ioc, width, inner_h))

    # --- PV-based tabs ---
    pvs = _parse_pvlist(pvlist_path) if pvlist_path and os.path.exists(pvlist_path) else []
    if not pvs:
        return tabs

    device_pvs = _assign_pvs_to_devices(pvs, iocprefix, device_names)

    for devname in device_names:
        dev_pv_list = device_pvs.get(devname, [])
        if not dev_pv_list:
            continue
        cats = _subcategorize_fields(dev_pv_list)
        if len(cats) <= 1:
            tabs.tab(devname)
            tabs.add_widget(devname, _build_pv_panel(
                dev_pv_list, f"dp-{iocname}-{devname}", width, inner_h))
        else:
            sub = widget.Tabs(f"st-{iocname}-{devname}", 0, 0, width, inner_h)
            cat_h = inner_h - 50
            cat_order = (['Main'] if 'Main' in cats else []) + \
                        sorted(c for c in cats if c != 'Main')
            for cat in cat_order:
                sub.tab(cat)
                sub.add_widget(cat, _build_pv_panel(
                    cats[cat], f"dp-{iocname}-{devname}-{cat}", width, cat_h))
            tabs.tab(devname)
            tabs.add_widget(devname, sub)

    # Other PVs (not matched to known devices)
    other = device_pvs.get(None, [])
    if other:
        tabs.tab("Other PVs")
        tabs.add_widget("Other PVs", _build_pv_panel(
            other, f"dp-{iocname}-other", width, inner_h))

    # All PVs flat list
    all_clean = [(pv, pv) for pv in pvs if ':AsynIO' not in pv]
    if all_clean:
        tabs.tab("All PVs")
        tabs.add_widget("All PVs", _build_pv_panel(
            all_clean, f"dp-{iocname}-all", width, inner_h))

    return tabs


def _generate_detailed_launcher(conf, iocs, args, project_dir):
    """Generate the detailed ``Launcher_detailed.bob`` file."""
    beamline = conf.get('beamline', 'epik8s')
    title = f"{beamline.upper()} Detailed Launcher"
    output_path = os.path.join(project_dir, args.detailed_output)

    scr = screen.Screen(title, output_path)
    scr.width(args.width)
    scr.height(args.height)
    tab_w, tab_h = args.width, args.height
    inner_h = tab_h - 50

    root_tabs = widget.Tabs("iocs", 0, 0, tab_w, tab_h)
    root_tabs.tab("Overview")
    root_tabs.add_widget("Overview", _build_overview_tab(conf, iocs, tab_w, inner_h))

    for ioc in iocs:
        if args.controls and ioc.get('name') not in args.controls:
            continue
        iocname = ioc.get('name', 'unknown')
        pvlist_path = None
        if args.pvlist_dir:
            candidate = os.path.join(os.path.abspath(args.pvlist_dir),
                                     iocname, 'pvlist.txt')
            if os.path.exists(candidate):
                pvlist_path = candidate

        detail = _build_ioc_detail_tabs(ioc, pvlist_path, tab_w, inner_h)
        root_tabs.tab(iocname)
        root_tabs.add_widget(iocname, detail)
        n_pvs = len(_parse_pvlist(pvlist_path)) if pvlist_path else 0
        n_devs = len(ioc.get('devices', []))
        print(f"  + {iocname}: {n_devs} devices, {n_pvs} PVs")

    scr.add_widget(root_tabs)
    scr.write_screen()
    print(f"\nGenerated {output_path} — '{title}'")


# ---------------------------------------------------------------------------
# Soft-IOC (iocmng) OPI generation
# ---------------------------------------------------------------------------

# Layout constants for softioc detail panels
SIOC_ROW_H = 26
SIOC_GAP = 4
SIOC_LABEL_W = 200
SIOC_VALUE_W = 220
SIOC_UNIT_W = 60
SIOC_LINK_W = 280
SIOC_SECTION_H = 30
SIOC_LEFT = 10
SIOC_DETAIL_W = 900


def _load_softioc_config(config_path):
    """Load and normalise a softioc-mng task config.yaml.

    Returns a dict with keys: parameters, inputs, outputs, rules,
    transforms, rule_defaults.
    """
    with open(config_path, 'r') as f:
        raw = yaml.safe_load(f) or {}

    params = raw.get('parameters', {})

    # Normalise arguments (supports both 'arguments' and legacy 'pvs')
    args_section = raw.get('arguments', raw.get('pvs', {}))
    inputs = args_section.get('inputs', {}) if isinstance(args_section, dict) else {}
    outputs = args_section.get('outputs', {}) if isinstance(args_section, dict) else {}

    return {
        'parameters': params,
        'inputs': inputs,
        'outputs': outputs,
        'rules': raw.get('rules', []),
        'transforms': raw.get('transforms', []),
        'rule_defaults': raw.get('rule_defaults', {}),
    }


def _load_softioc_values(softioc_config_path):
    """Load values-softioc.yaml and resolve task configs.

    Expected format::

        prefix: "SPARC:CONTROL"
        tasks:
          - name: softinterlock
            config: /path/to/softinterlock/config.yaml
            label: Soft Interlock          # optional
            zones: [LINAC]                 # optional

    Or with a ``config_dir`` to auto-discover::

        prefix: "SPARC:CONTROL"
        config_dir: /path/to/task/directories
    """
    with open(softioc_config_path, 'r') as f:
        sconf = yaml.safe_load(f) or {}

    prefix = sconf.get('prefix', '')
    tasks = []

    # Explicit task list
    for entry in sconf.get('tasks', []):
        name = entry.get('name', '')
        config_path = entry.get('config')
        if not config_path:
            continue
        # Resolve relative paths against the softioc values file location
        if not os.path.isabs(config_path):
            base = os.path.dirname(os.path.abspath(softioc_config_path))
            config_path = os.path.join(base, config_path)
        if not os.path.exists(config_path):
            print(f"  WARNING: softioc config not found: {config_path}")
            continue
        task_conf = _load_softioc_config(config_path)
        tasks.append({
            'name': name,
            'label': entry.get('label', name.replace('-', ' ').replace('_', ' ').title()),
            'zones': entry.get('zones', []),
            'prefix': entry.get('prefix', prefix),
            'config': task_conf,
            'config_path': config_path,
        })

    # Auto-discover from config_dir
    config_dir = sconf.get('config_dir')
    if config_dir:
        if not os.path.isabs(config_dir):
            base = os.path.dirname(os.path.abspath(softioc_config_path))
            config_dir = os.path.join(base, config_dir)
        if os.path.isdir(config_dir):
            existing_names = {t['name'] for t in tasks}
            for entry_name in sorted(os.listdir(config_dir)):
                if entry_name in existing_names:
                    continue
                candidate = os.path.join(config_dir, entry_name, 'config.yaml')
                if os.path.exists(candidate):
                    task_conf = _load_softioc_config(candidate)
                    tasks.append({
                        'name': entry_name,
                        'label': entry_name.replace('-', ' ').replace('_', ' ').title(),
                        'zones': [],
                        'prefix': prefix,
                        'config': task_conf,
                        'config_path': candidate,
                    })

    return {'prefix': prefix, 'tasks': tasks}


def _sioc_section_label(grp, uid, text, y, width):
    """Add a section heading bar inside a softioc detail panel."""
    bg = widget.Rectangle(f"sbg-{uid}", 0, y, width, SIOC_SECTION_H)
    bg.background_color(220, 225, 235)
    grp.add_widget(bg)
    lbl = widget.Label(f"slbl-{uid}", f"  {text}", 0, y + 4, width, 22)
    lbl.font_size(13)
    lbl.font_style_bold()
    lbl.foreground_color(30, 60, 110)
    grp.add_widget(lbl)
    return y + SIOC_SECTION_H + SIOC_GAP


def _sioc_pv_row(grp, uid, pv_name, label_text, pv_type, spec, x, y, writable=False):
    """Add one PV row: label + value widget + optional unit/link info."""
    lbl = widget.Label(f"l-{uid}", label_text, x, y, SIOC_LABEL_W, SIOC_ROW_H)
    lbl.font_size(11)
    grp.add_widget(lbl)

    vx = x + SIOC_LABEL_W + 4
    if pv_type == 'bool' and writable:
        val = widget.BooleanButton(f"v-{uid}", pv_name, vx, y, 80, SIOC_ROW_H)
        on_label = spec.get('onam', 'On')
        off_label = spec.get('znam', 'Off')
        val.on_label(on_label)
        val.off_label(off_label)
    elif writable:
        val = widget.TextEntry(f"v-{uid}", pv_name, vx, y, SIOC_VALUE_W, SIOC_ROW_H)
        val.font_size(11)
    else:
        val = widget.TextUpdate(f"v-{uid}", pv_name, vx, y, SIOC_VALUE_W, SIOC_ROW_H)
        val.font_size(11)
    grp.add_widget(val)

    info_x = vx + SIOC_VALUE_W + 8
    unit = spec.get('unit', '')
    link = spec.get('link', '')
    info_parts = []
    if unit:
        info_parts.append(unit)
    if link:
        info_parts.append(f"← {link}")
    if info_parts:
        info = widget.Label(f"i-{uid}", '  '.join(info_parts),
                            info_x, y, SIOC_LINK_W + SIOC_UNIT_W, SIOC_ROW_H)
        info.font_size(10)
        info.foreground_color(120, 120, 120)
        grp.add_widget(info)

    return y + SIOC_ROW_H + SIOC_GAP


def _build_softioc_detail(task_entry, project_dir):
    """Generate a detail .bob file for one softioc-mng task.

    Returns the filename of the generated .bob.
    """
    name = task_entry['name']
    label = task_entry['label']
    prefix = task_entry['prefix']
    conf = task_entry['config']
    inputs = conf['inputs']
    outputs = conf['outputs']
    params = conf['parameters']
    rules = conf['rules']
    transforms = conf['transforms']

    task_pv = f"{prefix}:{name.upper().replace('-', '_')}"

    # Calculate needed height
    n_builtin = 4  # ENABLE, STATUS, MESSAGE, CYCLE_COUNT/RUN
    n_inputs = len(inputs)
    n_outputs = len(outputs)
    n_rules = len(rules)
    n_transforms = len(transforms)
    n_params = len(params)
    total_rows = n_builtin + n_inputs + n_outputs + n_rules + n_transforms + n_params
    total_sections = 2  # Control + Inputs always
    if outputs:
        total_sections += 1
    if rules:
        total_sections += 1
    if transforms:
        total_sections += 1
    if params:
        total_sections += 1

    height = 80 + total_rows * (SIOC_ROW_H + SIOC_GAP) + total_sections * (SIOC_SECTION_H + 12) + 40
    bob_name = f"softioc_{name}.bob"
    bob_path = os.path.join(project_dir, bob_name)

    scr = screen.Screen(f"{label} — Detail", bob_path)
    scr.width(SIOC_DETAIL_W)
    scr.height(height)

    # Title
    title = widget.Label("title", f"{label}", 10, 10, SIOC_DETAIL_W - 20, 30)
    title.font_size(18)
    title.font_style_bold()
    title.foreground_color(30, 60, 110)
    scr.add_widget(title)

    # Subtitle: mode + interval
    mode = params.get('mode', 'continuous')
    interval = params.get('interval', '?')
    sub = widget.Label("subtitle",
                        f"mode: {mode}  |  interval: {interval}s  |  prefix: {task_pv}",
                        10, 40, SIOC_DETAIL_W - 20, 20)
    sub.font_size(11)
    sub.foreground_color(100, 100, 100)
    scr.add_widget(sub)

    y = 70

    # ── Control & Status section ──
    y = _sioc_section_label(scr, "ctrl", "Control & Status", y, SIOC_DETAIL_W)

    # ENABLE
    en_lbl = widget.Label("en-l", "Enable", SIOC_LEFT, y, SIOC_LABEL_W, SIOC_ROW_H)
    en_lbl.font_size(11)
    scr.add_widget(en_lbl)
    en_btn = widget.BooleanButton("en-v", f"{task_pv}:ENABLE",
                                   SIOC_LEFT + SIOC_LABEL_W + 4, y, 80, SIOC_ROW_H)
    en_btn.on_label("Enabled")
    en_btn.off_label("Disabled")
    en_btn.on_color(60, 180, 75)
    en_btn.off_color(200, 60, 60)
    scr.add_widget(en_btn)
    y += SIOC_ROW_H + SIOC_GAP

    # STATUS
    st_lbl = widget.Label("st-l", "Status", SIOC_LEFT, y, SIOC_LABEL_W, SIOC_ROW_H)
    st_lbl.font_size(11)
    scr.add_widget(st_lbl)
    st_val = widget.TextUpdate("st-v", f"{task_pv}:STATUS",
                                SIOC_LEFT + SIOC_LABEL_W + 4, y, SIOC_VALUE_W, SIOC_ROW_H)
    st_val.font_size(11)
    scr.add_widget(st_val)
    y += SIOC_ROW_H + SIOC_GAP

    # MESSAGE
    msg_lbl = widget.Label("msg-l", "Message", SIOC_LEFT, y, SIOC_LABEL_W, SIOC_ROW_H)
    msg_lbl.font_size(11)
    scr.add_widget(msg_lbl)
    msg_val = widget.TextUpdate("msg-v", f"{task_pv}:MESSAGE",
                                 SIOC_LEFT + SIOC_LABEL_W + 4, y, 450, SIOC_ROW_H)
    msg_val.font_size(11)
    scr.add_widget(msg_val)
    y += SIOC_ROW_H + SIOC_GAP

    # CYCLE_COUNT or RUN
    is_triggered = (mode == 'triggered')
    if is_triggered:
        cc_lbl = widget.Label("cc-l", "Trigger", SIOC_LEFT, y, SIOC_LABEL_W, SIOC_ROW_H)
        cc_lbl.font_size(11)
        scr.add_widget(cc_lbl)
        cc_btn = widget.BooleanButton("cc-v", f"{task_pv}:RUN",
                                       SIOC_LEFT + SIOC_LABEL_W + 4, y, 80, SIOC_ROW_H)
        cc_btn.on_label("Run")
        cc_btn.off_label("Idle")
        cc_btn.mode_push()
        scr.add_widget(cc_btn)
    else:
        cc_lbl = widget.Label("cc-l", "Cycle Count", SIOC_LEFT, y, SIOC_LABEL_W, SIOC_ROW_H)
        cc_lbl.font_size(11)
        scr.add_widget(cc_lbl)
        cc_val = widget.TextUpdate("cc-v", f"{task_pv}:CYCLE_COUNT",
                                    SIOC_LEFT + SIOC_LABEL_W + 4, y, 120, SIOC_ROW_H)
        cc_val.font_size(11)
        scr.add_widget(cc_val)
    y += SIOC_ROW_H + SIOC_GAP + 8

    # ── Parameters section ──
    if params:
        y = _sioc_section_label(scr, "params", f"Parameters ({len(params)})", y, SIOC_DETAIL_W)
        for idx, (pk, pv_val) in enumerate(params.items()):
            uid = f"p-{idx}"
            p_lbl = widget.Label(f"l-{uid}", pk, SIOC_LEFT, y, SIOC_LABEL_W, SIOC_ROW_H)
            p_lbl.font_size(11)
            scr.add_widget(p_lbl)
            p_val = widget.Label(f"v-{uid}", str(pv_val),
                                  SIOC_LEFT + SIOC_LABEL_W + 4, y, SIOC_VALUE_W, SIOC_ROW_H)
            p_val.font_size(11)
            p_val.foreground_color(60, 60, 60)
            scr.add_widget(p_val)
            y += SIOC_ROW_H + SIOC_GAP
        y += 8

    # ── Inputs section ──
    if inputs:
        y = _sioc_section_label(scr, "inputs", f"Inputs ({len(inputs)})", y, SIOC_DETAIL_W)
        for idx, (pv_key, spec) in enumerate(inputs.items()):
            pv_type = spec.get('type', 'float')
            pv_name = f"{task_pv}:{pv_key}"
            link = spec.get('link', '')
            unit = spec.get('unit', '')
            label_text = pv_key
            if unit:
                label_text += f" ({unit})"
            # Inputs without links are writable by operators
            writable = not bool(link)
            y = _sioc_pv_row(scr, f"in-{idx}", pv_name, label_text,
                              pv_type, spec, SIOC_LEFT, y, writable=writable)
        y += 8

    # ── Outputs section ──
    if outputs:
        y = _sioc_section_label(scr, "outputs", f"Outputs ({len(outputs)})", y, SIOC_DETAIL_W)
        for idx, (pv_key, spec) in enumerate(outputs.items()):
            pv_type = spec.get('type', 'float')
            pv_name = f"{task_pv}:{pv_key}"
            unit = spec.get('unit', '')
            label_text = pv_key
            if unit:
                label_text += f" ({unit})"
            y = _sioc_pv_row(scr, f"out-{idx}", pv_name, label_text,
                              pv_type, spec, SIOC_LEFT, y, writable=False)
        y += 8

    # ── Transforms section ──
    if transforms:
        y = _sioc_section_label(scr, "transforms", f"Transforms ({len(transforms)})", y, SIOC_DETAIL_W)
        for idx, tr in enumerate(transforms):
            uid = f"tr-{idx}"
            out_name = tr.get('output', '?')
            expr = tr.get('expression', '?')
            tr_lbl = widget.Label(f"l-{uid}", f"{out_name} =", SIOC_LEFT, y, 120, SIOC_ROW_H)
            tr_lbl.font_size(11)
            tr_lbl.font_style_bold()
            scr.add_widget(tr_lbl)
            tr_expr = widget.Label(f"v-{uid}", expr,
                                    SIOC_LEFT + 124, y, SIOC_DETAIL_W - 140, SIOC_ROW_H)
            tr_expr.font_size(11)
            tr_expr.foreground_color(80, 80, 80)
            scr.add_widget(tr_expr)
            y += SIOC_ROW_H + SIOC_GAP
        y += 8

    # ── Rules section ──
    if rules:
        y = _sioc_section_label(scr, "rules", f"Rules ({len(rules)})", y, SIOC_DETAIL_W)
        for idx, rule in enumerate(rules):
            uid = f"rl-{idx}"
            rule_id = rule.get('id', f'rule_{idx}')
            condition = rule.get('condition', '')
            message = rule.get('message', '')
            # Rule ID + condition
            rid_lbl = widget.Label(f"l-{uid}", rule_id, SIOC_LEFT, y, 160, SIOC_ROW_H)
            rid_lbl.font_size(11)
            rid_lbl.font_style_bold()
            rid_lbl.foreground_color(180, 60, 30)
            scr.add_widget(rid_lbl)
            cond_lbl = widget.Label(f"c-{uid}", condition,
                                     SIOC_LEFT + 164, y, SIOC_DETAIL_W - 180, SIOC_ROW_H)
            cond_lbl.font_size(10)
            cond_lbl.foreground_color(80, 80, 80)
            scr.add_widget(cond_lbl)
            y += SIOC_ROW_H + 2
            # Message
            if message:
                msg = widget.Label(f"m-{uid}", f"  → {message}",
                                    SIOC_LEFT + 20, y, SIOC_DETAIL_W - 40, SIOC_ROW_H - 4)
                msg.font_size(10)
                msg.foreground_color(100, 100, 100)
                scr.add_widget(msg)
                y += SIOC_ROW_H - 2
            # Actuators
            actuators = rule.get('actuators', {})
            if actuators:
                act_text = ', '.join(f"{k}→{v}" for k, v in actuators.items())
                act_lbl = widget.Label(f"a-{uid}", f"  actuators: {act_text}",
                                        SIOC_LEFT + 20, y, SIOC_DETAIL_W - 40, SIOC_ROW_H - 4)
                act_lbl.font_size(10)
                act_lbl.foreground_color(200, 80, 30)
                scr.add_widget(act_lbl)
                y += SIOC_ROW_H - 2
            # Outputs
            rule_outputs = rule.get('outputs', {})
            if rule_outputs:
                out_text = ', '.join(f"{k}={v}" for k, v in rule_outputs.items())
                out_lbl = widget.Label(f"o-{uid}", f"  outputs: {out_text}",
                                        SIOC_LEFT + 20, y, SIOC_DETAIL_W - 40, SIOC_ROW_H - 4)
                out_lbl.font_size(10)
                out_lbl.foreground_color(30, 100, 160)
                scr.add_widget(out_lbl)
                y += SIOC_ROW_H - 2
            y += SIOC_GAP

    # Adjust final height
    scr.height(y + 20)
    scr.write_screen()
    print(f"  + Generated {bob_name}: {len(inputs)} inputs, "
          f"{len(outputs)} outputs, {len(rules)} rules, {len(transforms)} transforms")
    return bob_name


def _build_softioc_summary_row(uid, task_entry, bob_name, x, y, row_w):
    """Build a dashboard summary row for one softioc task."""
    name = task_entry['name']
    label = task_entry['label']
    prefix = task_entry['prefix']
    task_pv = f"{prefix}:{name.upper().replace('-', '_')}"
    conf = task_entry['config']
    mode = conf['parameters'].get('mode', 'continuous')

    row_h = DASH_MIN_ROW_H + 4
    grp = widget.Group(f"row-{uid}", x, y, row_w, row_h)
    grp.no_style()
    _add_row_background(grp, uid, row_w, row_h)

    cx = 8
    # Task label
    lbl = widget.Label(f"lbl-{uid}", label, cx, 8, 180, 28)
    lbl.font_size(13)
    lbl.font_style_bold()
    lbl.foreground_color(30, 60, 110)
    grp.add_widget(lbl)
    cx += 184

    # Enable button
    en = widget.BooleanButton(f"en-{uid}", f"{task_pv}:ENABLE", cx, 10, 70, 24)
    en.on_label("ON")
    en.off_label("OFF")
    en.on_color(60, 180, 75)
    en.off_color(200, 60, 60)
    grp.add_widget(en)
    cx += 78

    # Status
    st = widget.TextUpdate(f"st-{uid}", f"{task_pv}:STATUS", cx, 10, 80, 24)
    st.font_size(11)
    grp.add_widget(st)
    cx += 88

    # Cycle count
    if mode != 'triggered':
        cc = widget.TextUpdate(f"cc-{uid}", f"{task_pv}:CYCLE_COUNT", cx, 10, 60, 24)
        cc.font_size(10)
        grp.add_widget(cc)
    else:
        run = widget.BooleanButton(f"run-{uid}", f"{task_pv}:RUN", cx, 10, 60, 24)
        run.on_label("Run")
        run.off_label("Idle")
        run.mode_push()
        grp.add_widget(run)
    cx += 68

    # Message
    msg = widget.TextUpdate(f"msg-{uid}", f"{task_pv}:MESSAGE", cx, 10, 280, 24)
    msg.font_size(10)
    grp.add_widget(msg)
    cx += 288

    # "Open" button → detail panel
    btn = widget.ActionButton(f"btn-{uid}", "Detail ⬈", "",
                               cx, 8, 90, 28)
    btn.action_open_display(bob_name, 'window', f"Open {label}", {})
    btn.background_color(60, 120, 200)
    btn.foreground_color(255, 255, 255)
    grp.add_widget(btn)

    return grp


def _build_softioc_dashboard_section(softioc_data, project_dir, x, y, width):
    """Build the complete Soft IOCs dashboard section.

    Generates detail .bob files and returns (section_group, total_height).
    """
    tasks = softioc_data['tasks']
    if not tasks:
        return None, 0

    # Generate detail panels
    bob_names = {}
    for t in tasks:
        bob_names[t['name']] = _build_softioc_detail(t, project_dir)

    # Section header
    sec_h = DASH_SECTION_HEADER_H
    hdr = _build_section_header("Soft IOCs", "softioc", len(tasks), x, 0, width)

    row_h = DASH_MIN_ROW_H + 4
    total_h = sec_h + DASH_ROW_GAP + len(tasks) * (row_h + DASH_ROW_GAP) + DASH_SECTION_GAP

    grp = widget.Group("softioc-section", x, y, width, total_h)
    grp.no_style()
    grp.add_widget(hdr)

    ry = sec_h + DASH_ROW_GAP
    for idx, t in enumerate(tasks):
        uid = f"sioc-{t['name']}-{idx}"
        row = _build_softioc_summary_row(uid, t, bob_names[t['name']],
                                          0, ry, width)
        grp.add_widget(row)
        ry += row_h + DASH_ROW_GAP

    return grp, total_h


# ---------------------------------------------------------------------------
# Setup helpers
# ---------------------------------------------------------------------------

def _setup_epik8s_opi(project_dir, args):
    """Clone or symlink the epik8s-opi widget library into the project."""
    epik8s_opi_path = os.path.join(project_dir, 'epik8s-opi')
    if args.epik8s_opi_dir:
        src = os.path.abspath(args.epik8s_opi_dir)
        if not os.path.exists(epik8s_opi_path):
            os.symlink(src, epik8s_opi_path)
            print(f"Linked epik8s-opi from {src}")
    elif not os.path.exists(epik8s_opi_path):
        print(f"Cloning epik8s-opi from {args.epik8s_opi_url} "
              f"(branch {args.epik8s_opi_branch})")
        subprocess.run([
            "git", "clone", "--depth", "1",
            "-b", args.epik8s_opi_branch,
            "--recurse-submodules",
            args.epik8s_opi_url, epik8s_opi_path,
        ])
        git_dir = os.path.join(epik8s_opi_path, ".git")
        if os.path.exists(git_dir):
            shutil.rmtree(git_dir)
    return epik8s_opi_path

def _generate_settings(conf, script_dir, project_dir):
    """Render and write ``settings.ini`` for Phoebus."""
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


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main_opigen():
    global DASH_ROW_GAP, DASH_SECTION_GAP, DASH_BTN_GAP, DEVICE_ORDER
    script_dir = os.path.dirname(os.path.realpath(__file__)) + "/template/"

    parser = argparse.ArgumentParser(
        description="Generate a Phoebus OPI project from an EPIK8s beamline "
                    "YAML using reusable epik8s-opi components.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--config", type=str,
                        help="Path to the EPIK8s beamline YAML configuration file.")
    parser.add_argument("--yaml", dest="config", type=str,
                        help="Deprecated alias for --config.")
    parser.add_argument("--version", action="store_true",
                        help="Show the version and exit")
    parser.add_argument("--output", type=str, default="Launcher.bob",
                        help="Main OPI file name")
    parser.add_argument("--title", type=str, default=None,
                        help="Title for the launcher (default: '<beamline> Launcher')")
    parser.add_argument("--projectdir", type=str, default="opi",
                        help="Directory where all project files will be generated")
    parser.add_argument("--width", type=int, default=1900,
                        help="Width of the launcher screen")
    parser.add_argument("--height", type=int, default=1400,
                        help="Height of the launcher screen")
    parser.add_argument("--device-gap", type=int, default=DASH_ROW_GAP,
                        help="Vertical gap between device rows in the dashboard")
    parser.add_argument("--section-gap", type=int, default=DASH_SECTION_GAP,
                        help="Vertical gap between dashboard sections")
    parser.add_argument("--button-gap", type=int, default=DASH_BTN_GAP,
                        help="Horizontal gap between compact rows and their Open button")
    parser.add_argument("--device-order",
                        choices=["zone-order", "source-order", "label-order"],
                        default=DEFAULT_DEVICE_ORDER,
                        help="Ordering of devices inside each launcher section")
    parser.add_argument('--controls', nargs='+',
                        help="Include just the given controls (default ALL).")
    parser.add_argument("--epik8s-opi-url", type=str,
                        default=DEFAULT_EPIK8S_OPI_URL,
                        help="Git URL for epik8s-opi widget library")
    parser.add_argument("--epik8s-opi-branch", type=str, default="main",
                        help="Branch of epik8s-opi to clone")
    parser.add_argument("--epik8s-opi-dir", type=str, default=None,
                        help="Path to existing epik8s-opi checkout (skip cloning)")
    parser.add_argument("--detailed", action="store_true",
                        help="Generate a detailed per-IOC launcher with PV panels")
    parser.add_argument("--pvlist-dir", type=str, default=None,
                        help="Directory with IOC PV lists (<iocname>/pvlist.txt)")
    parser.add_argument("--detailed-output", type=str,
                        default="Launcher_detailed.bob",
                        help="Output filename for the detailed launcher")
    parser.add_argument("--generate-settings-ini", action="store_true",
                        help="Generate settings.ini in the OPI project directory")
    parser.add_argument("--softioc-config", type=str, default=None,
                        help="Path to values-softioc.yaml for softioc-mng OPI generation")
    parser.add_argument("--softioc-only", action="store_true",
                        help="Generate only softioc OPIs (skip beamline launcher)")

    args = parser.parse_args()
    if args.version:
        print(f"epik8s-tools version {__version__}")
        return

    if not args.config and not args.softioc_only:
        print("# must define a valid epik8s configuration yaml --yaml <configuration>")
        return -1

    if args.softioc_only and not args.softioc_config:
        print("# --softioc-only requires --softioc-config <path>")
        return -1

    if not args.projectdir:
        print("# must define an output projectdir --projectdir <project output directory>")
        return -2

    DASH_ROW_GAP = max(0, args.device_gap)
    DASH_SECTION_GAP = max(0, args.section_gap)
    DASH_BTN_GAP = max(0, args.button_gap)
    DEVICE_ORDER = args.device_order

    project_dir = os.path.abspath(args.projectdir)
    os.makedirs(project_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # Load softioc-mng configuration (optional)
    # ------------------------------------------------------------------
    softioc_data = None
    if args.softioc_config:
        if not os.path.exists(args.softioc_config):
            print(f"## softioc config not found: {args.softioc_config}")
            return -3
        print(f"\n--- Soft IOC OPI generation ---")
        softioc_data = _load_softioc_values(args.softioc_config)
        print(f"Loaded {len(softioc_data['tasks'])} softioc tasks "
              f"(prefix: {softioc_data['prefix']})")

    # ------------------------------------------------------------------
    # Softioc-only mode: generate standalone launcher
    # ------------------------------------------------------------------
    if args.softioc_only:
        if not softioc_data or not softioc_data['tasks']:
            print("## No softioc tasks found")
            return -4

        sioc_prefix = softioc_data['prefix']
        sioc_title = args.title or f"Soft IOC Manager — {sioc_prefix}"
        sioc_screen = screen.Screen(sioc_title, os.path.join(project_dir, args.output))
        sioc_screen.width(args.width)
        sioc_screen.height(args.height)

        # Header
        hdr_grp = widget.Group("header", 0, 0, args.width, DASH_HEADER_H)
        hdr_grp.no_style()
        hdr_bg = widget.Rectangle("hdr-bg", 0, 0, args.width, DASH_HEADER_H)
        hdr_bg.background_color(30, 60, 110)
        hdr_grp.add_widget(hdr_bg)
        hdr_title = widget.Label("hdr-title",
                                  f"Soft IOC Manager — {sioc_prefix}  |  "
                                  f"{len(softioc_data['tasks'])} tasks",
                                  DASH_LEFT_PAD, 8, args.width - 100, 28)
        hdr_title.font_size(20)
        hdr_title.font_style_bold()
        hdr_title.foreground_color(255, 255, 255)
        hdr_grp.add_widget(hdr_title)
        sioc_screen.add_widget(hdr_grp)

        # Body: softioc section
        section_w = args.width - 2 * DASH_LEFT_PAD
        sioc_section, sioc_h = _build_softioc_dashboard_section(
            softioc_data, project_dir, DASH_LEFT_PAD, DASH_HEADER_H + 10, section_w)
        if sioc_section:
            sioc_screen.add_widget(sioc_section)

        sioc_screen.write_screen()
        print(f"\nGenerated {os.path.join(project_dir, args.output)} — '{sioc_title}'")
        return

    # ------------------------------------------------------------------
    # Load & prepare YAML configuration
    # ------------------------------------------------------------------
    with open(args.config, 'r') as f:
        conf = yaml.safe_load(f)
    apply_ioc_defaults(conf)

    if 'epicsConfiguration' not in conf:
        print("## epicsConfiguration not present in configuration")
        return
    if 'iocs' not in conf['epicsConfiguration']:
        print("%% iocs not present in configuration")
        return

    iocs = conf['epicsConfiguration']['iocs']
    beamline = conf.get('beamline', 'epik8s')
    title = args.title or f"{beamline.upper()} Launcher"

    # ------------------------------------------------------------------
    # Setup epik8s-opi widget library
    # ------------------------------------------------------------------
    epik8s_opi_path = _setup_epik8s_opi(project_dir, args)

    # Copy the YAML into the project so that YAML-driven OPIs can read it
    values_dest = os.path.join(project_dir, 'values.yaml')
    shutil.copy2(args.config, values_dest)
    print(f"Copied {args.config} -> {values_dest}")

    # ------------------------------------------------------------------
    # Group IOCs by devgroup
    # ------------------------------------------------------------------
    devgroups = {}  # devgroup -> list of IOCs
    for ioc in iocs:
        if args.controls and ioc.get('name') not in args.controls:
            continue
        dg = ioc.get('devgroup', '')
        if not dg:
            continue
        devgroups.setdefault(dg, []).append(ioc)

    # ------------------------------------------------------------------
    # Build Launcher screen — dashboard layout
    # ------------------------------------------------------------------
    launcher_screen = screen.Screen(title, os.path.join(project_dir, args.output))
    launcher_screen.width(args.width)
    launcher_screen.height(args.height)

    # Header banner
    header = _build_dashboard_header(conf, iocs, args.width)
    launcher_screen.add_widget(header)

    body_h = args.height - DASH_HEADER_H
    body = _build_dashboard_body(conf, iocs, devgroups, epik8s_opi_path,
                                  args.width, body_h)
    launcher_screen.add_widget(body)

    # ------------------------------------------------------------------
    # Add Soft IOC section to the dashboard (optional)
    # ------------------------------------------------------------------
    if softioc_data and softioc_data['tasks']:
        section_w = args.width - 2 * DASH_LEFT_PAD
        sioc_section, sioc_h = _build_softioc_dashboard_section(
            softioc_data, project_dir, DASH_LEFT_PAD,
            args.height - 20, section_w)
        if sioc_section:
            launcher_screen.add_widget(sioc_section)
            # Extend height to accommodate the new section
            launcher_screen.height(args.height + sioc_h)
            print(f"\n+ Soft IOCs: {len(softioc_data['tasks'])} tasks")

    # Print summary
    for devgroup in _ordered_devgroups(devgroups):
        dg_iocs = devgroups[devgroup]
        reg = DEVGROUP_REGISTRY.get(devgroup, {})
        label = reg.get('label', devgroup.title())
        n_dev = sum(1 for _ in _flatten_devices(dg_iocs))
        entries = _collect_per_device_entries(reg, devgroup, dg_iocs, epik8s_opi_path)
        print(f"+ {label} [{devgroup}]: {len(dg_iocs)} IOCs, "
              f"{n_dev} devices, {len(entries)} open-in-window buttons")
    launcher_screen.write_screen()
    print(f"\nGenerated {os.path.join(project_dir, args.output)} — '{title}'")

    # ------------------------------------------------------------------
    # Generate detailed launcher (optional)
    # ------------------------------------------------------------------
    if args.detailed:
        print("\n--- Detailed launcher ---")
        _generate_detailed_launcher(conf, iocs, args, project_dir)

    # ------------------------------------------------------------------
    # Generate settings.ini (optional)
    # ------------------------------------------------------------------
    if args.generate_settings_ini:
        _generate_settings(conf, script_dir, project_dir)


if __name__ == "__main__":
    main_opigen()
