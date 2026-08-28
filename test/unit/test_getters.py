"""Tests for getters."""

from napalm.base import models
from napalm.base.exceptions import CommandErrorException
from napalm.base.test import helpers
from napalm.base.test.getters import BaseTestGetters
from napalm.base.test.getters import wrap_test_cases

from napalm_panos import PANOSDriver

import pytest

import requests


@pytest.mark.usefixtures("set_device_parameters")
class TestGetter(BaseTestGetters):
    """Test get_* methods."""

    @wrap_test_cases
    def test_get_interfaces(self, test_case):
        """Test get_interfaces."""
        get_interfaces = self.device.get_interfaces()
        if test_case in {
            "empty_interfaces",
        }:
            assert len(get_interfaces) == 0
        else:
            assert len(get_interfaces) > 0

            # for interface, interface_data in get_interfaces.items():
            #     assert helpers.test_model(InterfaceDict, interface_data)
        for interface, interface_data in get_interfaces.items():
            assert helpers.test_model(models.InterfaceDict, interface_data)

        return get_interfaces

    @wrap_test_cases
    def test_get_network_instances_filtered(self, test_case):
        """Test filtering network instances by their normalized name."""
        network_instances = self.device.get_network_instances(name="customer-a")
        assert self.device.get_network_instances(name="not-configured") == {}
        for network_instance in network_instances.values():
            assert helpers.test_model(models.NetworkInstanceDict, network_instance)
            assert helpers.test_model(models.NetworkInstanceStateDict, network_instance["state"])
            assert helpers.test_model(
                models.NetworkInstanceInterfacesDict,
                network_instance["interfaces"],
            )
        return network_instances


def test_get_network_instances_uses_rest(monkeypatch):
    """Use the versioned REST resource when REST transport is available."""
    driver = PANOSDriver(
        "firewall.example",
        "admin",
        "password",
        optional_args={"api_key": "secret", "api_transport": "rest"},
    )
    monkeypatch.setattr(
        driver,
        "_get_system_info",
        lambda: {"sw-version": "11.1.4-h1", "advanced-routing": "off"},
    )
    request = {}

    class Response:
        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {
                "@status": "success",
                "result": {
                    "entry": [
                        {
                            "@name": "default",
                            "interface": {"member": ["ethernet1/1"]},
                        }
                    ]
                },
            }

    def fake_get(url, **kwargs):
        request["url"] = url
        request.update(kwargs)
        return Response()

    monkeypatch.setattr(requests, "get", fake_get)

    assert driver.get_network_instances() == {
        "default": {
            "name": "default",
            "type": "DEFAULT_INSTANCE",
            "state": {"route_distinguisher": ""},
            "interfaces": {"interface": {"ethernet1/1": {}}},
        }
    }
    assert request["url"].endswith("/restapi/v11.1/Network/VirtualRouters")
    assert request["headers"] == {"X-PAN-KEY": "secret"}


def test_parse_rest_logical_router_vrfs():
    """Flatten logical-router child VRFs without colliding default names."""
    payload = {
        "@status": "success",
        "result": {
            "entry": [
                {
                    "@name": "edge-lr",
                    "vrf": {
                        "entry": [
                            {
                                "@name": "default",
                                "interface": {"member": ["ethernet1/1"]},
                            },
                            {
                                "@name": "blue",
                                "interface": {"member": "ethernet1/2.200"},
                            },
                        ]
                    },
                },
                {
                    "@name": "services-lr",
                    "vrf": {
                        "entry": {
                            "@name": "default",
                            "interface": {"member": ["tunnel.10"]},
                        }
                    },
                },
            ]
        },
    }

    result = PANOSDriver._parse_rest_network_instances(payload, True)

    assert set(result) == {
        "edge-lr/default",
        "edge-lr/blue",
        "services-lr/default",
    }
    assert result["edge-lr/default"]["type"] == "L3VRF"
    assert result["services-lr/default"]["interfaces"] == {"interface": {"tunnel.10": {}}}


def test_parse_rest_network_instances_accepts_zero_count():
    """Treat a successful empty REST result as authoritative."""
    payload = {"@status": "success", "result": {"@count": "0"}}

    assert PANOSDriver._parse_rest_network_instances(payload, False) == {}


def test_get_network_instances_rest_surfaces_errors(monkeypatch):
    """Do not silently change transports in explicit REST mode."""
    driver = PANOSDriver(
        "firewall.example",
        "admin",
        "password",
        optional_args={"api_transport": "rest"},
    )
    monkeypatch.setattr(
        driver,
        "_get_system_info",
        lambda: {"sw-version": "12.1.0", "advanced-routing": "on"},
    )

    def unsupported(*args, **kwargs):
        raise ValueError("logical-router REST resource unavailable")

    monkeypatch.setattr(driver, "_get_network_instances_rest", unsupported)

    with pytest.raises(CommandErrorException, match="logical-router REST resource unavailable"):
        driver.get_network_instances()


def test_get_network_instances_auto_falls_back_to_xml(monkeypatch):
    """Use XML when a PAN-OS REST resource is unavailable."""
    driver = PANOSDriver(
        "firewall.example",
        "admin",
        "password",
        optional_args={"api_transport": "auto"},
    )
    monkeypatch.setattr(
        driver,
        "_get_system_info",
        lambda: {"sw-version": "10.1.0", "advanced-routing": "off"},
    )

    def unavailable(*args, **kwargs):
        raise requests.ConnectionError("REST resource unavailable")

    expected = {
        "default": {
            "name": "default",
            "type": "DEFAULT_INSTANCE",
            "state": {"route_distinguisher": ""},
            "interfaces": {"interface": {}},
        }
    }
    monkeypatch.setattr(driver, "_get_network_instances_rest", unavailable)
    monkeypatch.setattr(driver, "_get_network_instances_xml", lambda advanced_routing: expected)

    assert driver.get_network_instances() == expected


def test_get_network_instances_auto_uses_xml_for_advanced_routing(monkeypatch):
    """Avoid an undocumented logical-router REST resource in auto mode."""
    driver = PANOSDriver(
        "firewall.example",
        "admin",
        "password",
        optional_args={"api_transport": "auto"},
    )
    monkeypatch.setattr(
        driver,
        "_get_system_info",
        lambda: {"sw-version": "12.1.0", "advanced-routing": "on"},
    )
    expected = {
        "edge-lr/default": {
            "name": "edge-lr/default",
            "type": "L3VRF",
            "state": {"route_distinguisher": ""},
            "interfaces": {"interface": {}},
        }
    }

    def unexpected_rest(*args, **kwargs):
        raise AssertionError("auto must not probe an undocumented REST resource")

    monkeypatch.setattr(driver, "_get_network_instances_rest", unexpected_rest)
    monkeypatch.setattr(driver, "_get_network_instances_xml", lambda advanced_routing: expected)

    assert driver.get_network_instances(name="edge-lr/default") == expected
    assert driver.get_network_instances(name="default") == {}
