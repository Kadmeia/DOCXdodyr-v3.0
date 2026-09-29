import json
from pathlib import Path
import os
import re

from app_paths import atomic_write_json
from document_restorer import _sha256, META_KEY, BINDING_SCHEMA_V2


def publish_binding(decoder, outputs, run_id, emit_sidecars=False):
    decoder_raw = Path(decoder)
    if decoder_raw.is_symlink():
        raise ValueError("Decoder must not be a symbolic link")
    decoder = decoder_raw.resolve(strict=True)
    normalized_outputs = []
    for value in outputs:
        output_raw = Path(value)
        if output_raw.is_symlink():
            raise ValueError("Bound output must not be a symbolic link")
        normalized_outputs.append(output_raw.resolve(strict=True))
    outputs = normalized_outputs
    if not run_id or not re.fullmatch(r"[A-Za-z0-9_-]+", str(run_id)):
        raise ValueError("Invalid decoder run ID")
    records = []
    for output in outputs:
        try:
            relative = str(output.relative_to(decoder.parent))
        except ValueError:
            # Falling back to basename silently created a binding that could
            # never be verified: the manifest pointed at decoder.parent/name
            # while the actual output lived elsewhere.  Reject the invalid
            # topology instead of publishing an unusable decoder.
            raise ValueError("Bound output must be below the decoder directory")
        records.append({
            "path": relative,
            "filename": output.name,
            "sha256": _sha256(output),
        })

    if emit_sidecars:
        # Legacy sidecar mode: mapping in decoder, binding in .provenance.json and .manifest.json
        decoder_hash = _sha256(decoder)
        for output, record in zip(outputs, records):
            atomic_write_json(output.with_name(output.name + ".provenance.json"), {
                "schema": "docxdodyr.decoder-binding/v1", "run_id": str(run_id),
                "decoder_path": os.path.relpath(decoder, output.parent),
                "decoder_sha256": decoder_hash, "output_sha256": record["sha256"],
            })
        atomic_write_json(decoder.with_name(decoder.stem + ".manifest.json"), {
            "schema": "docxdodyr.batch-manifest/v1", "run_id": str(run_id),
            "complete": True, "errors": [],
            "decoder": {"path": decoder.name, "sha256": decoder_hash},
            "documents": [{"outputs": records}],
        })
    else:
        # Clean mode without sidecar files: embed binding directly in decoder
        try:
            decoder_data = json.loads(decoder.read_text(encoding="utf-8"))
        except Exception:
            decoder_data = {}
        if not isinstance(decoder_data, dict):
            decoder_data = {}
        decoder_data[META_KEY] = {
            "schema": BINDING_SCHEMA_V2,
            "run_id": str(run_id),
            "complete": True,
            "documents": records,
        }
        atomic_write_json(decoder, decoder_data)
        if os.name == "posix":
            try:
                os.chmod(decoder, 0o600)
            except OSError:
                pass
