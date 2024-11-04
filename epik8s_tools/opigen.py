import yaml
import argparse
import subprocess
import os
from epik8s_gen import render_template,create_values_yaml
from phoebusgen import screen as screen
from phoebusgen import widget as widget

def main_opigen():
    script_dir = os.path.dirname(os.path.realpath(__file__)) + "/template/"

    # Set up command-line argument parsing
    parser = argparse.ArgumentParser(description="Generate a Phoebus display with tabs from a EPIK8s YAML configuration.",
    formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument(
        "--yaml",
        type=str,
        required=True,
        help="Path to the EPIK8s YAML configuration file."
    )
    parser.add_argument(
        "--output",
        type=str,
        default="launcher.bob",
        help="Output path for the generated display"
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
    args = parser.parse_args()
    parent_path = os.path.dirname(args.output) or "."  # Default to "." if no directory

    # Load YAML configuration
    with open(args.yaml, 'r') as f:
        conf = yaml.safe_load(f)
    if not 'epicsConfiguration' in conf:
        print("## epicsConfiguration not present in configuration")
        return
    if not 'iocs' in conf['epicsConfiguration']:
        print("%% iocs not present in configuration")
        return
    config=conf['epicsConfiguration']['iocs']
    # Clone each unique OPI URL if it hasn’t been cloned already
    cloned_urls = set()
    for device in config:
        if not 'opi' in device or not 'url' in device['opi']:
            continue
        
        opi_section = device.get('opi', {})
        opi_url = opi_section.get('url')
        
        if opi_url and opi_url not in cloned_urls:
            clone_path = os.path.join(args.clone_dir, os.path.basename(opi_url))
            if not os.path.exists(clone_path):
                print(f"Cloning {opi_url} into {clone_path}")
                subprocess.run(["git", "clone", opi_url, clone_path,"--recurse-submodules"])
            else:
                print(f"Repository {opi_url} already cloned in {clone_path}.")
            cloned_urls.add(opi_url)
    # Create Phoebus screen with specified dimensions and NavigationTabs for tab layout
    launcher_screen = screen.Screen(args.title, args.output)
    launcher_screen.width(args.width)
    launcher_screen.height(args.height)
    group_taps = widget.Tabs("group",0,0,args.width,args.height)
    

    # Group devices by 'devgroup'
    devgroups = {}
    devtypegroup = {}
    for device in config:
        if not 'devgroup' in device:
            devgroup="ukngroup"
            device['devgroup']="ukngroup"

        else:
            devgroup = device.get('devgroup')

        if not 'devtype' in device:
            devtype="ukntype"
            device['devtype']="ukntype"
        else:
            devtype = device.get('devtype')

        if devgroup not in devgroups:
            devgroups[devgroup] = {}
            devgroups[devgroup][devtype]=[]
            
        if 'opi' in device and 'url' in device['opi'] and 'main' in device['opi']:
            devgroups[devgroup][devtype].append(device)

    

    # Loop over each devgroup and create a tab for it
    for devgroup in devgroups:
        # Create a tab for each devgroup
        devgroup_tab = group_taps.tab(devgroup)
        type_tabs = widget.Tabs(f"tab-{devgroup}-type",0,0,args.width,args.height)

        for devtype in devgroups[devgroup]:
            if len(devgroups[devgroup][devtype])==0:
                print(f"no device {devtype} of group {devgroup}")
                continue
            type_tabs.tab_direction_vertical()
            type_tabs.tab(f"{devtype}")

            nav_tab = widget.NavigationTabs(f"nav-{devgroup}-{devtype}",0,0,args.width,args.height)

            for device in devgroups[devgroup][devtype]:
                # Extract opi section and macros
                
                opi_section = device.get('opi', {})
                main_bob = opi_section.get('main')
                macros = opi_section.get('macro', [])
                opi_url = opi_section.get('url')

                macro_values = {macro['name']: macro['value'] for macro in macros}
                
                # Set the action path to the cloned directory
                action_path = os.path.join(args.clone_dir, os.path.basename(opi_url), main_bob)

                # Add an action button to call the .bob file with macros in each tab
                print(f"* adding {device['name']} group: {devgroup} type: {devtype} opi {action_path} to nav")
                nav_tab.tab(f"{device['name']}",action_path,devgroup,macro_values)
            print(f"* adding nav to tab {devtype}")
            type_tabs.add_widget(f"{devtype}",nav_tab)
        print(f"* adding tab {devtype} to tab {devgroup}")
        group_taps.add_widget(f"{devgroup}",type_tabs)
   

        # Add the devgroup tab to NavigationTabs
        # nav_tabs.add_tab(devgroup_tab)

    # Add NavigationTabs to the screen and save
    launcher_screen.add_widget(group_taps)
    launcher_screen.write_screen()
    print(f"Generated Phoebus launcher with tabs at {args.output} titled '{args.title}'")
    if not 'gateway' in conf['epicsConfiguration']['services'] and not 'loadbalancer' in conf['epicsConfiguration']['services']['gateway']:
        print("%% no cagateway service with loadbalancer no connection with cluster possible")
        conf['cagatewayip']= None
    else:
        conf['cagatewayip']=conf['epicsConfiguration']['services']['gateway']['loadbalancer']
    
    if not 'pvagateway' in conf['epicsConfiguration']['services'] and not 'loadbalancer' in conf['epicsConfiguration']['services']['pvagateway']:
        print("%% no pvagateway service with loadbalancer specified no PVA gateway")
        conf['pvagatewayip']=None
    else:
        conf['pvagatewayip']=conf['epicsConfiguration']['services']['pvagateway']['loadbalancer']

        
    replacements = {
        "beamline": conf['beamline'],
        "namespace": conf['namespace'],
        "dnsnamespace": conf['epik8namespace'],
        "cagatewayip": conf['cagatewayip'],
        "pvagatewayip": conf['pvagatewayip'],
        
    }
    rendered_settings = render_template(script_dir + 'settings.ini', replacements)
    
    create_values_yaml('phoebus_settings.ini', rendered_settings, f'{parent_path}/')
    print(f"* created {parent_path}/phoebus_settings.ini")
if __name__ == "__main__":
    main_opigen()
