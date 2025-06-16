# EPIK8s Chart for {{ beamline }}

This documentation is automatically generated on {{time}} by *epik8s-tools* ({{version}}) .

## Table of Contents
- [Beamline](#beamline)
{% if nfsMounts %}
- [NFS Mounts](#nfs-mounts)
{%- endif %}
- [IO Controllers (IOCS)](#iocs)
- [EPIK8s Services](#services)
- [Application](#applications)



## Beamline

**BEAMLINE**: `{{ beamline }}`

**BEAMLINE URL**: `{{ giturl }}`

**BEAMLINE REV**: `{{ gitrev }}`

**Services DNS**: `{{ epik8namespace }}`

**Namespace**: `{{ namespace }}`

**EPIK8s charts default revision**: `{{targetRevision}}`

**CA Gateway**: `{{cagatewayip}}`

**PVA Gateway**: `{{pvagatewayip}}`



{% if nfsMounts %}
---

## NFS Mounts

| Name               | Server                 | Src                                      | Dst                    |
|--------------------|------------------------|------------------------------------------|------------------------|
{%- for nfs in nfsMounts %}
|{{ nfs.name.ljust(20)}}|{{ nfs.server.ljust(24) }}|{{ nfs.path.ljust(42)}}|{{ nfs.mountPath.ljust(24)}}|
{%- endfor%}

---

{% endif %}


## IOCs

| Name             | Type       | Group      | Prefix                   |Template      |Description                                         |
|------------------|------------|------------|--------------------------|--------------|---------------------------------------------------|
{%- for ioc in iocs %}
{%- set opiurl = "-" %}
{%- set opimain = "-" %}
{%- set ctx_macro = "-" %}
{%- set template = "-" %}

{%- if ioc.opi %}
{%- set opiurl = ioc.opinfo.url %}
{%- set opimain = ioc.opinfo.main %}
{%- set ctx_macro = ioc.opinfo.macroinfo %}
{%- endif %}
{%- if ioc.template %}
{%- set template = ioc.template %}
{%- elif ioc.iocdir %}
{%- set template = ioc.iocdir %}
{%- endif %}

{%- set name=ioc.name.ljust(18) %}
| [{{name}}](#{{ioc.name | lower}})|{{ (ioc.devtype | default("uknown")).ljust(12)}}|{{ (ioc.devgroup | default("uknown")).ljust(12)}}|{{ (ioc.iocprefix ~":"~ ioc.iocroot).ljust(26)}}|{{ template.ljust(14)}}|{{(ioc.asset | string).ljust(55)}}|
{%- endfor %}
---

---
{%- for ioc in iocs %}
### {{ioc.name}}

*Asset*: {{ (ioc.asset | string).ljust(50)}}

*Type*: {{ (ioc.devtype | string).ljust(50)}}
*Group*: {{ (ioc.devgroup | string).ljust(50)}} 


{%- if ioc.iocparam %}

#### IOC Params

| Name             | Value                            |
|------------------|----------------------------------|

{%- for p in ioc.iocparam %}
| {{ (p.name|string).ljust(16)}} | {{ (p.value| string).ljust(32) }} |
{%- endfor %}

---
{%- endif %}

{%- if ioc.iocinit %}
#### Common initializations

| Name             | Value                             | 
|------------------|-----------------------------------|
{%- for p in ioc.iocinit %}
| {{ (p.name|string).ljust(16)}} | {{ (p.value| string).ljust(32) }} |
{%- endfor %}
{%- endif %}

{%- if ioc.devices %}
#### Devices

| Name                         | HW conf                  | Group            | PV device prefix                 | Description                                                                                        |
|------------------------------|--------------------------|------------------|----------------------------------|----------------------------------------------------------------------------------------------------|

{%- for dev in ioc.devices %}
{%- set devinfo = "" %}
{%- set asset = ioc.asset | default("-") %}
{%- set devgroup = ioc.devgroup | default("-") %}

{%- if dev.devtype %}
{%- set devinfo = dev.devtype %}
{%- endif %}
{%- if dev.channel is defined %}
{%- set devinfo = devinfo ~ "chan " ~ dev.channel %}
{%- endif %}
{%- if dev.bus is defined %}
{%- set devinfo = devinfo ~ " " ~ dev.bus %}
{%- endif %}
{%- if dev.slot is defined %}
{%- set devinfo = devinfo ~ ":" ~ dev.slot %}
{%- endif %}
{%- if dev.func is defined %}
{%- set devinfo = devinfo ~ ":" ~ dev.func %}
{%- endif %}
{%- if dev.axid is defined %}
{%- set devinfo = devinfo ~ "axid " ~ dev.axid %}
{%- endif %}


{%- if dev.asset %}
{%- set asset = dev.asset %}
{%- endif %}
{%- if dev.devgroup %}
{%- set devgroup = dev.devgroup %}
{%- endif %}
|{{ (dev.name|string).ljust(30)}}|{{ (devinfo | string).ljust(26) }}|{{ (devgroup | string).ljust(18) }}|{{ (ioc.iocprefix ~":"~ dev.name).ljust(34) }}|{{ (asset | string).ljust(100)}}|
{%- endfor %}
{%- endif %}

{%- for dev in ioc.devices %}
{%- if dev.iocinit %}

#### ***{{dev.name}}*** initializations


| Name                              | Value                             |
|-----------------------------------|-----------------------------------|
{%- for p in dev.iocinit %}
| {{ (p.name|string).ljust(33)}} | {{ (p.value|string).ljust(34)}}|
{%- endfor %}
{%- endif %}
{%- endfor %}

---
{%- endfor %}

---

---

## Services
| Name             | URL                            | Balancer      | Chart                          |Description                                         |
|------------------|--------------------------------|---------------|--------------------------------|----------------------------------------------------|
{%- for service, details in services.items() %}
{%- set loadb = "-" %}
{%- set ingress = "-" %}
{%- if details.loadbalancer %}
{%- set loadb = details.loadbalancer %}
{%- endif %}
{%- if details.enable_ingress %}
{%- set ingress = "http://"~beamline~"-"~service~"."~epik8namespace %}
{%- endif %}
| {{ service.ljust(16)}} | {{ ingress.ljust(30) }} | {{ loadb.ljust(10)}} | {{ details.charturl.ljust(30) }} | {{ (details.asset | string).ljust(50)}} |
{%- endfor %}

---
{%- if applications %}
## Applications
| Name             | App Repo                       | Image                          |Description                                         |
|------------------|--------------------------------|--------------------------------|----------------------------------------------------|
{%- for app in applications %}
  {%- set repoapp = "-" %}
  {%- if app.gitRepoApp %}
    {%- set repoapp = app.gitRepoApp.url %}
  {%- endif %}
  | {{ app.name.ljust(16)}} | {{ repoapp.ljust(30) }} | {{ app.image.repository.ljust(30)}} | {{ (app.asset | string).ljust(50)}} |
{%- endfor %}
{%- endif %}
## Phoebus Settings
You can find phoebus settings for epik8s `{{ beamline }}` in **opi/settings.ini**


