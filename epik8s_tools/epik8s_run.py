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
import subprocess  # For running Docker commands

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

## create a configuration in appargs.workdir for each ioc listed, for each ioc you should dump ioc as a yaml file as config/iocname-config.yaml
## run jnjrender  /epics/support/ibek-templates/ config/iocname-config.yaml --output iocname-ibek.yaml
def iocrun(iocs, appargs):
    config_dir = "/epics/ioc/config"
    if os.path.exists(config_dir):
        shutil.rmtree(config_dir)  # Remove the directory and its contents
        print(f"* Removed existing directory: {config_dir}")

    os.makedirs(config_dir)
    print(f"* Created configuration directory: {config_dir}")

    for ioc in iocs:
        ioc_name = ioc['name']
        config_file = os.path.join(appargs.workdir, f"{ioc_name}-config.yaml")
        output_file = os.path.join(config_dir, f"{ioc_name}-ibek.yaml")

        # Dump the IOC configuration to a YAML file
        with open(config_file, 'w') as file:
            yaml.dump(ioc, file, default_flow_style=False)
        print(f"* Created configuration file: {config_file}")

        # Run jnjrender to generate the output file
        jnjrender_command = f"jnjrender /epics/support/ibek-templates/ {config_file} --output {output_file}"
        print(f"* Running command: {jnjrender_command}")
        result = os.system(jnjrender_command)

        if result != 0:
            print(f"Error: Failed to run jnjrender for IOC '{ioc_name}'.")
            exit(1)
        else:
            print(f"* Successfully generated: {output_file}")
    
    
    start_command = ["/epics/ioc/start.sh"]
    print(f"* Running command: {start_command} with DIR={config_dir}")
    result = subprocess.run(start_command)

    if result.returncode != 0:
        print(f"Error: Failed to start IOC.")
        exit(1)
    else:
        print(f"* Successfully started IOC.")
    

import shutil  # Ensure shutil is imported for checking application availability

def main_run():
    parser = argparse.ArgumentParser(
        description="Run IOC from a given YAML configuration",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument("yaml_file", nargs="?", help="Path to the Configuration EPIK8S beamline YAML.")
    parser.add_argument("iocnames", nargs="*", help="Name of the iocs to run")

    parser.add_argument("--version", action="store_true", help="Show the version and exit")
    parser.add_argument("--native", action="store_true", help="Don't use Docker to run, run inside")
    parser.add_argument("--image", default="ghcr.io/infn-epics/infn-epics-ioc-runtime:latest", help="Use Docker image to run")
    parser.add_argument("--workdir", default=".", help="Working directory")
    parser.add_argument("--platform", default="linux/amd64", help="Docker image platform")
    parser.add_argument("--network", default="", help="Docker network")

    parser.add_argument("--caport", default="5064", help="Base port to use for CA")
    parser.add_argument("--pvaport", default="5075", help="Base port to use for PVA")

    args = parser.parse_args()

    # Handle --version flag early
    if args.version:
        print(f"epik8s-tools version {__version__}")
        exit(0)

    # Validate positional arguments
    if not args.yaml_file:
        print("Error: The 'yaml_file' argument is required.")
        exit(1)

    if not args.iocnames:
        print("Error: At least one IOC name must be specified.")
        exit(1)

    if not os.path.isfile(args.yaml_file):
        print(f"# yaml configuration {args.yaml_file} does not exists")
        exit(1)
        
    yamlconf=None
    with open(args.yaml_file, 'r') as file:
        yamlconf = yaml.safe_load(file)

    ## get ioc lists
    iocs=[]
    if 'epicsConfiguration' in yamlconf and 'iocs' in yamlconf['epicsConfiguration']:
        epics_config = yamlconf.get('epicsConfiguration', {})
        iocs=epics_config.get('iocs', []) ## epik8s yaml full configuratio
    elif 'iocs' in yamlconf:
        iocs=yamlconf.get('iocs', []) ## provided iocs list
    else:
        iocs=[yamlconf] ## ioc configuration alone
        
    ## check if the iocname1,iocname2 passed in arguments are included in the iocs list
    ioc_names_from_args = args.iocnames  # List of IOC names passed as arguments
        
        
    print(f"* found '{len(iocs)}' IOCs  in configuration")
    
    iocrunlist=[]
    # Validate the IOC names
    for ioc_name in ioc_names_from_args:
        found=False
        for ioc in iocs:
            if ioc_name == ioc['name']:
                ## add iocname
                ioc['iocname']=ioc_name
                ioc['config_dir']=args.workdir
                ioc['data_dir']=args.workdir
                ioc['autosave_dir']=args.workdir

                ## unroll iocparam
                if 'iocparam' in ioc:
                    for p in ioc['iocparam']:
                        ioc[p['name']]=p['value']
                    del ioc['iocparam']
                          
                iocrunlist.append(ioc)
                print(f"* found '{ioc_name}'")

                found=True
        if not found:
            print(f"Error: IOC '{ioc_name}' is not defined in the YAML configuration.")
            exit(2)
        

    # Check if the working directory exists, if not, create it
    if not os.path.exists(args.workdir):
        os.makedirs(args.workdir)
        print(f"* Created working directory: {args.workdir}")
    # Check for native mode requirements
    if args.native:
        required_directories = ["/epics/epics-base/", "/epics/ibek-defs/", "/epics/support/ibek-templates/"]
        required_apps = ["ibek", "jnjrender","/epics/ioc/start.sh"]

        # Check if required directories exist
        for directory in required_directories:
            if not os.path.isdir(directory):
                print(f"Error: Required directory '{directory}' is missing.")
                exit(1)

        # Check if required applications are available
        for app in required_apps:
            if not shutil.which(app):
                print(f"Error: Required application '{app}' is not available in PATH.")
                exit(1)

        print("* All required directories and applications are available for native mode.")
        iocrun(iocrunlist, args)
    else:
        # Run Docker with the specified parameters
        yaml_file_abs_path = os.path.abspath(args.yaml_file)  # Convert to absolute path
        
        # Build Docker arguments dynamically
        docker_args = [
            "docker", "run", "--rm", "-it",
            "--platform", args.platform,
            "-v", f"{os.path.abspath(args.workdir)}:/workdir",
            "-v", f"{yaml_file_abs_path}:/tmp/epik8s-config.yaml"
        ]

        # Add network option if specified, otherwise map ports
        if args.network:
            docker_args.extend(["--network", args.network])
        else:
            docker_args.extend([
                "-p", f"{args.caport}:{args.caport}",  # Map CA port
                "-p", f"{args.pvaport}:{args.pvaport}"  # Map PVA port
            ])

        # Add remaining Docker arguments
        docker_args.extend([
            args.image,
            "epik8s-run",  # Run the same script inside Docker
            "/tmp/epik8s-config.yaml",
            *args.iocnames,
            "--workdir", "/workdir",
            "--native"
        ])

        # Print and execute the Docker command
        print(f"* Running Docker command: {' '.join(docker_args)}")
        result = subprocess.run(docker_args)

        if result.returncode != 0:
            print("Error: Failed to run the IOC in Docker.")
            exit(result.returncode)

if __name__ == "__main__":
    main_run()
