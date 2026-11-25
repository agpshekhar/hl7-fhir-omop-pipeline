"""Checks on the committed BridgeLink channel, without running the engine.

The channel is an XML export. These tests pin the settings the rest of the pipeline
depends on, so an edit in the Administrator that changes one of them fails here first.
"""

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from labpipe.bridgelink.deploy import channel_id

ROOT = Path(__file__).resolve().parent.parent
CHANNEL = ROOT / "interfaces" / "bridgelink" / "lab-results-in.xml"
COMPOSE = ROOT / "docker-compose.yml"


@pytest.fixture(scope="module")
def channel():
    return ET.parse(CHANNEL).getroot()


def test_channel_has_a_fixed_id_and_name(channel):
    # A fixed id lets the deploy job overwrite the same channel on every run.
    assert channel_id(CHANNEL.read_text()) == "5d0c3f8e-7a1b-4c2d-9e4f-0a1b2c3d4e5f"
    assert channel.findtext("name") == "lab-results-in"


def test_source_is_an_mllp_listener_on_port_6661(channel):
    props = channel.find("sourceConnector/properties")
    assert channel.findtext("sourceConnector/transportName") == "TCP Listener"
    assert props.findtext("listenerConnectorProperties/port") == "6661"
    assert props.findtext("transmissionModeProperties/pluginPointName") == "MLLP"
    assert props.findtext("transmissionModeProperties/startOfMessageBytes") == "0B"
    assert props.findtext("transmissionModeProperties/endOfMessageBytes") == "1C0D"


def test_ack_waits_for_the_staging_write(channel):
    # AA only after the destination has written the row; AE if the write failed.
    variable = channel.findtext(
        "sourceConnector/properties/sourceConnectorProperties/responseVariable"
    )
    assert variable == "Auto-generate (Destinations completed)"


def test_channel_reads_and_writes_hl7_v2(channel):
    for transformer in channel.iter("transformer"):
        assert transformer.findtext("inboundDataType") == "HL7V2"
        assert transformer.findtext("outboundDataType") == "HL7V2"


def test_destination_inserts_into_staging_and_skips_duplicates(channel):
    destinations = channel.findall("destinationConnectors/connector")
    assert len(destinations) == 1
    dest = destinations[0]
    assert dest.findtext("transportName") == "Database Writer"
    assert dest.findtext("enabled") == "true"
    script = dest.findtext("properties/query")
    assert "INSERT INTO staging.raw_message" in script
    assert "ON CONFLICT (msh10) DO NOTHING" in script


def test_channel_file_holds_no_password(channel):
    dest = channel.find("destinationConnectors/connector/properties")
    assert dest.findtext("password") in (None, "")
    assert "POSTGRES_PASSWORD" in dest.findtext("query")  # read from the environment instead


def test_channel_starts_when_deployed(channel):
    assert channel.findtext("properties/initialState") == "STARTED"


def test_channel_version_matches_the_pinned_image(channel):
    pinned = re.search(r"innovarhealthcare/bridgelink:(\S+)", COMPOSE.read_text()).group(1)
    assert channel.get("version") == pinned
