import yaml
import os
import ast
import shutil
import jinja2
from jinja2 import Environment, FileSystemLoader,Template
from collections import OrderedDict
import argparse
from datetime import datetime
from epik8s_tools.epik8s_version import __version__

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

def generate_readme(values, dir, output_file):
    """Render the Jinja2 template using YAML data and write to README.md."""
    yaml_data=values
    yaml_data['iocs'] = values['epicsConfiguration']['iocs']
    yaml_data['services'] = values['epicsConfiguration']['services']
    if 'gateway' in yaml_data['services'] and 'loadbalancer' in yaml_data['services']['gateway']:
        yaml_data['cagatewayip']=yaml_data['services']['gateway']['loadbalancer']
    if 'pvagateway' in yaml_data['services'] and 'loadbalancer' in yaml_data['services']['pvagateway']:
        yaml_data['pvagatewayip']=yaml_data['services']['pvagateway']['loadbalancer']
    yaml_data['version'] = __version__
    yaml_data['time'] = datetime.today().date()
    env = Environment(loader=FileSystemLoader(searchpath=dir))
    template = env.get_template('README.md')
    for ioc in yaml_data['iocs']:
        if 'opi' in ioc and ioc['opi'] in yaml_data['opi']:
            opi=yaml_data['opi'][ioc['opi']]
            temp = Template(str(opi))
            rendered=ast.literal_eval(temp.render(ioc))
            ioc['opinfo']=rendered
            
            if 'macro' in rendered:
                acc=""
                for m in rendered['macro']:
                    acc=m['name']+"="+m['value']+" "+acc
                ioc['opinfo']['macroinfo']=acc
   
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
                    elif 'gitpath' in entry:
                        dir += "/" + entry['gitpath']
                        entry = entry['gitpath']
                    elif 'name' in entry:
                        dir += "/" + entry['name']
                        entry = entry['name']
                    
                        
                    
                source_dir = os.path.join(script_dir, dir)
                if os.path.isdir(source_dir):
                    shutil.copytree(source_dir, os.path.join(target_dir, entry), dirs_exist_ok=True)
                    #print(f"Copied directory {entry} to {target_dir}")

def create_project(project_name, replacements):
    script_dir = os.path.dirname(os.path.realpath(__file__)) + "/template/"
    rendered_values = render_template(script_dir + 'values.yaml', replacements)
    rendered_deploy = render_template(script_dir + 'deploy.yaml', replacements)
    rendered_settings = render_template(script_dir + 'settings.ini', replacements)
    
    create_directory_tree(project_name)
    values = yaml.safe_load(rendered_values)
    values['iocs'] = values['epicsConfiguration']['iocs']
    values['services'] = values['epicsConfiguration']['services']
    values['cagatewayip']=replacements['cagatewayip']
    values['pvagatewayip']=replacements['pvagatewayip']
    values['version'] = replacements['version']
    values['time'] = replacements['time']

    copy_corresponding_directories(values, script_dir, project_name)
    create_chart_yaml(project_name, f'{project_name}/deploy')
    create_values_yaml('values.yaml', rendered_values, f'{project_name}/deploy')
    create_values_yaml('settings.ini', rendered_settings, f'{project_name}/opi')
    shutil.copy(script_dir + 'epik8s.yaml', f'{project_name}/deploy/templates')
    create_values_yaml(replacements['beamline'] + "-k8s-application.yaml", rendered_deploy, f'{project_name}/')
    generate_readme(values, script_dir, f"{project_name}/README.md")

def main():
    parser = argparse.ArgumentParser(description="Generate project structure and Helm charts",
    formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--version", action="store_true", help="Show the version and exit")  # Add this option

    parser.add_argument("project_name", nargs="?",help="Name of the project")
    parser.add_argument("--beamline", default=None, help="Beamline Name value")
    parser.add_argument("--namespace", default=None, help="Namespace for beamline")
    parser.add_argument("--targetRevision", default="experimental", help="Target revision")
    parser.add_argument("--serviceAccount", default="default", help="Service account")
    parser.add_argument("--beamlinerepogit", help="Git beamline URL")
    parser.add_argument("--beamlinereporev", default="main", help="Git revision")
    parser.add_argument("--iocbaseip", default=None, help="IOC enable static IP address provide K8S Service CIDR: ie.: 10.152.183.0/24 (microk8s), 10.96.0.0/12 (i.e k8s vanilla)")
    parser.add_argument("--iocstartip", default="2", help="IOC start IP enable static ioc addressing")
    parser.add_argument("--cagatewayip", default=None, help="Load balancer CA Gateway IP")
    parser.add_argument("--pvagatewayip", default=None, help="Load balancer PVA Gateway IP")
    parser.add_argument("--dnsnamespace", help="DNS/IP required for ingress definition")
    parser.add_argument("--mysqlchart", action="store_true", help="use mysql custom chart, instead of bitnami (microk8s)")
    parser.add_argument("--channelfinder", action="store_true", help="enable channelfinder and chfeeder")
    parser.add_argument("--token", default="", help="GIT personal token, empty unautheticated")


    parser.add_argument(
    "--ingressclass", 
    choices=["haproxy", "nginx", ""], 
    default="", 
    help="Ingress class name: haproxy, nginx, or empty for no ingress class"
)

    parser.add_argument("--nfsserver", default=None, help="NFS Server")
    parser.add_argument("--nfsdirdata", default="/epik8s/data", help="NFS data partition")
    parser.add_argument("--nfsdirautosave", default="/epik8s/autosave", help="NFS autosave partition")
    parser.add_argument("--nfsdirconfig", default="/epik8s/config", help="NFS config partition")
    parser.add_argument("--elasticsearch", default=None, help="Elastic Search server")
    parser.add_argument("--mongodb", default=None, help="MongoDB server")
    parser.add_argument("--kafka", default=None, help="Kafka server")
    parser.add_argument("--vcams", default=1, type=int, help="Generate a number of simulated cams")
    parser.add_argument("--vicpdas", default=1, type=int, help="Generate a number of simulated icpdas")
    parser.add_argument("--vquad", default=1, type=int, help="Generate a number of simulated quadrupoles")
    parser.add_argument("--vcor", default=1, type=int, help="Generate a number of simulated correctors")
    parser.add_argument("--vdip", default=1, type=int, help="Generate a number of simulated dipoles")
    parser.add_argument("--vbpm", default=1, type=int, help="Generate a number of simulated bpms")
    parser.add_argument("--vmot", default=1, type=int, help="Generate a number of simulated motors")
    parser.add_argument("--vgac", default=1, type=int, help="Generate a number of simulated vacuum gauges")
    parser.add_argument("--vvpc", default=1, type=int, help="Generate a number of simulated vacuum pumps")

    parser.add_argument("--openshift", default=False, help="Activate openshift flag")

    args = parser.parse_args()

    if args.version:
        print(f"epik8s-tools version {__version__}")
        return

    if not args.beamlinerepogit:
        print("# You must provide a valid beamlinerepogit")
        return -1
    if not args.dnsnamespace:
        print("# You must provide a valid dnsnamespace")
        return -2
    
    if not args.cagatewayip:
        print("%% No cagatewayip provided your CA PVs cannot read outside the cluster")
        
    
    if not args.pvagatewayip:
        print("%% No pvagatewayip provided your PVA PVs cannot read outside the cluster")
        
    
    if not args.iocbaseip:
        print("%% No iocbaseip provided your IOC can change IPs, you probably must restart gateway each IOC restart")
        
        
    if not args.beamline:
        print(f"# No beamline provided")
        return -3


    if not args.namespace:
        args.namespace = args.beamline


    print(f"* Beamline: {args.beamline}")
    print(f"* Namespace: {args.namespace}")
    print(f"* Project: {args.project_name}")
    print(f"* Beamline Repo: {args.beamlinerepogit} ({args.beamlinereporev})")
    print(f"* Service DNS: {args.dnsnamespace}")
    print(f"* EPIK8s charts default revision: {args.targetRevision}")
    print(f"* CA Gateway: {args.cagatewayip}")
    print(f"* PVA Gateway: {args.pvagatewayip}")
    
    print("\n")
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
        "kafka": args.kafka,
        "elasticsearch": args.elasticsearch,
        "mongodb": args.mongodb,
        "ingressClassName": args.ingressclass,
        "openshift": args.openshift,
        "vcams": args.vcams,
        "vicpdas": args.vicpdas,
        "vquad": args.vquad,
        "vcor": args.vcor,
        "vdip": args.vdip,
        "vbpm": args.vbpm,
        "vmot": args.vmot,
        "vgac": args.vgac,
        "vvpc": args.vvpc,
        "application": __name__,
        "version": __version__,
        "mysqlchart":args.mysqlchart,
        "channelfinder":args.channelfinder,
        "time": datetime.today().date(),
        "token": args.token

        
        
    }

    create_project(args.project_name, replacements)
    
if __name__ == "__main__":
    main()
