from fastapi.testclient import TestClient
from voicebot import server


def test_vad_assets_require_complete_install_and_allowlisted_names(monkeypatch, tmp_path):
    monkeypatch.setattr(server, 'UI', tmp_path / 'ui' / 'demo-console.html')
    root = tmp_path / 'models' / 'vad'
    root.mkdir(parents=True)
    client = TestClient(server.app)
    assert client.get('/vad-assets/manifest.json').status_code == 404
    (root / 'manifest.json').write_text('{}')
    assert client.get('/vad-assets/manifest.json').status_code == 404
    for name in ('ort.wasm.min.js','ort-wasm-simd-threaded.mjs',
                 'ort-wasm-simd-threaded.wasm','silero_vad_v5.onnx'):
        (root / name).write_bytes(b'asset')
    assert client.get('/vad-assets/manifest.json').status_code == 200
    assert client.get('/vad-assets/ort-wasm-simd-threaded.wasm').headers['content-type'] == 'application/wasm'
    (root / 'secret').write_text('private')
    assert client.get('/vad-assets/secret').status_code == 404
    assert client.get('/audio-assets/secret').status_code == 404


def test_detector_scripts_are_served_locally():
    client = TestClient(server.app)
    for name in ('endpointing.js','neural-vad.js','vad-worker.js'):
        response = client.get('/audio-assets/' + name)
        assert response.status_code == 200
        assert response.headers['cache-control'] == 'no-store'
    assert client.get('/endpointing.js').status_code == 200
    assert client.get('/neural-vad.js').status_code == 200
