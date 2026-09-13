"""Install pinned local browser VAD assets; no runtime CDN or model downloads."""
import argparse
import base64
import hashlib
import io
import json
import ssl
import sys
from pathlib import Path
import tarfile
import urllib.error
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


def _tls_context() -> ssl.SSLContext | None:
    """certifi's roots where they exist, the system's otherwise.

    Not belt and braces. A python.org build carries no CA bundle of its own
    and never had `Install Certificates.command` run, so `urlopen` fails to
    verify anything at all — while every other download here works, because
    huggingface_hub goes through requests and requests goes through certifi.
    The result was a script that could not run on the machine it was written
    for, failing with a raw SSL traceback.
    """
    try:
        import certifi
    except ImportError:
        return None
    return ssl.create_default_context(cafile=certifi.where())


def _fetch(url: str, context: ssl.SSLContext | None) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=120, context=context) as response:
            return response.read()
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, ssl.SSLCertVerificationError):
            raise SystemExit(
                f"Could not verify TLS for {url}.\n"
                "This interpreter has no CA bundle. Either install certifi into "
                "this venv:\n"
                "    uv pip install --python .venv/bin/python certifi\n"
                "or, on a python.org build, run its certificate installer once:\n"
                "    /Applications/Python\\ 3.11/Install\\ Certificates.command"
            ) from exc
        raise SystemExit(f"Could not download {url}: {reason}") from exc


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true",
                    help="re-download even if the manifest is already present")
    args = ap.parse_args()

    target = ROOT / 'models' / 'vad'
    if (target / 'manifest.json').exists() and not args.force:
        print(f'VAD assets already installed in {target} — --force to re-download')
        return 0

    context = _tls_context()
    assets = {}
    sources = []
    for url, integrity, names in PACKAGES:
        data = _fetch(url, context)
        if hashlib.sha512(data).digest() != base64.b64decode(integrity):
            raise SystemExit(f'Package integrity mismatch: {url}')
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
            for name in names:
                assets[name] = archive.extractfile('package/dist/' + name).read()
            metadata = json.load(archive.extractfile('package/package.json'))
            sources.append({k:metadata.get(k) for k in ('name','version','license','repository')})
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
    return 0


if __name__ == '__main__':
    sys.exit(main())
