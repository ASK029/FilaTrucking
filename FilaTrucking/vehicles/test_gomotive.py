import unittest
from unittest.mock import patch, MagicMock
from vehicles.gomotive_client import GoMotiveClient, GoMotiveOdometer, GoMotiveMaintenanceAlert

class TestGoMotiveClient(unittest.TestCase):
    def setUp(self):
        self.client = GoMotiveClient(base_url="https://api.test.com", api_key="test-key")

    @patch('requests.get')
    def test_test_connection_success(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"vehicles": []}
        mock_get.return_value = mock_response
        
        self.assertTrue(self.client.test_connection())
        mock_get.assert_called_once()
        headers = mock_get.call_args[1]['headers']
        self.assertEqual(headers["x-api-key"], "test-key")

    @patch('requests.get')
    def test_test_connection_failure(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_get.return_value = mock_response
        
        self.assertFalse(self.client.test_connection())

    @patch('requests.get')
    def test_test_connection_unauthorized(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_get.return_value = mock_response
        
        self.assertFalse(self.client.test_connection())

    @patch('requests.get')
    def test_fetch_vehicles(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"vehicles": [{"id": "v1", "number": "101"}]}
        mock_get.return_value = mock_response
        
        vehicles = self.client.fetch_vehicles()
        self.assertEqual(len(vehicles), 1)
        self.assertEqual(vehicles[0]["id"], "v1")

    @patch('requests.get')
    def test_fetch_vehicle(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"vehicle": {"id": "v1", "number": "101"}}
        mock_get.return_value = mock_response
        
        v = self.client.fetch_vehicle("v1")
        self.assertIsNotNone(v)
        self.assertEqual(v["vehicle"]["id"], "v1")

    @patch('requests.get')
    def test_fetch_odometer_from_vehicle_locations(self, mock_get):
        """The client reads vehicles[].vehicle.current_location.odometer from /vehicle_locations (GoMotive's real shape)."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"vehicles": [
            {"vehicle": {"id": 7, "current_location": {"odometer": 99.0}}},
            {"vehicle": {"id": "v1", "current_location": {"odometer": 1234.9}}},
        ]}
        mock_get.return_value = mock_response

        reading = self.client.fetch_odometer_reading("v1")
        self.assertIsNotNone(reading)
        self.assertEqual(reading.odometer, 1234)  # truncated to whole miles

    @patch('requests.get')
    def test_fetch_odometer_unknown_vehicle_or_missing_location_is_none(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"vehicles": [{"vehicle": {"id": "v1", "current_location": None}}]}
        mock_get.return_value = mock_response

        self.assertIsNone(self.client.fetch_odometer_reading("v1"))
        self.assertIsNone(self.client.fetch_odometer_reading("other"))

    @patch('requests.get')
    def test_fetch_odometer_ignores_a_garbage_value(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"vehicles": [{"vehicle": {"id": "v1", "current_location": {"odometer": "n/a"}}}]}
        mock_get.return_value = mock_response

        self.assertIsNone(self.client.fetch_odometer_reading("v1"))

    @patch('requests.get')
    def test_fetch_maintenance_alerts_reads_fault_codes(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"fault_codes": [
            {"fault_code": {"id": 5, "code_label": "P0300", "fmi_description": "Misfire", "vehicle": {"id": "v1"}}},
            {"fault_code": {"id": 6, "code_label": "X", "vehicle": {"id": "someone-else"}}},  # other truck: skipped
            {"fault_code": {}},  # empty wrapper: skipped
        ]}
        mock_get.return_value = mock_response

        alerts = self.client.fetch_maintenance_alerts("v1")
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].alert_id, "5")
        self.assertEqual(alerts[0].description, "P0300: Misfire")
        self.assertEqual(alerts[0].service_type, "Fault Code")

    @patch('requests.get')
    def test_fetch_maintenance_alerts_without_data_is_empty(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {}
        mock_get.return_value = mock_response

        self.assertEqual(self.client.fetch_maintenance_alerts("v1"), [])

if __name__ == '__main__':
    unittest.main()
