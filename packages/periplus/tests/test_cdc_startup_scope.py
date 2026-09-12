"""Operational CDC calls must scope native work before historical scans."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from uuid import UUID

from periplus.materialization.live import LiveCdcConnection, _consumer_name


class CdcStartupScopeTests(unittest.TestCase):
    def setUp(self):
        self.connection = Mock()
        self.cdc = LiveCdcConnection.__new__(LiveCdcConnection)
        self.cdc.catalogue = SimpleNamespace(config=SimpleNamespace(alias='lake'),trusted_connection=self.connection)

    def test_existing_generation_scopes_doctor_before_work(self):
        generation = SimpleNamespace(id=UUID(int=42))
        with patch('periplus.materialization.live.state.active_generation',return_value=generation):
            self.cdc.bootstrap()
        self.connection.execute.assert_called_once_with(
            f"SELECT * FROM cdc_doctor('lake', consumer := '{_consumer_name(generation.id)}')")

    def test_first_install_initializes_metadata_without_generation(self):
        with patch('periplus.materialization.live.state.active_generation',return_value=None):
            self.cdc.bootstrap()
        self.connection.execute.assert_called_once_with("SELECT * FROM cdc_doctor('lake')")

    def test_existence_uses_native_consumer_argument_and_preserves_absence(self):
        self.connection.execute.return_value.fetchall.return_value=[]
        self.assertFalse(self.cdc._consumer_exists('wanted'))
        self.connection.execute.assert_called_once_with("SELECT consumer_name FROM cdc_consumer_stats('lake', consumer := 'wanted')")
        self.connection.execute.return_value.fetchall.return_value=[('wanted',)]
        self.assertTrue(self.cdc._consumer_exists('wanted'))
