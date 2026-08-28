"""Normalize PAN-OS routing instances into the NAPALM data model."""

import xml.etree.ElementTree  # nosec B405

NETWORK_INSTANCE_SEPARATOR = "/"


def _as_list(value):
    """Return PAN-OS singleton-or-list values as a list."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _rest_entries(container):
    """Extract REST API entries from a PAN-OS response container."""
    if not isinstance(container, dict):
        return []
    return _as_list(container.get("entry"))


def _rest_interface_names(entry):
    """Extract interface member names from a REST API router or VRF entry."""
    interface = entry.get("interface", {}) if isinstance(entry, dict) else {}
    if not isinstance(interface, dict):
        return []
    return [str(member) for member in _as_list(interface.get("member")) if member not in (None, "")]


def _xml_interface_names(entry):
    """Extract interface member names from an XML router or VRF entry."""
    return [str(member.text) for member in entry.findall("./interface/member") if member.text]


def _network_instance(router_name, interfaces, vrf_name=None):
    """Build one NAPALM network-instance dictionary."""
    if vrf_name is None:
        instance_name = router_name
        is_default = router_name == "default"
    else:
        instance_name = NETWORK_INSTANCE_SEPARATOR.join((router_name, vrf_name))
        # PAN-OS advanced routing can have several logical routers, each with a
        # child VRF named "default". None is a device-wide default instance.
        is_default = False

    return instance_name, {
        "name": instance_name,
        "type": "DEFAULT_INSTANCE" if is_default else "L3VRF",
        "state": {"route_distinguisher": ""},
        "interfaces": {"interface": {interface_name: {} for interface_name in interfaces}},
    }


def parse_rest_network_instances(payload, advanced_routing):
    """Normalize a PAN-OS REST API router response into NAPALM data."""
    if not isinstance(payload, dict):
        raise ValueError("PAN-OS REST API returned a non-object response")
    if payload.get("@status") != "success":
        raise ValueError(str(payload.get("msg", "PAN-OS REST API request failed")))

    result = payload.get("result")
    if not isinstance(result, dict):
        raise ValueError("PAN-OS REST API response did not contain a result object")
    entries = _rest_entries(result)
    instances = {}

    for router in entries:
        if not isinstance(router, dict):
            continue
        router_name = router.get("@name") or router.get("name")
        if not router_name:
            continue
        router_name = str(router_name)

        if not advanced_routing:
            instance_name, instance = _network_instance(router_name, _rest_interface_names(router))
            instances[instance_name] = instance
            continue

        for vrf in _rest_entries(router.get("vrf", {})):
            if not isinstance(vrf, dict):
                continue
            vrf_name = vrf.get("@name") or vrf.get("name")
            if not vrf_name:
                continue
            instance_name, instance = _network_instance(
                router_name,
                _rest_interface_names(vrf),
                str(vrf_name),
            )
            instances[instance_name] = instance

    return instances


def parse_xml_network_instances(configuration, advanced_routing):
    """Normalize PAN-OS running configuration XML into NAPALM data."""
    root = xml.etree.ElementTree.fromstring(configuration)  # nosec B314
    instances = {}

    if not advanced_routing:
        routers = root.findall("./result/config/devices/entry/network/virtual-router/entry")
        for router in routers:
            router_name = router.attrib.get("name")
            if not router_name:
                continue
            instance_name, instance = _network_instance(router_name, _xml_interface_names(router))
            instances[instance_name] = instance
        return instances

    routers = root.findall("./result/config/devices/entry/network/logical-router/entry")
    for router in routers:
        router_name = router.attrib.get("name")
        if not router_name:
            continue
        for vrf in router.findall("./vrf/entry"):
            vrf_name = vrf.attrib.get("name")
            if not vrf_name:
                continue
            instance_name, instance = _network_instance(
                router_name,
                _xml_interface_names(vrf),
                vrf_name,
            )
            instances[instance_name] = instance

    return instances
