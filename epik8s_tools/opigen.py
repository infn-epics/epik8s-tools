import yaml
import argparse
import subprocess
import os
import ast
import shutil  # For removing directories
from epik8s_tools.epik8s_gen import render_template,create_values_yaml,generate_readme
from jinja2 import Template

from phoebusgen import screen as screen
from phoebusgen import widget as widget
from epik8s_tools.epik8s_version import __version__

def main_opigen():
    script_dir = os.path.dirname(os.path.realpath(__file__)) + "/template/"

    # Set up command-line argument parsing
    parser = argparse.ArgumentParser(description="Generate a Phoebus display with tabs from a EPIK8s YAML configuration.",
    formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument(
        "--yaml",
        type=str,
        help="Path to the EPIK8s YAML configuration file."
    )
    parser.add_argument("--version", action="store_true", help="Show the version and exit")  # Add this option

    parser.add_argument(
        "--output",
        type=str,
        default="Launcher.bob",
        help="Main opi name"
    )
    parser.add_argument(
        "--title",
        type=str,
        default="Test Launcher",
        help="Title for the launcher"
    )
    parser.add_argument(
        "--clone-dir",
        type=str,
        default="opi-repos",
        help="Directory to clone the OPI GIT REPOS"
    )
    parser.add_argument(
        "--projectdir",
        type=str,
        help="Directory where all project files will be generated"
    )
    parser.add_argument(
        "--width",
        type=int,
        default=1900,
        help="Width of the launcher screen (default: 1900)"
    )
    parser.add_argument(
        "--height",
        type=int,
        default=1400,
        help="Height of the launcher screen (default: 1400)"
    )
    
    parser.add_argument('--controls', nargs='+', help="Include just the given controls (default ALL).")

    args = parser.parse_args()
    if args.version:
        print(f"epik8s-tools version {__version__}")
        return

    if not args.yaml:
        print(f"# must define a valid epik8s configuration yaml --yaml <configuration>")
        return -1
    
    if not args.projectdir:
        print(f"# must define an output projectdir --projectdir <project output directory>")
        return -2
    # Define the project directory to store all generated files
    project_dir = os.path.abspath(args.projectdir)
    if not os.path.exists(project_dir):
        os.makedirs(project_dir)
        print(f"Created project directory: {project_dir}")
    

    # Load YAML configuration
    with open(args.yaml, 'r') as f:
        conf = yaml.safe_load(f)
    if not 'epicsConfiguration' in conf:
        print("## epicsConfiguration not present in configuration")
        return
    if not 'iocs' in conf['epicsConfiguration']:
        print("%% iocs not present in configuration")
        return
    config = conf['epicsConfiguration']['iocs']
    for device in config:
        if 'opi' in device and not isinstance(device['opi'], str):
            print(f"## opi field in device {device['name']} is not a string")
            return
        if 'opi' in device and not device['opi'] in conf['opi']:
            print(f"## opi '{device['opi']}' in '{device['name']}', not found in {args.yaml}")
            return
    config = [device for device in config if 'opi' in device and 'url' in conf['opi'][device['opi']]]
    config = [device for device in config if args.controls==None or device['name'] in args.controls]

    # Clone each unique OPI URL if it hasn’t been cloned already
    cloned_urls = set()
    for device in config:  
        opidesc=conf['opi'][device['opi']]
        if 'iocparam' in device:
            for p in device['iocparam']:
                device[p['name']]=p['value']
        device['beamline']=conf['beamline']         
        templ = Template(str(opidesc))
        opi_section=ast.literal_eval(templ.render(device))
        device['opidesc']=opi_section
        opi_url = opi_section.get('url')
        clonedirfull = os.path.join(project_dir, args.clone_dir)

        if opi_url and opi_url not in cloned_urls:
            clone_path = os.path.join(clonedirfull, os.path.basename(opi_url))
            if not os.path.exists(clone_path):
                print(f"Cloning {opi_url} into {clone_path}")
                subprocess.run(["git", "clone", "--depth","1", opi_url, clone_path,"--recurse-submodules"])
            else:
                print(f"Repository {opi_url} already cloned in {clone_path}.")
            
            # Remove the .git directory to strip Git history
            git_dir = os.path.join(clone_path, ".git")
            if os.path.exists(git_dir):
                print(f"Removing .git directory from {clone_path}")
                shutil.rmtree(git_dir)

            cloned_urls.add(opi_url)

    # Create Phoebus screen with specified dimensions and NavigationTabs for tab layout
    launcher_screen = screen.Screen(args.title, os.path.join(project_dir, args.output))
    launcher_screen.width(args.width)
    launcher_screen.height(args.height)
    group_taps = widget.Tabs("group", 0, 0, args.width, args.height)

    # Group devices by 'devgroup'
    devgroups = {}
    devtypegroup = {}
    for device in config:
        if not 'devgroup' in device:
            devgroup = "ukngroup"
            device['devgroup'] = "ukngroup"
        else:
            devgroup = device.get('devgroup')

        if not 'devtype' in device:
            devtype = "ukntype"
            device['devtype'] = "ukntype"
        else:
            devtype = device.get('devtype')

        if devgroup not in devgroups:
            devgroups[devgroup] = {}
            devgroups[devgroup][devtype] = []
        
        if devtype not in devgroups[devgroup]:
            devgroups[devgroup][devtype] = []
          
        if 'opi' in device and 'url' in device['opidesc'] and 'main' in device['opidesc']:
            devgroups[devgroup][devtype].append(device)

    # Loop over each devgroup and create a tab for it
    for devgroup in devgroups:
        # Create a tab for each devgroup
        devgroup_tab = group_taps.tab(devgroup)
        type_tabs = widget.Tabs(f"tab-{devgroup}-type", 0, 0, args.width, args.height)

        for devtype in devgroups[devgroup]:
            if len(devgroups[devgroup][devtype]) == 0:
                print(f"no device {devtype} of group {devgroup}")
                continue
            type_tabs.tab_direction_vertical()
            type_tabs.tab(f"{devtype}")

            nav_tab = widget.NavigationTabs(f"nav-{devgroup}-{devtype}", 0, 0, args.width, args.height)

            for device in devgroups[devgroup][devtype]:
                # Extract opi section and macros
                opi_section = device.get('opidesc', {})
                main_bob = opi_section.get('main')
                macros = opi_section.get('macro', [])
                opi_url = opi_section.get('url')

                macro_values = {macro['name']: macro['value'] for macro in macros}

                # Set the action path to the cloned directory
                action_path = os.path.join(args.clone_dir, os.path.basename(opi_url), main_bob)

                # Add an action button to call the .bob file with macros in each tab
                print(f"* adding {device['name']} group: {devgroup} type: {devtype} opi {action_path} to nav")
                nav_tab.tab(f"{device['name']}", action_path, devgroup, macro_values)
            print(f"* adding nav to tab {devtype}")
            type_tabs.add_widget(f"{devtype}", nav_tab)
        print(f"* adding tab {devtype} to tab {devgroup}")
        group_taps.add_widget(f"{devgroup}", type_tabs)

    # Add NavigationTabs to the screen and save
    launcher_screen.add_widget(group_taps)
    launcher_screen.write_screen()
    print(f"Generated Phoebus launcher with tabs at {os.path.join(project_dir, args.output)} titled '{args.title}'")
    
    if not 'gateway' in conf['epicsConfiguration']['services'] and not 'loadbalancer' in conf['epicsConfiguration']['services']['gateway']:
        print("%% no cagateway service with loadbalancer no connection with cluster possible")
        conf['cagatewayip'] = None
    else:
        conf['cagatewayip'] = conf['epicsConfiguration']['services']['gateway']['loadbalancer']
    
    if not 'pvagateway' in conf['epicsConfiguration']['services'] and not 'loadbalancer' in conf['epicsConfiguration']['services']['pvagateway']:
        print("%% no pvagateway service with loadbalancer specified no PVA gateway")
        conf['pvagatewayip'] = None
    else:
        conf['pvagatewayip'] = conf['epicsConfiguration']['services']['pvagateway']['loadbalancer']

    replacements = {
        "beamline": conf['beamline'],
        "namespace": conf['namespace'],
        "dnsnamespace": conf['epik8namespace'],
        "cagatewayip": conf['cagatewayip'],
        "pvagatewayip": conf['pvagatewayip'],
    }
    rendered_settings = render_template(script_dir + 'settings.ini', replacements)

    create_values_yaml('settings.ini', rendered_settings, f'{project_dir}/')
    print(f"* created {project_dir}/settings.ini")
    generate_readme(conf, script_dir, f"{project_dir}/README.md")

if __name__ == "__main__":
    main_opigen()
