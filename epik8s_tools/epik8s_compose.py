import os,sys
import argparse
 
import shutil
from jinja2 import Template



# Templates for config and execution
GATEWAY_EXEC = """
#!/bin/sh
mkdir -p /epics/ca-gateway/configure
cp -r /mnt/* /epics/ca-gateway/configure
sleep 10
/epics/ca-gateway/configure/start.sh
"""

IOC_EXEC = """
#!/bin/sh
{% if serial and serial.ip and serial.port %}
echo "opening {{ serial.ptty }},raw,echo=0,b{{ serial.baud }} tcp:{{ serial.ip }}:{{ serial.port }}"
socat pty,link={{ serial.ptty }},raw,echo=0,b{{ serial.baud }} tcp:{{ serial.ip }}:{{ serial.port }} &
sleep 1
if [ -e {{ serial.ptty }} ]; then
echo "tty {{ serial.ptty }}"
else
echo "## failed tty {{ serial.ptty }} "
exit 1
fi
{% endif %}
echo "* pruning previous configs"
rm -rf /epics/ioc/config
mkdir -p /epics/ioc/config
cp -r /mnt/* /epics/ioc/config/
{% for mount in nfsMounts %}
mkdir -p {{ mount.mountPath }}/{{ iocname }}
{% if mount.name == "config" %}
cp -r /epics/ioc/config/* {{ mount.mountPath }}/{{ iocname }}/
{% endif %}
{% endfor %}
{% if start %}
export PATH="$PATH:$PWD"
chmod +x {{ start }}
{{ start }}
{% else %}
/epics/ioc/start.sh
{% endif %}
"""

def copy_directory(src, dest):
    if os.path.exists(dest):
        shutil.rmtree(dest)
    shutil.copytree(src, dest)

def parse_config(file_path):
    with open(file_path, 'r') as file:
        return yaml.safe_load(file)


def determine_mount_path(host_dir, what, service_name, output_dir):
    # If host_dir is relative, concatenate it with output_dir
    check_dir = os.path.join(output_dir, host_dir)
    isrelative = False
    if not os.path.isabs(host_dir):
        check_dir = os.path.join(check_dir, what, service_name)
        isrelative = True

    
    fallback_dir = os.path.join(host_dir, what, service_name)
    if os.path.isdir(check_dir):
        return fallback_dir
    else:
        if isrelative:
            print(f"%% [{service_name}] path {check_dir} does not exist, check iocdir or devtype in YAML configuration")
        else:
            print(f"%% [{service_name}] path {fallback_dir} does not exist, check iocdir or devtype in YAML configuration")
    return ""

def render_config(template_str, service_config):
    template = Template(template_str)
    return template.render(service_config)

def render_j2_files(directory, config):
    for root, _, files in os.walk(directory):
        for file in files:
            if file.endswith(".j2"):
                file_path = os.path.join(root, file)
                with open(file_path, 'r') as f:
                    template_str = f.read()
                rendered_content = render_config(template_str, config)
                output_file_path = file_path[:-3]  # Remove the .j2 extension
                with open(output_file_path, 'w') as f:
                    f.write(rendered_content)
#                os.remove(file_path)  # Optionally remove the .j2 file after rendering

def write_config_file(directory, content, fname):
    os.makedirs(directory, exist_ok=True)
    config_path = os.path.join(directory, fname)
    with open(config_path, 'w') as file:
        file.write(content)
        os.chmod(config_path, 0o755)

def generate_docker_compose_and_configs(output_dir, selected_services, caport, pvaport, ingressport, exclude_services):
    config_yaml = os.path.join(output_dir, "config.yaml")
    host_dir = "./config"
    config = parse_config(config_yaml)

    docker_compose = {'services': {}}
    epics_config = config.get('epicsConfiguration', {})
    env_content = None
    env_host_content = None
    epics_ca_addr_list = ""
    epics_pva_addr_list = ""
    cadepend_list = []
    pvadepend_list = []

    # Generate env file
    for ioc in epics_config.get('iocs', []):
        if selected_services and ioc['name'] not in selected_services:
            continue
        if exclude_services and ioc['name'] in exclude_services:
            print(f"%% ioc {ioc['name']} excluded")
            continue
        epics_ca_addr_list += f"{ioc['name']} "
        cadepend_list.append(ioc['name'])
        if 'pva' in ioc:
            epics_pva_addr_list += f"{ioc['name']} "
            pvadepend_list.append(ioc['name'])
    if epics_ca_addr_list:
        env_content = f"EPICS_CA_ADDR_LIST=\"{epics_ca_addr_list.strip()}\"\nEPICS_PVA_NAME_SERVERS=\"{epics_pva_addr_list.strip()}\"\nEPICS_PVA_ADDR_LIST=\"{epics_pva_addr_list.strip()}\""

    # Process services
    for service, service_val in epics_config.get('services', {}).items():
        image = None
        tag = None
        if selected_services and service not in selected_services:
            continue
        if exclude_services and service in exclude_services:
            print(f"%% service {service} excluded")
            continue

        if 'image' not in service_val:
            if service == "gateway":
                image = "baltig.infn.it:4567/epics-containers/docker-ca-gateway"
            if service == "pvagateway":
                image = "baltig.infn.it:4567/epics-containers/docker-pva-gateway"
        else:
            image = service_val['image'].get('repository', service_val['image'])
            if 'tag' in service_val['image']:
                tag = service_val['image'].get('tag', 'latest')

        if not image:
            print(f"%% service {service} skipped no image")
            continue
        if tag:
            docker_compose['services'][service] = {'image': f"{image}:{tag}"}
        else:
            docker_compose['services'][service] = {'image': f"{image}"}

        if 'loadbalancer' in service_val:
            if service == "gateway":
                docker_compose['services'][service]['ports'] = [f"{caport}:5064/tcp", f"{caport}:5064/udp", f"{caport+1}:5065/tcp", f"{caport+1}:5065/udp"]
                env_host_content = f"export EPICS_CA_ADDR_LIST=localhost:{caport}\n"
                caport += 2
                docker_compose['services'][service]['depends_on'] = cadepend_list

            if service == "pvagateway":
                docker_compose['services'][service]['ports'] = [f"{pvaport}:5075/tcp", f"{pvaport+1}:5076/udp"]
                if env_host_content:
                    env_host_content += f"\nexport EPICS_PVA_NAME_SERVERS=localhost:{pvaport}\n"
                else:
                    env_host_content = f"\nexport EPICS_PVA_NAME_SERVERS=localhost:{pvaport}\n"
                pvaport += 2
                docker_compose['services'][service]['depends_on'] = pvadepend_list

        if 'enable_ingress' in service_val and service_val['enable_ingress']:
            if service == "archiver":
                docker_compose['services'][service]['ports'] = [f"{ingressport}:17665"]
                ingressport += 1

        if env_content:
            docker_compose['services'][service]['env_file'] = ["__docker__.env"]
        mount_path = determine_mount_path(host_dir, 'services', service, output_dir)
        if mount_path:
            copy_directory(f"{output_dir}/{mount_path}", f"{output_dir}/__docker__/{service}")
            config_content=yaml.dump(service_val, default_flow_style=False)
            write_config_file(f"{output_dir}/__docker__/{service}/init", config_content, "init.yaml")
            render_j2_files(f"{output_dir}/__docker__/{service}",service_val) 
            docker_compose['services'][service]['volumes'] = [f"./__docker__/{service}:/mnt"]
            if service == "gateway" or service == "pvagateway":
                write_config_file(f"{output_dir}/__docker__/{service}", GATEWAY_EXEC, "docker_run.sh")
                docker_compose['services'][service]['command'] = "sh -c /mnt/docker_run.sh"
            else:
                if os.path.isfile(mount_path + "/start.sh"):
                    docker_compose['services'][service]['command'] = "sh -c /mnt/start.sh"

        print(f"* added service {service}")

    # Process IOCs
    for ioc in epics_config.get('iocs', []):
        if selected_services and ioc['name'] not in selected_services:
            continue
        image = ioc.get('image', 'baltig.infn.it:4567/epics-containers/infn-epics-ioc')
        docker_compose['services'][ioc['name']] = {'image': image}
        docker_compose['services'][ioc['name']]['ports'] = ["5064/tcp", "5064/udp", "5065/tcp", "5065/udp", "5075/tcp", "5075/udp"]
        if 'networks' in ioc:
            docker_compose['services'][ioc['name']]['network_mode'] = 'host'

        if env_content:
            docker_compose['services'][ioc['name']]['env_file'] = [f"__docker__.env"]
       
        mount_path = determine_mount_path(host_dir, 'iocs', ioc.get('iocdir', ioc['name']), output_dir)
        
        if mount_path:
            copy_directory(f"{output_dir}/{mount_path}", f"{output_dir}/__docker__/{ioc['name']}")

            docker_compose['services'][ioc['name']]['volumes'] = [f"./__docker__/{ioc['name']}:/mnt"]
            docker_compose['services'][ioc['name']]['tty'] = True
            docker_compose['services'][ioc['name']]['stdin_open'] = True

            todump = ioc
            if 'iocparam' in todump:
                for k in todump['iocparam']:
                    todump[k['name']] = k['value']
                del todump['iocparam']
            todump['iocname'] = ioc['name']
            config_content = yaml.dump(todump, default_flow_style=False)

            write_config_file(f"{output_dir}/__docker__/{ioc['name']}/init", config_content, "init.yaml")
            try:
                render_j2_files(f"{output_dir}/__docker__/{ioc['name']}",todump) # f"{output_dir}/__docker__/{ioc['name']}/init/init.yaml")
            except Exception as e:
                if e:
                    print(f"## error in IOC {ioc['name']} rendering '{output_dir}/__docker__/{ioc['name']}'\n\n{e}")
                else:
                    print(f"## error in IOC {ioc['name']} rendering '{output_dir}/__docker__/{ioc['name']}'")

                sys.exit(1)
                
            exec_content = render_config(IOC_EXEC, ioc)
            write_config_file(f"{output_dir}/__docker__/{ioc['name']}", exec_content, "docker_run.sh")
            
            docker_compose['services'][ioc['name']]['command'] = f"sh -c /mnt/docker_run.sh"
            
        print(f"* added ioc {ioc['name']}")

    if env_content:
        env_content += "\nexport EPICS_CA_AUTO_ADDR_LIST=NO\n"
        write_config_file(output_dir, env_content, "__docker__.env")

    if env_host_content:
        env_host_content += "\nexport EPICS_CA_AUTO_ADDR_LIST=NO\n"
        write_config_file(output_dir, env_host_content, "epics-channel.env")
    else:
        print("%% no environment file generated, no services gateway services selected")

    return docker_compose

def main_compose():
    caport = 5064
    pvaport = 5075
    ingressport = 8090
    parser = argparse.ArgumentParser(description="Generate docker-compose.yaml and config.yaml for EPICS IOC.")
    parser.add_argument('--config', required=True, help="Path to the configuration file (YAML).")
    parser.add_argument('--host-dir', required=True, help="Base directory on the host.")
    parser.add_argument('--output', help="Output directory for the generated files.")
    parser.add_argument('--services', nargs='+', help="List of services to include in the output (default ALL).")
    parser.add_argument('--exclude', nargs='+', help="List of services to exclude in the output")

    parser.add_argument('--caport', default=caport, help="Start CA access port to map on host")
    parser.add_argument('--pvaport', default=pvaport, help="Start PVA port to map on host")
    parser.add_argument('--htmlport', default=ingressport, help="Start ingress (http) port on host")

    args = parser.parse_args()

    caport = args.caport
    pvaport = args.pvaport
    ingressport = args.htmlport
    output_dir = args.output
    yl = parse_config(args.config)
    if args.output is None and 'beamline' in yl:
        output_dir = f"{yl['beamline']}-compose"
    os.makedirs(output_dir, exist_ok=True)

    # Copy host_dir and config to output_dir
    copy_directory(args.host_dir, os.path.join(output_dir, 'config'))
    shutil.copy(args.config, os.path.join(output_dir, 'config.yaml'))
    print(f"* copied {args.host_dir} to {output_dir}/config")
    print(f"* copied {args.config} to {output_dir}/config.yaml")

    try:
        docker_compose = generate_docker_compose_and_configs(output_dir, args.services, int(caport), int(pvaport), ingressport, args.exclude)
    except FileNotFoundError as e:
        print(e)
        return

    dcf = os.path.join(output_dir, 'docker-compose.yaml')
    with open(dcf, 'w') as output_file:
        yaml.dump(docker_compose, output_file, default_flow_style=False)

    print(f"* docker compose file '{dcf}'")


if __name__ == "__main__":
    main_compose()
