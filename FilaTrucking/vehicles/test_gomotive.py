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
    def test_fetch_odometer_flat(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"odometer": 1234.9}
        mock_get.return_value = mock_response
        
        reading = self.client.fetch_odometer_reading("v1")
        self.assertIsNotNone(reading)
        self.assertEqual(reading.odometer, 1234)

    @patch('requests.get')
    def test_fetch_odometer_nested(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"odometer": {"value": 555.5}}
        mock_get.return_value = mock_response
        
        reading = self.client.fetch_odometer_reading("v1")
        self.assertIsNotNone(reading)
        self.assertEqual(reading.odometer, 555)

    @patch('requests.get')
    def test_fetch_maintenance_alerts(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "alerts": [
                {
                    "mileage_at_service": 10000,
                    "next_service_mileage": 15000,
                    "description": "Oil Change",
                    "service_type": "Maintenance"
                }
            ]
        }
        mock_get.return_value = mock_response
        
        alerts = self.client.fetch_maintenance_alerts("v1")
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].description, "Oil Change")
        self.assertEqual(alerts[0].next_service_mileage, 15000)

if __name__ == '__main__':
    unittest.main()
