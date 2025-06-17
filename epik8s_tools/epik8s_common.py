IOC_EXEC = """
#!/bin/sh
{%- if serial and serial.ip and serial.port %}
echo "opening {{ serial.ptty }},raw,echo=0,b{{ serial.baud }} tcp:{{ serial.ip }}:{{ serial.port }}"
socat pty,link={{ serial.ptty }},raw,echo=0,b{{ serial.baud }} tcp:{{ serial.ip }}:{{ serial.port }} &
sleep 1
if [ -e {{ serial.ptty }} ]; then
echo "tty {{ serial.ptty }}"
else
echo "## failed tty {{ serial.ptty }} "
exit 1
fi
{%- endif %}
{%- for mount in nfsMounts %}
mkdir -p {{ mount.mountPath }}/{{ iocname }}
{%- if mount.name == "config" %}
cp -r /epics/ioc/config/* {{ mount.mountPath }}/{{ iocname }}/
{%- endif %}
{%- endfor %}
{%- if start %}
export PATH="$PATH:$PWD"
chmod +x {{ start }}
{{ start }}
{%- else %}
/epics/ioc/start.sh
{%- endif %}
"""
import os
import subprocess  # For running Docker commands

def dump_exec(indir):
    ioc_exec_script = os.path.join(indir, "ioc_exec.sh.j2")
    with open(ioc_exec_script, "w") as f:
        f.write(IOC_EXEC)
    os.chmod(ioc_exec_script, 0o755)  # Make the script executable
    print(f"* Created ioc_exec script: {ioc_exec_script}")

def run_jnjrender(template_path, config_file, output_dir):
    """Run the jnjrender command with the specified template and config file."""
    jnjrender_cmd = f"jnjrender {template_path} {config_file} --output {output_dir}"
    result = os.system(jnjrender_cmd)
    if result != 0:
        print(f"Error: Failed to run jnjrender with template {jnjrender_cmd}")
        exit(1)

def app_dir():
    script_dir = os.path.dirname(os.path.realpath(__file__)) + "/template/"
    return script_dir

def run_remote(config: dict,source_dir,tmpwork) -> str:
    def get(d, path, default=None):
        keys = path.split(".")
        for k in keys:
            if isinstance(d, dict) and k in d:
                d = d[k]
            else:
                return default
        return d
    mountenable= 'nfsMounts' in config and config['nfsMounts']     
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

    if mountenable:
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
  echo "* copied {source_dir} to {workdir}"
else
  echo "## error copying {source_dir} to {workdir}"
  exit 1
fi""")

    if mountenable:
        lines.append("echo \"* Performing mounts\"")
        lines.append(f"ssh {options} {ssh_user}@{ssh_host} \"{workdir}/nfsmount.sh\"")

    #envstr = f"export __IOC_TOP__=\"{workdir}\" && export __IOC_PREFIX__=\"{config.get('iocprefix', '')}\" && export __IOC_NAME__=\"{config.get('iocname', '')}\""
    envstr = ""
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
        lines.append(f"ssh {options} {ssh_user}@{ssh_host} \"cd {workdir} {envstr} && ./{exec_cmd}\"")

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
