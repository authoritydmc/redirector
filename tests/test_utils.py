import unittest
from unittest.mock import patch, MagicMock, mock_open, call
import json
from datetime import datetime, timezone
import logging

from app.utils import utils

# Assuming the tests are run from the root directory 'r' (d:\codelab\r)
# and 'app' is in the PYTHONPATH.

# Disable logging for tests to keep output clean
logging.disable(logging.CRITICAL)

# Define a mock for app.CONSTANTS as it's used in utils.py
class MockConstants:
    data_source_redis = "redis_source"
    data_source_redirect = "db_redirect_source"
    data_source_upstream = "db_upstream_source"
    DATA_TYPE_STATIC = "static_type"
    DATA_TYPE_DYNAMIC = "dynamic_type"

# Path for patching names within the 'utils' module
UTILS_MODULE_PATH = 'app.utils.utils'

class TestUtils(unittest.TestCase):



    @patch(f'{UTILS_MODULE_PATH}._save_config')
    @patch(f'{UTILS_MODULE_PATH}.config')
    def test_get_config_key_exists(self, mock_config_module, mock_save_config):
        """Test get_config when key exists."""
        mock_config_module.get_configuration.return_value = {"existing_key": "existing_value"}

        result = utils.get_config("existing_key", "default_value")

        self.assertEqual(result, "existing_value")
        mock_config_module.get_configuration.assert_called_once()
        mock_save_config.assert_not_called()

    @patch(f'{UTILS_MODULE_PATH}._save_config')
    @patch(f'{UTILS_MODULE_PATH}.config')
    def test_get_config_key_not_exists_sets_default(self, mock_config_module, mock_save_config):
        """Test get_config when key does not exist, sets default and saves."""
        current_config = {}
        mock_config_module.get_configuration.return_value = current_config

        result = utils.get_config("new_key", "default_value")

        self.assertEqual(result, "default_value")
        self.assertEqual(current_config["new_key"], "default_value")
        mock_config_module.get_configuration.assert_called_once()
        mock_save_config.assert_called_once()

    @patch(f'{UTILS_MODULE_PATH}._save_config')
    @patch(f'{UTILS_MODULE_PATH}.config')
    def test_set_config(self, mock_config_module, mock_save_config):
        """Test set_config sets key and saves."""
        current_config = {}
        mock_config_module.get_configuration.return_value = current_config

        utils.set_config("another_key", "another_value")

        self.assertEqual(current_config["another_key"], "another_value")
        mock_config_module.get_configuration.assert_called_once()
        mock_save_config.assert_called_once()

    @patch(f'{UTILS_MODULE_PATH}._save_config')
    @patch(f'{UTILS_MODULE_PATH}.config')
    def test_set_config_overwrite(self, mock_config_module, mock_save_config):
        """Test set_config overwrites existing key and saves."""
        current_config = {"foo": "bar"}
        mock_config_module.get_configuration.return_value = current_config

        utils.set_config("foo", "baz")

        self.assertEqual(current_config["foo"], "baz")
        mock_config_module.get_configuration.assert_called_once()
        mock_save_config.assert_called_once()

    def test_destructure_subpath(self):
        test_cases = [
            ("raj", ("raj", [])),
            ("json/1", ("json", ["1"])),
            ("json/1/2", ("json", ["1", "2"])),
            ("/foo/bar", ("foo", ["bar"])),
            ("  leadingtrailing  ", ("leadingtrailing", [])),
            ("  lead/trail/path  ", ("lead", ["trail", "path"])),
            ("/", ("", [])),
            ("", ("", [])),
            ("UPPER/case/Test", ("upper", ["case", "test"])),
        ]
        for subpath_input, expected_output in test_cases:
            with self.subTest(subpath_input=subpath_input):
                self.assertEqual(utils.destructureSubPath(subpath_input), expected_output)

    def test_destructure_subpath_edge_cases(self):
        self.assertEqual(utils.destructureSubPath(None), ("", []))
        self.assertEqual(utils.destructureSubPath(123), ("123", []))
        self.assertEqual(utils.destructureSubPath("/"), ("", []))
        self.assertEqual(utils.destructureSubPath(""), ("", []))

    def test_replace_placeholders(self):
        self.assertEqual(utils.replacePlaceHolders("api/{id}/data", "123"), "api/123/data")
        self.assertEqual(utils.replacePlaceHolders("api/{id}/{name}", "val"), "api/val/val")
        self.assertEqual(utils.replacePlaceHolders("no_placeholders", "val"), "no_placeholders")

    def test_replace_placeholders_multiple_values(self):
        self.assertEqual(utils.replacePlaceHolders("api/{id}/{name}", ["123", "bob"]), "api/123/bob")
        self.assertEqual(utils.replacePlaceHolders("api/{id}/{name}/{id}", ["1", "x"]), "api/1/x/1")
        # Test repeated use of the last value if there are more placeholders than values
        self.assertEqual(utils.replacePlaceHolders("api/{id}/{name}/{extra}", ["1", "bob"]), "api/1/bob/bob")

    def test_get_placeholder_vars(self):
        self.assertEqual(utils.get_placeholder_vars("api/{id}/data/{name}"), ["id", "name"])
        self.assertEqual(utils.get_placeholder_vars("no_placeholders"), [])

    def test_get_placeholder_vars_brackets(self):
        self.assertEqual(utils.get_placeholder_vars("api/[foo]/bar/{baz}"), ["foo", "baz"])

    def test_get_config_default_types(self):
        # Test with int, bool, etc
        with patch(f'{UTILS_MODULE_PATH}.config') as mock_config_module, \
             patch(f'{UTILS_MODULE_PATH}._save_config') as mock_save_config:
            mock_config_module.get_configuration.return_value = {}
            result = utils.get_config("int_key", 42)
            self.assertEqual(result, 42)
            result = utils.get_config("bool_key", True)
            self.assertEqual(result, True)


if __name__ == '__main__':
    unittest.main(argv=['first-arg-is-ignored'], exit=False)