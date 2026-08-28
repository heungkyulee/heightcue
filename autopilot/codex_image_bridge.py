"""Deterministic Hermes Codex image bridge for HeightCue.

Runs Hermes's configured image-generation dispatcher in a *separate* process
using the Hermes venv interpreter, so credential refresh, OAuth storage, and
profile isolation stay owned by Hermes. This module never reads, copies, or
serializes the Codex OAuth token — it only passes a prompt, an aspect ratio,
and local source-image paths across the process boundary, and reads back a
JSON result.

Hard contract enforced here:

* at least one verified source image (text-only generation is rejected)
* an explicit output path
* provider ``openai-codex`` / tier ``gpt-image-2-medium`` (underlying model
  ``gpt-image-2``), portrait aspect
* anything else fails closed, loudly, without writing an output file

See ``test_codex_image_bridge.py`` for the executable contract, including a
compatibility test that inspects the *installed* Hermes files so an upgrade
that renames the dispatcher or swaps the provider fails instead of silently
degrading.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from typing import Any, Dict, Iterable, List, Optional

# ---------------------------------------------------------------------------
# Pinned contract
# ---------------------------------------------------------------------------

MODEL_ALIAS = "gpt-image-gen-2"
HERMES_PROVIDER = "openai-codex"
HERMES_MODEL = "gpt-image-2-medium"
PROVIDER_MODEL = "gpt-image-2"
ASPECT_RATIO = "portrait"
PORTRAIT_PIXEL_SIZE = "1024x1536"

HERMES_HOME = os.path.expanduser(
    os.environ.get("HERMES_HOME", "~/.hermes"))
HERMES_AGENT_DIR = os.path.join(HERMES_HOME, "hermes-agent")
HERMES_PYTHON = os.path.join(HERMES_AGENT_DIR, "venv", "bin", "python")
HERMES_DISPATCHER_FILE = os.path.join(
    HERMES_AGENT_DIR, "tools", "image_generation_tool.py")
HERMES_CODEX_PLUGIN_FILE = os.path.join(
    HERMES_AGENT_DIR, "plugins", "image_gen", "openai-codex", "__init__.py")

DEFAULT_TIMEOUT = 600


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class CodexBridgeError(Exception):
    """Base class for every bridge failure."""


class TextOnlyRejected(CodexBridgeError):
    """No source image was supplied — text-only use is not permitted."""


class SourceImageError(CodexBridgeError):
    """A source image path is missing, empty, or not a regular file."""


class OutputPathError(CodexBridgeError):
    """The caller did not supply an explicit output path."""


class PromptError(CodexBridgeError):
    """The prompt was empty or whitespace-only."""


class ProviderMismatch(CodexBridgeError):
    """The dispatcher answered from a provider/model we did not pin."""


class AspectMismatch(CodexBridgeError):
    """The dispatcher did not actually return the pinned portrait output."""


class DispatchError(CodexBridgeError):
    """The dispatcher failed, or returned an unusable result."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _verify_source_images(source_images: Optional[Iterable[str]]) -> List[str]:
    paths = [p for p in (source_images or []) if isinstance(p, str) and p.strip()]
    if not paths:
        raise TextOnlyRejected(
            "codex_image_bridge requires at least one source image; "
            "text-only generation is not permitted by this bridge."
        )

    verified: List[str] = []
    for raw in paths:
        path = os.path.abspath(os.path.expanduser(raw))
        if not os.path.exists(path):
            raise SourceImageError(f"source image does not exist: {path}")
        if not os.path.isfile(path):
            raise SourceImageError(f"source image is not a regular file: {path}")
        if os.path.getsize(path) <= 0:
            raise SourceImageError(f"source image is empty: {path}")
        verified.append(path)
    return verified


def _subprocess_runner(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Call the Hermes dispatcher inside the Hermes venv, in its own process.

    Only the payload crosses the boundary. Credentials are resolved by Hermes
    inside that process and never returned here.
    """
    if not os.path.exists(HERMES_PYTHON):
        raise DispatchError(f"Hermes venv python not found: {HERMES_PYTHON}")
    if not os.path.exists(HERMES_DISPATCHER_FILE):
        raise DispatchError(
            f"Hermes image dispatcher not found: {HERMES_DISPATCHER_FILE}")

    script = (
        "import json, sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "from tools.image_generation_tool import _handle_image_generate\n"
        "args = json.loads(sys.argv[2])\n"
        "out = _handle_image_generate(args)\n"
        "if isinstance(out, (bytes, bytearray)):\n"
        "    out = out.decode('utf-8')\n"
        "if isinstance(out, str):\n"
        "    try:\n"
        "        out = json.loads(out)\n"
        "    except Exception:\n"
        "        out = {'success': False, 'error': out}\n"
        "sys.stdout.write('@@RESULT@@' + json.dumps(out))\n"
    )

    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)

    try:
        proc = subprocess.run(
            [HERMES_PYTHON, "-c", script, HERMES_AGENT_DIR,
             json.dumps(payload)],
            capture_output=True, text=True, timeout=DEFAULT_TIMEOUT,
            cwd=HERMES_AGENT_DIR, env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise DispatchError(f"Hermes dispatcher timed out: {exc}") from exc

    marker = "@@RESULT@@"
    if marker not in proc.stdout:
        raise DispatchError(
            "Hermes dispatcher produced no result "
            f"(exit={proc.returncode}): {proc.stderr.strip()[-2000:]}")

    blob = proc.stdout.split(marker, 1)[1].strip()
    try:
        result = json.loads(blob)
    except json.JSONDecodeError as exc:
        raise DispatchError(f"Unparseable dispatcher result: {exc}") from exc
    if not isinstance(result, dict):
        raise DispatchError(f"Dispatcher result was not an object: {type(result)}")
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def edit_image(prompt: str, source_images: Optional[Iterable[str]],
               output_path: str, *, runner=None) -> Dict[str, Any]:
    """Edit ``source_images`` with ``prompt`` via Hermes Codex; return a manifest.

    ``runner`` is an injection seam for tests; production uses the real
    Hermes-venv subprocess runner.
    """
    if not isinstance(output_path, str) or not output_path.strip():
        raise OutputPathError(
            "an explicit output_path is required (no implicit destinations)")
    if not isinstance(prompt, str) or not prompt.strip():
        raise PromptError("prompt must be a non-empty string")

    verified = _verify_source_images(source_images)
    prompt = prompt.strip()
    out_abs = os.path.abspath(os.path.expanduser(output_path))

    payload: Dict[str, Any] = {
        "prompt": prompt,
        "aspect_ratio": ASPECT_RATIO,
        "image_url": verified[0],
        "reference_image_urls": verified[1:],
    }

    dispatch = runner or _subprocess_runner
    result = dispatch(payload)
    if not isinstance(result, dict):
        raise DispatchError(f"runner returned {type(result)}, expected dict")

    if not result.get("success"):
        raise DispatchError(
            f"Hermes image generation failed: {result.get('error')!r} "
            f"(type={result.get('error_type')!r})")

    provider = result.get("provider")
    model = result.get("model")
    if provider != HERMES_PROVIDER or model != HERMES_MODEL:
        raise ProviderMismatch(
            "refusing result from unpinned backend: "
            f"provider={provider!r} model={model!r}; "
            f"expected provider={HERMES_PROVIDER!r} model={HERMES_MODEL!r}")

    if result.get("modality") != "image":
        raise DispatchError(
            f"expected an image-to-image edit, got modality={result.get('modality')!r}")

    observed_aspect = result.get("aspect_ratio")
    if observed_aspect != ASPECT_RATIO:
        raise AspectMismatch(
            "refusing non-portrait result: "
            f"observed aspect_ratio={observed_aspect!r}; "
            f"expected {ASPECT_RATIO!r}")

    observed_pixels = result.get("pixel_size")
    if observed_pixels is not None and observed_pixels != PORTRAIT_PIXEL_SIZE:
        raise AspectMismatch(
            "refusing non-portrait result: "
            f"observed pixel_size={observed_pixels!r}; "
            f"expected {PORTRAIT_PIXEL_SIZE!r}")

    produced = result.get("image")
    if not isinstance(produced, str) or not produced.strip():
        raise DispatchError("dispatcher returned no image path")
    produced = os.path.abspath(os.path.expanduser(produced))
    if not os.path.isfile(produced) or os.path.getsize(produced) <= 0:
        raise DispatchError(f"produced image missing or empty: {produced}")

    parent = os.path.dirname(out_abs)
    if parent:
        os.makedirs(parent, exist_ok=True)
    shutil.copyfile(produced, out_abs)

    return {
        "model_alias": MODEL_ALIAS,
        "hermes_provider": HERMES_PROVIDER,
        "hermes_model": HERMES_MODEL,
        "provider_model": PROVIDER_MODEL,
        "aspect_ratio": observed_aspect,
        "observed_aspect_ratio": observed_aspect,
        "requested_aspect_ratio": ASPECT_RATIO,
        "observed_pixel_size": observed_pixels,
        "prompt": prompt,
        "output_path": out_abs,
        "output_sha256": _sha256_file(out_abs),
        "output_bytes": os.path.getsize(out_abs),
        "source_images": [
            {"path": p, "sha256": _sha256_file(p)} for p in verified
        ],
        "size": result.get("size"),
        "quality": result.get("quality"),
        "pixel_size": observed_pixels,
        "image_source": result.get("image_source"),
        "input_image_count": result.get("input_image_count"),
    }
