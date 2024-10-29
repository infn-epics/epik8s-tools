import yaml
import os
import shutil
import jinja2
from jinja2 import Environment, FileSystemLoader
from collections import OrderedDict
import argparse
__version__ = "0.2.14"

def render_template(template_path, context):
    """Render a Jinja2 template with the given context."""
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(os.path.dirname(template_path)))
    template = env.get_template(os.path.basename(template_path))
    return template.render(context)

def load_values_yaml(fil, script_dir):
    """Load the values.yaml file from the same directory as the script."""
    values_yaml_path = os.path.join(script_dir, fil)

    with open(values_yaml_path, 'r') as file:
        values = yaml.safe_load(file)
    return values

def generate_readme(yaml_data, dir, output_file):
    """Render the Jinja2 template using YAML data and write to README.md."""
    env = Environment(loader=FileSystemLoader(searchpath=dir))
    template = env.get_template('README.md')
    rendered_content = template.render(yaml_data)
    with open(output_file, 'w') as f:
        f.write(rendered_content)

def create_directory_tree(project_name):
    print(f"* create {project_name} tree")
    for dir in ["config/applications", "config/cronjobs", "config/iocs", "config/services", "deploy/templates", 
                "opi/ini", "opi/common", "opi/rf", "opi/diag", "opi/mag", "opi/vac", "opi/tim"]:
        os.makedirs(f'{project_name}/{dir}', exist_ok=True)
        with open(f'{project_name}/{dir}/.gitignore', 'a') as file:
            pass

def create_chart_yaml(project_name, output_dir):
    """Create Chart.yaml file."""
    chart_content = f"""
apiVersion: v2
name: {project_name}-chart
version: 1.0.1
"""
    with open(os.path.join(output_dir, 'Chart.yaml'), 'w') as file:
        file.write(chart_content)

def represent_ordereddict(dumper, data):
    return dumper.represent_dict(data.items())

yaml.add_representer(OrderedDict, represent_ordereddict)

def create_values_yaml(fil, values, output_dir):
    """Write the values.yaml file while preserving order."""
    with open(os.path.join(output_dir, fil), 'w') as file:
        file.write(values)

def copy_corresponding_directories(values, script_dir, project_name):
    directories = {
        "iocs": f"{project_name}/config/iocs",
        "services": f"{project_name}/config/services",
        "applications": f"{project_name}/config/applications"
    }

    for key, target_dir in directories.items():
        if key in values:
            for entry in values[key]:
                dir = key
                if isinstance(entry, str):
                    dir += "/" + entry
                else:
                    if 'iocdir' in entry:
                        dir += "/" + entry['iocdir']
                        entry = entry['iocdir']
                    elif 'name' in entry:
                        dir += "/" + entry['name']
                        entry = entry['name']
                    
                source_dir = os.path.join(script_dir, dir)
                if os.path.isdir(source_dir):
                    shutil.copytree(source_dir, os.path.join(target_dir, entry), dirs_exist_ok=True)
                    print(f"Copied directory {entry} to {target_dir}")

def create_project(project_name, replacements):
    script_dir = os.path.dirname(os.path.realpath(__file__)) + "/template/"
    rendered_values = render_template(script_dir + 'values.yaml', replacements)
    rendered_deploy = render_template(script_dir + 'deploy.yaml', replacements)
    rendered_settings = render_template(script_dir + 'settings.ini', replacements)
    
    create_directory_tree(project_name)
    values = yaml.safe_load(rendered_values)
    values['iocs'] = values['epicsConfiguration']['iocs']
    values['services'] = values['epicsConfiguration']['services']
    copy_corresponding_directories(values, script_dir, project_name)
    create_chart_yaml(project_name, f'{project_name}/deploy')
    create_values_yaml('values.yaml', rendered_values, f'{project_name}/deploy')
    create_values_yaml('settings.ini', rendered_settings, f'{project_name}/opi')
    shutil.copy(script_dir + 'epik8s.yaml', f'{project_name}/deploy/templates')
    create_values_yaml(replacements['beamline'] + "-k8s-application.yaml", rendered_deploy, f'{project_name}/')
    generate_readme(values, script_dir, f"{project_name}/README.md")

def main():
    parser = argparse.ArgumentParser(description="Generate project structure and Helm charts")
    parser.add_argument("--version", action="store_true", help="Show the version and exit")  # Add this option

    parser.add_argument("project_name", nargs="?",help="Name of the project")
    parser.add_argument("--beamline", default=None, help="Beamline Name value")
    parser.add_argument("--namespace", default=None, help="Namespace for beamline")
    parser.add_argument("--targetRevision", default="experimental", help="Target revision")
    parser.add_argument("--serviceAccount", default="default", help="Service account")
    parser.add_argument("--beamlinerepogit", help="Git beamline URL")
    parser.add_argument("--beamlinereporev", default="main", help="Git revision")
    parser.add_argument("--iocbaseip", default=None, help="IOC enable static IP address provide CIDR: ie.: 10.152.183.0/24")
    parser.add_argument("--iocstartip", default="2", help="IOC start IP enable static ioc addressing")
    parser.add_argument("--cagatewayip", default=None, help="Load balancer CA Gateway IP")
    parser.add_argument("--pvagatewayip", default=None, help="Load balancer PVA Gateway IP")
    parser.add_argument("--dnsnamespace", help="DNS/IP required for ingress definition")
    parser.add_argument("--nfsserver", default=None, help="NFS Server")
    parser.add_argument("--nfsdirdata", default="/epik8s/data", help="NFS data partition")
    parser.add_argument("--nfsdirautosave", default="/epik8s/autosave", help="NFS autosave partition")
    parser.add_argument("--nfsdirconfig", default="/epik8s/config", help="NFS config partition")
    parser.add_argument("--backend", default=None, help="Activate backend services")
    parser.add_argument("--openshift", default=False, help="Activate openshift flag")

    args = parser.parse_args()

    if args.version:
        print(f"epik8s-tools version {__version__}")
        return

    if not args.beamlinerepogit:
        print("# You must provide a valid beamlinerepogit")
        return
    if not args.dnsnamespace:
        print("# You must provide a valid dnsnamespace")
        return
    
    if not args.cagatewayip:
        print("%% No cagatewayip provided your CA PVs cannot read outside the cluster")
        return
    
    if not args.pvagatewayip:
        print("%% No pvagatewayip provided your PVA PVs cannot read outside the cluster")
        return
    
    if not args.iocbaseip:
        print("%% No iocbaseip provided your IOC can change IPs, you probably must restart gateway each IOC restart")
        return
        
    if not args.beamline:
        args.beamline = args.project_name
    if not args.namespace:
        args.namespace = args.beamline

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
        "cagatewayip": args.cagatewayip,
        "pvagatewayip": args.pvagatewayip,
        "nfsserver": args.nfsserver,
        "nfsdirdata": args.nfsdirdata,
        "nfsdirautosave": args.nfsdirautosave,
        "nfsdirconfig": args.nfsdirconfig,
        "backend": args.backend,
        "openshift": args.openshift
    }

    create_project(args.project_name, replacements)
    
if __name__ == "__main__":
    main()
