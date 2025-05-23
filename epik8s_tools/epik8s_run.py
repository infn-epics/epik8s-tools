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


def run_remote(config: dict,source_dir,tmpwork) -> str:
    def get(d, path, default=None):
        keys = path.split(".")
        for k in keys:
            if isinstance(d, dict) and k in d:
                d = d[k]
            else:
                return default
        return d
          
    ca_server_port = str(get(config, "ca_server_port", 5064))
    pva_server_port = str(get(config, "pva_server_port", 5075))
    sshforward = ""
    if get(config, "forwardca"):
        sshforward = f"-L 0.0.0.0:5064:localhost:{ca_server_port}"
    if get(config, "pva"):
        sshforward = f"-L 0.0.0.0:5075:localhost:{pva_server_port}"

    caserverport_bcast = str(int(ca_server_port) + 1)
    pvaserverport_bcast = str(int(pva_server_port) + 1)

    dockeropt = "-it"
    dockerenv = ""
    lines = [
        "cd ~; id",
        f"caserverport={ca_server_port}",
        f"pvaserverport={pva_server_port}",
        f"sshforward=\"{sshforward}\"",
        f"caserverport_bcast=$(expr $caserverport + 1)",
        f"pvaserverport_bcast=$(expr $pvaserverport + 1)",
        f"echo \"EPICS_CA_SERVER_PORT=$caserverport\"",
        f"echo \"EPICS_CA_REPEATER_PORT=$caserverport_bcast\"",
        f"echo \"EPICS_PVAS_SERVER_PORT=$pvaserverport\"",
        f"echo \"EPICS_PVAS_BROADCAST_PORT=$pvaserverport_bcast\""
    ]

    networks = get(config, "networks", [])
    if networks:
        for net in networks:
            lines.append(f"echo \"* adding {net['annotation']}\"")
            if "ip" in net:
                dockeropt += f" --network {net['annotation']} --ip {net['ip']}"
            else:
                dockeropt += f" --network {net['annotation']}"
    elif get(config, "docker.hostnet"):
        lines.append("echo \"* enabling host network\"")
        dockeropt += " --network host"
        dockerenv = (
            f"-e EPICS_CA_SERVER_PORT={ca_server_port} "
            f"-e EPICS_CA_REPEATER_PORT={caserverport_bcast} "
            f"-e EPICS_PVAS_INTF_LIST=127.0.0.1 "
            f"-e EPICS_PVAS_SERVER_PORT={pva_server_port} "
            f"-e EPICS_PVAS_BROADCAST_PORT={pvaserverport_bcast}"
        )
    else:
        dockeropt += (
            f" -p {ca_server_port}:5064/tcp -p {ca_server_port}:5064/udp "
            f"-p {caserverport_bcast}:5065/tcp -p {caserverport_bcast}:5065/udp "
            f"-p {pva_server_port}:5075/tcp -p {pva_server_port}:5075/udp "
            f"-p {pvaserverport_bcast}:5076/tcp -p {pvaserverport_bcast}:5076/udp"
        )

    options = "-o StrictHostKeyChecking=no"
    dockermount = "-v .:/epics/ioc/config"

    ssh_opts = get(config, "ssh_options", "")
    if ssh_opts:
        options += f" {ssh_opts}"

    initcmd = get(config, "ssh.initcmd")
    if initcmd:
        lines.append(f"echo \"* Performing initcmd \\\"{initcmd}\\\"\"")
        lines.append(f"ssh {options} {config['ssh']['user']}@{config['ssh']['host']} \"{initcmd}\"")

    lines.append(f"echo \"* path {get(config, 'gitRepoConfig.path', '')}\"")

    for mount in get(config, "nfsMounts", []):
        mount_path = mount["mountPath"]
        dockermount = f"-v \"{mount_path}\":\"{mount_path}\" {dockermount}"



    
    ssh_user = config.get("user","root")
    ssh_host = config["host"]
    exec_cmd = config.get("exec", "./start.sh")
    print(f"* remote execution on {ssh_host}")

    workdir = config.get("workdir",f"workdir-{config['iocname']}")
    lines.append(f"echo \"* try connecting ssh {options} {ssh_user}@{ssh_host} mkdir -p {workdir}\"")
    lines.append(f"""if ssh {options} {ssh_user}@{ssh_host} "rm -rf {workdir};mkdir -p {workdir}"; then
  echo "* created workdir {workdir}"
else
  echo "## error creating {workdir} aborting.."
  exit 1
fi""")

    scpopt = config.get("scpoptions", "")
    lines.append(f"""echo "* scp {options} {scpopt} -r {source_dir}/* {ssh_user}@{ssh_host}:{workdir}"
if scp {options} {scpopt} -r {source_dir}/* {ssh_user}@{ssh_host}:{workdir}; then
  echo "* copied {source_dir}"
else
  echo "## error copying {source_dir}"
  exit 1
fi""")

    if get(config, "nfsMounts"):
        lines.append("echo \"* Performing mounts\"")
        lines.append(f"ssh {options} {ssh_user}@{ssh_host} \"{workdir}/nfsmount.sh\"")

    envstr = f"export __IOC_TOP__=\"{workdir}\" && export __IOC_PREFIX__=\"{config.get('iocprefix', '')}\" && export __IOC_NAME__=\"{config.get('iocname', '')}\""
    for env in config.get("env", []):
        envstr += f" && export {env['name']}=\"{env['value']}\""
        dockerenv += f" -e {env['name']}={env['value']}"

    if get(config, "docker.enable"):
        docker_args = get(config, "docker.args")
        if docker_args:
            dockeropt = docker_args
            lines.append(f"echo \"User options {docker_args}\"")

        options += " -t"
        image = config["docker"]["image"]
        iocname = config["iocname"]
        rundocker = f"docker run --rm --name {iocname}  {dockermount} {dockerenv} {dockeropt} {image}"
        lines.append(f"echo \"* killing {iocname} docker  (if any)\"")
        lines.append(f"ssh {options} {ssh_user}@{ssh_host} \"docker kill {iocname};docker rm {iocname}\"")
        lines.append("sleep 1")
        lines.append(f"echo \"* pulling {image}\"")
        lines.append(f"ssh {options} {ssh_user}@{ssh_host} \"docker pull {image}\"")
        lines.append(f"echo \"* Running Docker '{rundocker}'\"")
        lines.append(f"ssh {options} {sshforward} {ssh_user}@{ssh_host} \"cd {workdir} && {rundocker}\"")
    else:
        lines.append(f"echo \"* Running Remotely {exec_cmd} workdir {workdir}\"")
        lines.append(f"echo \"* Passing Environment {envstr}\"")
        lines.append(f"ssh {options} {ssh_user}@{ssh_host} \"cd {workdir} && {envstr} && ./{exec_cmd}\"")

    lines.append("echo \"## Exiting..\"")
    lines.append("exit 1")
    
    # Write the lines to a shell script
    script_path = f"{tmpwork}/run.sh"
    with open(script_path, "w") as f:
        f.write("#!/bin/bash\n")
        f.write("\n".join(lines))
    os.chmod(script_path, 0o755)  # Make the script executable

    # Execute the script
    print(f"* Connecting to executing script: {script_path}")
    result = subprocess.run([script_path])

    return result.returncode

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

def run_jnjrender(template_path, config_file, output_dir):
    """Run the jnjrender command with the specified template and config file."""
    jnjrender_cmd = f"jnjrender {template_path} {config_file} --output {output_dir}"
    result = os.system(jnjrender_cmd)
    if result != 0:
        print(f"Error: Failed to run jnjrender with template {jnjrender_cmd}")
        exit(1)
   
## create a configuration in appargs.workdir for each ioc listed, for each ioc you should dump ioc as a yaml file as config/iocname-config.yaml
## run jnjrender  /epics/support/ibek-templates/ config/iocname-config.yaml --output iocname-ibek.yaml
def iocrun(iocs, appargs):
    config_dir = appargs.configdir
    script_dir = os.path.dirname(os.path.realpath(__file__)) + "/template/"

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
            print(f"Searching '{template_name}' in {appargs.templatedir}")

            for root, dirs, files in os.walk(appargs.templatedir):
                if template_name in files:
                    template_path = os.path.join(root, template_name)
                    template_dir = root
                    break
            if template_path:
                ## this is a ibek template
                # Call jnjrender with the found template file
                run_jnjrender(template_dir,config_file,config_dir)
                
                ibek_count += 1
                ioc['ibek'] = True
                continue  # Skip the default jnjrender call below if template was used
            else:
                print(f"Searching '{ioc['template']}' in {appargs.templatedir}")
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
                        run_remote(ioc,iocconfig,appargs.workdir)
                    continue
    
    if ibek_count>0:
        start_command = ["/epics/ioc/start.sh"]
        print(f"* Running command: {start_command} with DIR={config_dir}")
        result = subprocess.run(start_command)

        if result.returncode != 0:
            print(f"Error: Failed to start IBEK IOC.")
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
    parser.add_argument("--templatedir", default="/epics/support/templates", help="Templates directory")
    parser.add_argument("--configdir", default="/epics/ioc/config", help="Configuration output directory")
    parser.add_argument("--rm", action="store_true", help="Remove configuration directory content")

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
                if 'nfsMounts' in yamlconf:
                    ioc['nfsMounts']=yamlconf['nfsMounts']
                    for k in ioc['nfsMounts']:
                        if 'mountPath' in k:
                            ioc[k['name']+"_dir"]=k['mountPath']

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
