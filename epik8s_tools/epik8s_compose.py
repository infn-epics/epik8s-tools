import os
import argparse
import yaml
from jinja2 import Template

# Template for config.yaml
CONFIG_TEMPLATE = """
{{- if .nfsMounts }}
{{- range .nfsMounts }}
{{ .name | lower }}_dir: {{ .mountPath }}/{{ .iocname }}
{{- end }}
{{- else }}
config_dir: {{ .iocConfig }}
data_dir: {{ .dataVolume.hostPath }}
autosave_dir: "/autosave"
{{- end }}
ioctop: {{ .iocConfig }}
{{- range iocparam }}
{{ .name }}: {{ .value }}
{{- end }}
{{ toYaml . | indent(4) }}
"""
CONFIG_TEMPLATE = """
ioctop: {{name}}
devtype: {{devtype}}
ioctop: {{ name }}
devtype: {{ devtype }}
{% for param in iocparam %}
{{ param.name }}: {{ param.value }}
{% endfor %}
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

echo "=== configuration yaml ======="
cat /epics/ioc/config/__docker__/config.yaml 
echo "=============================="
echo "* copy ioc config and replace any .j2 with rendered values"
find /epics/ioc/config -name "*.j2" -exec sh -c 'jnjrender "$1" /epics/ioc/config/__docker__/config.yaml --output "${1%.j2}"' _ {} \;
{% for mount in nfsMounts %}
mkdir -p {{ mount.mountPath }}/{{ iocname }}
{% if mount.name == "config" %}
cp -r /epics/ioc/config/* {{ mount.mountPath }}/{{ iocname }}/
{% endif %}
{% endfor %}
cd /epics/ioc/config
ls -latr
{% if start %}
export PATH="$PATH:$PWD"
chmod +x {{ start }}
{{ start }}
{% endif %}
"""

def parse_config(file_path):
    """Parses a YAML configuration file."""
    with open(file_path, 'r') as file:
        return yaml.safe_load(file)

def determine_mount_path(host_dir, what,service_name):
    """Determines the directory to mount."""
    fallback_dir = os.path.join(host_dir, what+'/'+service_name)

    if os.path.isdir(fallback_dir):
        return fallback_dir
    else:
        print(f"%% path {fallback_dir} does not exists")
        
    
    return ""

def render_config(str,service_config):
    """Renders the config.yaml content using the Jinja2 template."""
    template = Template(str)
    return template.render(service_config)

def write_config_file(directory, content,fname):
    """Writes the generated config.yaml content to the specified directory."""
    os.makedirs(directory, exist_ok=True)
    config_path = os.path.join(directory, fname)
    with open(config_path, 'w') as file:
        file.write(content)
        os.chmod(config_path, 0o755)  # rwxr-xr-x


def generate_docker_compose_and_configs(config, host_dir):
    """Generates docker-compose.yaml and config.yaml files."""
    docker_compose = {
        'services': {}
    }
    epics_config=config.get('epicsConfiguration')
    epics_ca_addr_list=""
    epics_pva_addr_list=""
    for ioc in epics_config.get('iocs', []):
        epics_ca_addr_list=f"{ioc['name']} {epics_ca_addr_list}"
        if 'pva' in ioc:
            epics_pva_addr_list=f"{ioc['name']} {epics_pva_addr_list}"
    
    env_content=None  
    if epics_ca_addr_list:
        env_content=f"EPICS_CA_ADDR_LIST=\"{epics_ca_addr_list}\"\nEPICS_PVA_ADDR_LIST=\"{epics_pva_addr_list}\""

    
    for service,service_val in epics_config.get('services', {}).items():
        service_name = service
        if 'image' in service_val:
            image=""
            if 'repository' in service_val['image']:
                image=service_val['image']['repository']
                if 'tag' in service_val['image']:
                    image=image+":"+service_val['image']['tag']  
            else: 
                image = service_val['image']
            # env_vars = service.get('environment', {})
            docker_compose['services'][service_name] ={}
            docker_compose['services'][service_name]['image']=f"{image}"
            if env_content:
                docker_compose['services'][service_name]['env_file']=["__docker__.env"]
            
            # Determine the mount path
            mount_path = determine_mount_path(host_dir, 'services',service_name)

            # Add the service to the docker-compose structure
            if mount_path:
                docker_compose['services'][service_name]['volumes']=[f"{mount_path}:/service"]
            print (f"* added service {service_name}")

            # Generate config.yaml for the service
            # config_content = render_config(service)
            #write_config_file(mount_path, config_content)

    for ioc in epics_config.get('iocs', []):
        ioc_name = ioc['name']
        if not 'image' in ioc:
            if 'ioc-chart.git' in ioc['charturl']:
                ioc['image']='baltig.infn.it:4567/epics-containers/infn-epics-ioc'
            if 'ioc-launcher-chart.git' in ioc['charturl']:
                continue ## dont handle embedded iocs
        if 'image' in ioc:
            image = ioc['image']
            # env_vars = service.get('environment', {})
            docker_compose['services'][ioc_name]= {}
            docker_compose['services'][ioc_name]['image']=f"{image}"
            if env_content:
                docker_compose['services'][ioc_name]['env_file']=["__docker__.env"]
            iocdir=ioc_name
            if 'iocdir' in ioc:
                iocdir=ioc['iocdir']
            # Determine the mount path
            mount_path = determine_mount_path(host_dir, 'iocs',iocdir)

            # Add the service to the docker-compose structure
            if mount_path:
                docker_compose['services'][ioc_name]['volumes'] = [f"{mount_path}:/epics/ioc/config"]
                # Generate config.yaml for the service
                config_content = render_config(CONFIG_TEMPLATE,ioc)
                write_config_file(mount_path+"/__docker__", config_content,"config.yaml")
                exec_content = render_config(IOC_EXEC,ioc)
                write_config_file(mount_path+"/__docker__", exec_content,"docker_run.sh")

                docker_compose['services'][ioc_name]['command'] = f"/epics/ioc/config/__docker__/docker_run.sh"
        print (f"* added ioc {ioc_name}")
    if env_content:
        write_config_file(".", env_content,"__docker__.env")
    return docker_compose

def main():
    parser = argparse.ArgumentParser(description="Generate docker-compose.yaml and config.yaml for EPICS IOC.")
    parser.add_argument('--config', required=True, help="Path to the configuration file (YAML).")
    parser.add_argument('--host-dir', required=True, help="Base directory on the host.")
    parser.add_argument('--output', default='docker-compose.yaml', help="Output file for docker-compose.yaml.")

    args = parser.parse_args()

    # Parse configuration file
    config = parse_config(args.config)

    # Generate docker-compose.yaml and config.yaml
    try:
        docker_compose = generate_docker_compose_and_configs(config, args.host_dir)
    except FileNotFoundError as e:
        print(e)
        return

    # Write docker-compose.yaml to output file
    with open(args.output, 'w') as output_file:
        yaml.dump(docker_compose, output_file, default_flow_style=False)

    print(f"Docker Compose file generated at '{args.output}'")
    print("Config files generated for each service.")

if __name__ == "__main__":
    main()
