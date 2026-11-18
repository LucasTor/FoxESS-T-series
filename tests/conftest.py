"""Shared fixtures."""
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load the integration from custom_components."""
    yield


@pytest.fixture
def no_reader():
    """Keep the receive loop from opening real connections."""
    with patch("custom_components.foxess_tseries.reader.InverterReader.start"):
        yield
