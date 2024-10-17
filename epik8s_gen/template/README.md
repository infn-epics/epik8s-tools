# EPIK8s Chart for {{ beamline }}

**URL**: `{{ giturl }}`
**REV**: `{{ gitrev }}`
**NAMESPACE**: `{{ epik8namespace }}`


---

## IO Controllers (IOCs)

{% for ioc in iocs %}
- **IOC Name**: **`{{ ioc.name }}`**
  - **Asset/Doc**: `{{ ioc.asset }}`
  - **Chart**: [{{ ioc.charturl }}]({{ ioc.charturl }})  
  - **Prefix**: `{{ ioc.iocprefix }}`
  - **Root**: `{{ ioc.iocroot }}`
  - **Type**: `{{ ioc.devtype }}`
  - **Group**: `{{ ioc.devgroup }}`
  {% if ioc.iocdir %}
  - **Template**: `{{ ioc.iocdir }}`
  {% endif %}
{% endfor %}

---

## Services

{% for service, details in services.items() %}
- **Service Name**: **`{{ service }}`**
  - **Chart**: [{{ details.charturl }}]({{ details.charturl }})  
  {% if details.loadbalancer %}
  - **Load Balancer IP**: `{{ details.loadbalancer }}`
  {% endif %}
  {% if details.enable_ingress %}
  - **Ingress URL**: [http://{{ beamline }}-{{ service }}.{{ epik8namespace }}](http://{{ beamline }}-{{ service }}.{{ epik8namespace }})  
  {% endif %}
{% endfor %}

---

## Applications

{% for app in applications %}
- **Application Name**: **`{{ app.name }}`**
{% endfor %}

## Phoebus Settings
You can find phoebus settings for epik8s `{{ beamline }}` in **opi/settings.ini**
