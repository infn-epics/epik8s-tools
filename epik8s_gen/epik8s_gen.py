import yaml
import os
import shutil
import jinja2
from collections import OrderedDict

def render_template(template_path, context):
    """Render a Jinja2 template with the given context."""
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(os.path.dirname(template_path)))
    template = env.get_template(os.path.basename(template_path))
    return template.render(context)


def load_values_yaml(fil,script_dir):
    """Load the values.yaml file from the same directory as the script."""
    values_yaml_path = os.path.join(script_dir, fil)  # Path to the values.yaml in the same directory

    with open(values_yaml_path, 'r') as file:
        values = yaml.safe_load(file)
    return values

def replace_placeholders(values, replacements):
    """Recursively replace placeholders in a nested dictionary."""
    if isinstance(values, dict):
        return {k: replace_placeholders(v, replacements) for k, v in values.items()}
    elif isinstance(values, list):
        return [replace_placeholders(i, replacements) for i in values]
    elif isinstance(values, str):
        # Replace {placeholders} in string
        for placeholder, replacement in replacements.items():
            values = values.replace(f"<{placeholder}>", replacement)
        return values
    else:
        return values

def create_directory_tree(project_name):
    print (f"* create {project_name} tree")
    """Create the directory structure."""
    os.makedirs(f'{project_name}/config/applications', exist_ok=True)
    os.makedirs(f'{project_name}/config/cronjobs', exist_ok=True)
    os.makedirs(f'{project_name}/config/iocs', exist_ok=True)
    os.makedirs(f'{project_name}/config/services', exist_ok=True)
    os.makedirs(f'{project_name}/deploy/templates', exist_ok=True)
    os.makedirs(f'{project_name}/opi', exist_ok=True)

def create_chart_yaml(project_name, output_dir):
    """Create Chart.yaml file."""
    chart_content = f"""
apiVersion: v2
name: {project_name}-chart
version: 1.0.1
"""
    with open(os.path.join(output_dir, 'Chart.yaml'), 'w') as file:
        file.write(chart_content)

# Custom representer to handle OrderedDict
def represent_ordereddict(dumper, data):
    return dumper.represent_dict(data.items())

yaml.add_representer(OrderedDict, represent_ordereddict)

def create_values_yaml(fil,values, output_dir):
   
    """Write the values.yaml file while preserving order."""
    with open(os.path.join(output_dir, fil), 'w') as file:
        # yaml.dump(values, file, Dumper=yaml.SafeDumper, default_flow_style=False, sort_keys=False)
        file.write(values)

def copy_corresponding_directories(values, script_dir, project_name):
    """
    Copy directories for iocs, services, and applications from script directory
    to the corresponding target directories.
    """
    values_copy={}
    values_copy['applications']=values['applications']
    values_copy['iocs']=values['epicsConfiguration']['iocs']
    values_copy['services']=values['epicsConfiguration']['services']

    directories = {
        "iocs": f"{project_name}/config/iocs",
        "services": f"{project_name}/config/services",
        "applications": f"{project_name}/config/applications"
    }

    for key, target_dir in directories.items():
        if key in values_copy:
            for entry in values_copy[key]:
                dir=key
                if isinstance(entry,str):
                    dir+="/"+entry
                else:
                    if 'iocdir' in entry:
                        dir+="/"+entry['iocdir']
                        entry=entry['iocdir']
                    elif 'name' in entry:
                        dir+="/"+entry['name']
                        entry=entry['name']
                    
                source_dir = os.path.join(script_dir, dir)
                if os.path.isdir(source_dir):
                    # If the directory exists, copy it to the target directory
                    shutil.copytree(source_dir, os.path.join(target_dir, entry), dirs_exist_ok=True)
                    print(f"Copied directory {entry} to {target_dir}")

def main(project_name, replacements):
    # Load values from the fixed location of values.yaml
    script_dir = os.path.dirname(os.path.realpath(__file__))+"/template/"

    # values = load_values_yaml('values.yaml',script_dir)

    # Replace placeholders with actual values from arguments
    # values = replace_placeholders(values, replacements)
    rendered_values = render_template(script_dir+'values.yaml', replacements)

    # deploy_values = load_values_yaml('deploy.yaml',script_dir)
    rendered_deploy = render_template(script_dir+'deploy.yaml', replacements)

    # Replace placeholders with actual values from arguments
    # deploy_values = replace_placeholders(deploy_values, replacements)
    # Create the directory structure
    create_directory_tree(project_name)

    # Get the directory of the script (where values.yaml is located)

    values = yaml.safe_load(rendered_values)  # You may need to adapt this if the format is YAML
    #deploy_values = yaml.safe_load(rendered_deploy)  # You may need to adapt this if the format is YAML

    # Copy corresponding directories for iocs, services, and applications
    copy_corresponding_directories(values, script_dir, project_name)

    # Create Chart.yaml file
    create_chart_yaml(project_name, f'{project_name}/deploy')

    # Create updated values.yaml file while preserving order
    create_values_yaml('values.yaml',rendered_values, f'{project_name}/deploy')
    create_values_yaml(replacements['beamline']+"-k8s-application.yaml",rendered_deploy, f'{project_name}/')


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate project structure and Helm charts")
    parser.add_argument("project_name", help="Name of the project")
    
    # Add command-line arguments for placeholder replacements
    parser.add_argument("--beamline", default=None, help="Beamline Name value")
    parser.add_argument("--namespace", default=None, help="Namespace for beamline")
    parser.add_argument("--targetRevision", default="1.0.0", help="Target revision")
    parser.add_argument("--serviceAccount", default="default", help="Service account")
    parser.add_argument("--beamlinerepogit", required=True, help="Git beamline URL")
    parser.add_argument("--beamlinereporev", default="main", help="Git revision")
    parser.add_argument("--iocbaseip", default=None, help="IOC base IP enable static ioc addressing")
    parser.add_argument("--iocstartip", default=None, help="IOC start IP enable static ioc addressing")
    parser.add_argument("--iociprange", default="65536", help="IOC IP range")
    parser.add_argument("--cagatewayip", default=None, help="Load balancer CA Gateway IP")
    parser.add_argument("--pvagatewayip", default=None, help="Load balancer PVA Gateway IP")
    parser.add_argument("--dnsnamespace", required=True, help="DNS/IP required for ingress definition")
    parser.add_argument("--nfsserver", default=None, help="NFS Server")
    parser.add_argument("--nfsdirdata", default="/epik8s/data", help="NFS data partition")
    parser.add_argument("--nfsdirautosave", default="/epik8s/autosave", help="NFS autosave partition")
    parser.add_argument("--nfsdirconfig", default="/epik8s/config", help="NFS config partition")
    parser.add_argument("--backend", default=None, help="Activate backend services")
    parser.add_argument("--openshift", default=False, help="Activate openshift flag")

    
    args = parser.parse_args()
    if not args.beamline:
        args.beamline=args.project_name


    if not args.namespace:
        args.namespace=args.beamline
        
    # Create a dictionary for replacements
    replacements = {
        "beamline": args.beamline,
        "namespace": args.namespace,
        "dnsnamespace": args.dnsnamespace,
        "targetRevision": args.targetRevision,
        "serviceAccount": args.serviceAccount,
        "beamlinerepogit": args.beamlinerepogit,
        "beamlinereporev": args.beamlinereporev,
        "iocbaseip": args.iocbaseip,
        "iocstartip": args.iocstartip,
        "iociprange": args.iociprange,
        "cagatewayip": args.cagatewayip,
        "pvgatewayip": args.pvagatewayip,
        "nfsserver": args.nfsserver,
        "nfsdirdata": args.nfsdirdata,
        "nfsdirautosave": args.nfsdirautosave,
        "nfsdirconfig": args.nfsdirconfig,
        "backend": args.backend,
        "openshift": args.openshift

    }

    main(args.project_name, replacements)
