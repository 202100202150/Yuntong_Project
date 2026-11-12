"""Persistence and authenticated HTTP contract tests, with no cameras or GPU."""
from http import HTTPStatus
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from camera_bridge import CameraBridgeService, make_handler
from spatial_localization import SpatialLocalizer, default_config, validate_config


class SpatialApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.service = object.__new__(CameraBridgeService)
        self.service._spatial_lock = threading.RLock()
        self.service._spatial_config_path = Path(self.temporary.name) / 'spatial-config.json'
        self.service._spatial_config = validate_config(default_config())
        self.service._spatial_localizer = SpatialLocalizer(self.service._spatial_config)
        self.service._spatial_cache_key = None
        self.service._spatial_cache = None
        self.token = 'spatial-api-test-token-with-32-characters'
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.service, self.token,
            max_snapshot_bytes=1024 * 1024, shutdown_callback=lambda: None))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_address[1]}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.temporary.cleanup()

    def request(self, payload=None, token=True, path='/v1/spatial/config'):
        body = None if payload is None else json.dumps(payload).encode()
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['Authorization'] = f'Bearer {self.token}'
        request = urllib.request.Request(self.base + path, data=body, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            return exc.code, json.load(exc)

    def test_configuration_requires_existing_bridge_auth(self):
        self.assertEqual(self.request(token=False)[0], HTTPStatus.UNAUTHORIZED)

    def test_rig_update_is_persisted_and_changes_actual_camera_positions(self):
        status, payload = self.request()
        self.assertEqual(status, HTTPStatus.OK)
        config = payload['config']
        config['rig']['baselineMeters'] = 0.4
        config['rig']['midpoint']['y'] = 1.6
        config['cameras']['camera1']['pitchDeg'] = 12
        self.assertEqual(self.request({'config': config})[0], HTTPStatus.OK)
        written = json.loads(self.service._spatial_config_path.read_text(encoding='utf-8'))
        self.assertEqual(written['cameras']['camera1']['pitchDeg'], 12)
        spatial = self.service.spatial_payload({'camera1': None, 'camera2': None})
        self.assertAlmostEqual(spatial['cameras']['camera1']['x'], -0.2)
        self.assertAlmostEqual(spatial['cameras']['camera2']['x'], 0.2)
        self.assertAlmostEqual(spatial['cameras']['camera1']['y'], 1.6)
        self.assertEqual(spatial['targets'], [])
        self.assertEqual(spatial['state'], 'uncalibrated')

    def test_invalid_baseline_does_not_replace_previous_configuration(self):
        original = self.service.spatial_config_payload()['config']
        invalid = json.loads(json.dumps(original))
        invalid['rig']['baselineMeters'] = 0
        self.assertEqual(self.request({'config': invalid})[0], HTTPStatus.BAD_REQUEST)
        self.assertEqual(self.service.spatial_config_payload()['config'], original)
        self.assertFalse(self.service._spatial_config_path.exists())

    def test_extrinsics_draft_changes_runtime_only_when_saved(self):
        original = self.service.spatial_config_payload()['config']
        config = self.service.spatial_config_payload()['config']
        config['extrinsics']['enabled'] = True
        rotation = [[-1, 0, 0], [0, -1, 0], [0, 0, 1]]
        for key, center in [('camera1', [8, 1, 2]), ('camera2', [4, 1, 2])]:
            config['extrinsics'][key] = {
                'rotationCameraToBoard': rotation,
                'translationCameraInBoard': center,
            }

        path = '/v1/spatial/config/derive'
        self.assertEqual(self.request({'config': config}, token=False, path=path)[0], HTTPStatus.UNAUTHORIZED)
        status, draft = self.request({'config': config}, path=path)
        self.assertEqual(status, HTTPStatus.OK, draft)
        rig = draft['config']['rig']
        self.assertEqual(rig['geometrySource'], 'extrinsics')
        self.assertAlmostEqual(rig['baselineMeters'], 4.0)
        self.assertEqual(rig['midpoint'], {'x': 0.0, 'y': 0.0, 'z': 0.0})
        self.assertEqual(rig['cameraPositions']['camera1'], {
            'x': 2.0, 'y': 0.0, 'z': 0.0, 'side': 'left', 'distanceToOriginMeters': 2.0,
        })
        self.assertEqual(rig['cameraPositions']['camera2'], {
            'x': -2.0, 'y': 0.0, 'z': 0.0, 'side': 'right', 'distanceToOriginMeters': 2.0,
        })
        self.assertEqual(self.service.spatial_config_payload()['config'], original)
        self.assertFalse(self.service._spatial_config_path.exists())

        status, saved = self.request(draft)
        self.assertEqual(status, HTTPStatus.OK, saved)
        self.assertEqual(saved, draft)
        written = json.loads(self.service._spatial_config_path.read_text(encoding='utf-8'))
        self.assertEqual(written, saved['config'])
        spatial = self.service.spatial_payload({'camera1': None, 'camera2': None})
        self.assertEqual(spatial['cameras'], {
            key: {axis: position[axis] for axis in 'xyz'}
            for key, position in rig['cameraPositions'].items()
        })

        config['extrinsics']['camera2']['translationCameraInBoard'] = [8, 1, 2]
        self.assertEqual(self.request({'config': config}, path=path)[0], HTTPStatus.BAD_REQUEST)
        self.assertEqual(self.service.spatial_config_payload(), saved)
        self.assertEqual(json.loads(self.service._spatial_config_path.read_text(encoding='utf-8')), written)


if __name__ == '__main__':
    unittest.main()
