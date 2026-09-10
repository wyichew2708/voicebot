"""Install pinned local browser VAD assets; no runtime CDN or model downloads."""
import base64
import hashlib
import io
import json
from pathlib import Path
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = [
    ('https://registry.npmjs.org/@ricky0123/vad-web/-/vad-web-0.0.30.tgz',
     'cJyYrh4YeeUBJcbR9Bic/bFDyB9qBkAepvpuWM3vLxnAi7bC3VHzf51UeNdT+OtY4D7MLAgV8iJMc4z41ZnaWg==',
     ['silero_vad_v5.onnx']),
    ('https://registry.npmjs.org/onnxruntime-web/-/onnxruntime-web-1.29.0.tgz',
     'LuQlpX6MFLJZu756erwUeb1mNfoJGbs1kzDwJGNlf5RvfYMdqhcY3vNpDPK40CUV2HoWTkIj+uS0o36GFHjeYw==',
     ['ort.wasm.min.js', 'ort-wasm-simd-threaded.mjs', 'ort-wasm-simd-threaded.wasm']),
]


def main():
    assets = {}
    sources = []
    for url, integrity, names in PACKAGES:
        with urllib.request.urlopen(url, timeout=120) as response:
            data = response.read()
        if hashlib.sha512(data).digest() != base64.b64decode(integrity):
            raise ValueError(f'Package integrity mismatch: {url}')
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
            for name in names:
                assets[name] = archive.extractfile('package/dist/' + name).read()
            metadata = json.load(archive.extractfile('package/package.json'))
            sources.append({k:metadata.get(k) for k in ('name','version','license','repository')})
    target = ROOT / 'models' / 'vad'
    target.mkdir(parents=True, exist_ok=True)
    # Publish the manifest last. The server never enables a partial install.
    for name, content in assets.items():
        tmp = target / (name + '.tmp')
        tmp.write_bytes(content)
        tmp.replace(target / name)
    manifest = {'model':'silero-v5', 'runtime':'onnxruntime-web-1.29.0',
                'sources':sources, 'sha256':{n:hashlib.sha256(b).hexdigest() for n,b in assets.items()}}
    tmp = target / 'manifest.tmp'
    tmp.write_text(json.dumps(manifest, indent=2) + '\n')
    tmp.replace(target / 'manifest.json')
    print(f'Installed {len(assets)} verified VAD assets in {target}')


if __name__ == '__main__':
    main()
