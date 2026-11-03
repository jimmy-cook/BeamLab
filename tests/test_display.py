import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from beam_lab.display import initial_size


@pytest.mark.parametrize("dpi,width,height", [(96, 1366, 768), (144, 1920, 1080),
                                               (192, 3840, 2160), (288, 3840, 2160)])
def test_initial_window_fits_display(dpi, width, height):
    actual_width, actual_height = initial_size(width, height, dpi)
    assert 0 < actual_width < width
    assert 0 < actual_height < height
    assert actual_width <= 1240*dpi/96
    assert actual_height <= 860*dpi/96


def test_executable_declares_native_dpi_rendering():
    manifest = Path(__file__).resolve().parents[1]/"windows.manifest"
    tree = ET.parse(manifest)
    assert tree.find(".//{http://schemas.microsoft.com/SMI/2016/WindowsSettings}dpiAwareness").text == "PerMonitorV2, PerMonitor"
    assert tree.find(".//{http://schemas.microsoft.com/SMI/2005/WindowsSettings}dpiAware").text == "true/pm"
