import yaml
import os
import ast
import shutil
import jinja2
from jinja2 import Environment, FileSystemLoader,Template
from collections import OrderedDict
import argparse
from datetime import datetime
from epik8s_tools import __version__
import subprocess  # For running Docker commands
from epik8s_common import dump_exec, run_jnjrender,app_dir,run_remote


def copytree(template_dir, config_dir):
    for item in os.listdir(template_dir):
        s = os.path.join(template_dir, item)
        d = os.path.join(config_dir, item)
        if os.path.isdir(s):
            shutil.copytree(s, d, False, None)
        else:
            shutil.copy2(s, d)
    
def gitconfig(config: dict) -> str:
    url = config["gitRepoConfig"]["url"]
    path = config["gitRepoConfig"].get("path", "")
    branch = config["gitRepoConfig"].get("branch", "")
    token = config["gitRepoConfig"].get("token", "")

    lines = [
        "set -e  # Exit immediately if a command fails",
        "id=$(id)",
        "cd /pvc",
        "rm -rf *",
        "prefix=\"\"",
        f"echo \"ID $id cloning: {url} {path} revision {branch}\"",
        "if [ -d temp-config ]; then",
        "  rm -rf temp-config",
        "fi"
    ]

    if token:
        lines.append("git config --global credential.helper \"store --file=/.ssh/git_token\"")
    else:
        lines.append("echo \"Cloning repository unauthenticated\"")

    if branch:
        lines.append(f"git clone --depth 1 -b {branch} {url} --recurse-submodules temp-config")
    else:
        lines.append(f"git clone --depth 1 {url} --recurse-submodules temp-config")

    lines.append(f"if [ -d temp-config/{path} ]; then")
    if path == ".":
        lines.append("  mv temp-config/* /pvc/")
    else:
        lines.append(f"  mv temp-config/{path}/* /pvc/")
        lines.append("  rm -rf temp-config")
    lines.append("else")
    lines.append("  mv temp-config/* /pvc/")
    lines.append("fi")

    return "\n".join(lines)



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
    config_dir = appargs.configdir
    script_dir = app_dir()

    if os.path.exists(config_dir):
        if appargs.rm:
            for item in os.listdir(config_dir):
                item_path = os.path.join(config_dir, item)
                if os.path.isdir(item_path):
                    shutil.rmtree(item_path)
                else:
                    os.remove(item_path)
                print(f"* Removed all contents of directory: {config_dir}")
    else:
        os.makedirs(config_dir)
        print(f"* Created configuration directory: {config_dir}")
        
    ibek_count = 0
    for ioc in iocs:
        ioc_name = ioc['name']
        config_file = os.path.join(appargs.workdir, f"{ioc_name}-config.yaml")
        output_file = os.path.join(config_dir, f"{ioc_name}-ibek.yaml")

        # Dump the IOC configuration to a YAML file
        with open(config_file, 'w') as file:
            yaml.dump(ioc, file, default_flow_style=False)
        print(f"* Created configuration file: {config_file}")
        if 'template' in ioc:
            # Find template.yaml.j2 recursively in /epics/support/ibek-templates/
            template_name = ioc['template']+".yaml.j2"
            template_path = None
            template_dir = None
            print(f"* IBEK Search '{template_name}' in {appargs.templatedir}")

            for root, dirs, files in os.walk(appargs.templatedir):
                if template_name in files:
                    template_path = os.path.join(root, template_name)
                    template_dir = root
                    break
            if template_path:
                ## this is a ibek template
                # Call jnjrender with the found template file
                dump_exec(config_dir)
                run_jnjrender(template_dir,config_file,config_dir)
                
                ibek_count += 1
                ioc['ibek'] = True
                continue  # Skip the default jnjrender call below if template was used
            else:
                print(f"* Searching '{ioc['template']}' in {appargs.templatedir}")
                ## search directory ioc['template'] in /epics/support/support-templates
                template_path = None

                for root, dirs, files in os.walk(appargs.templatedir):
                    if ioc['template'] in dirs:
                        template_path = os.path.join(root, ioc['template'])
                        template_dir = root
                        break
                if template_path:
                    iocconfig = f"{config_dir}/{ioc_name}"
                    os.makedirs(iocconfig, exist_ok=True)
                    run_jnjrender(template_path,config_file,iocconfig)
                    if 'host' in ioc:
                        run_jnjrender(script_dir+"/nfsmount.sh.j2",config_file,iocconfig)
                        # copy config_file to iocconfig
                        if os.path.exists("/BUILD_INFO.txt"):
                            shutil.copy("/BUILD_INFO.txt", os.path.join(iocconfig, "BUILD_INFO.txt"))
                        shutil.copy(config_file, os.path.join(iocconfig, f"{ioc_name}-config.yaml"))
                        run_remote(ioc,iocconfig,appargs.workdir)
                    continue
    
    if ibek_count>0:
        start_command = f"{config_dir}/ioc_exec.sh"
        # Execute the command in IOC_EXEC
     
        result = subprocess.run(start_command, shell=True)
        if result.returncode != 0:
            print(f"Error: Failed to execute {start_command} script.")
            exit(1)
        else:
            print(f"* Successfully executed {start_command} script.")
        
    

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
    parser.add_argument("--templatedir", default="/epics/support/templates", help="Templates directory")
    parser.add_argument("--configdir", default="/epics/ioc/config", help="Configuration output directory")
    parser.add_argument("--rm", action="store_true", help="Remove configuration directory content")
    parser.add_argument("--dockerargs", default="", help="Additional Docker arguments for running the IOC")
    parser.add_argument("--caport", default="5064", help="Base port to use for CA")
    parser.add_argument("--pvaport", default="5075", help="Base port to use for PVA")

    args = parser.parse_args()

    # Handle --version flag early
    if args.version:
        print(f"epik8s-run version {__version__}")
        exit(0)

    # Validate positional arguments
    if not args.yaml_file:
        print("Error: The 'yaml_file' argument is required.")
        exit(1)

    if not args.iocnames:
        print("Error: At least one IOC name must be specified.")
        exit(1)

    if not os.path.isfile(args.yaml_file):
        print(f"# yaml configuration '{args.yaml_file}' does not exists")
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
                ioc['config_dir']=args.workdir+"/"+ioc_name
                ioc['data_dir']=args.workdir+"/"+ioc_name
                ioc['autosave_dir']=args.workdir+"/"+ioc_name
                ioc['epik8s-tools-version']=__version__
                if 'workdir' in ioc:
                    ioc['ioctop']=ioc['workdir']
                if 'nfsMounts' in yamlconf and yamlconf['nfsMounts']:
                    ioc['nfsMounts']=yamlconf['nfsMounts']
                    for k in ioc['nfsMounts']:
                        if 'mountPath' in k:
                            ioc[k['name']+"_dir"]=k['mountPath']+"/"+ioc_name

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
        required_directories = ["/epics/epics-base/", "/epics/ibek-defs/", f"{args.templatedir}"]
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
        if args.dockerargs:
            docker_args.extend(args.dockerargs.split())
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
