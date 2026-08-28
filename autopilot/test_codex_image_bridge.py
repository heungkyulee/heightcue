"""Tests for the deterministic Hermes Codex image bridge.

No test here performs a paid API call. The dispatcher is always faked; the
compatibility test only inspects the installed Hermes source files on disk.
"""

import ast
import hashlib
import json
import os
import shutil
import struct
import sys
import tempfile
import textwrap
import unittest
import zlib

import codex_image_bridge as bridge


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _png_bytes(width=1024, height=1536, tag=b"\x00"):
    """Minimal but structurally valid PNG so hashing/copying is realistic."""

    def chunk(kind, data):
        payload = kind + data
        return struct.pack(">I", len(data)) + payload + struct.pack(
            ">I", zlib.crc32(payload) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = tag * 16
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def _sha256_file(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


class _FakeRunner:
    """Stands in for the Hermes-venv subprocess dispatcher call."""

    def __init__(self, result=None, produced_png=None):
        self.result = result
        self.produced_png = produced_png
        self.calls = []

    def __call__(self, payload):
        self.calls.append(payload)
        result = dict(self.result or {})
        if self.produced_png is not None:
            result["image"] = self.produced_png
        return result


class BridgeTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="codexbridge-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

        self.src_a = os.path.join(self.tmp, "product_a.png")
        with open(self.src_a, "wb") as fh:
            fh.write(_png_bytes(tag=b"\x11"))

        self.src_b = os.path.join(self.tmp, "product_b.png")
        with open(self.src_b, "wb") as fh:
            fh.write(_png_bytes(tag=b"\x22"))

        self.produced = os.path.join(self.tmp, "hermes_cache_out.png")
        with open(self.produced, "wb") as fh:
            fh.write(_png_bytes(tag=b"\x33"))

        self.out = os.path.join(self.tmp, "run", "workspace", "shot_01.png")

    def good_result(self):
        return {
            "success": True,
            "image": self.produced,
            "model": "gpt-image-2-medium",
            "provider": "openai-codex",
            "prompt": "portrait edit",
            "aspect_ratio": "portrait",
            "modality": "image",
            "size": "941x1672",
            "quality": "medium",
            "input_image_count": 1,
            "image_source": "final",
            "pixel_size": "941x1672",
        }


# ---------------------------------------------------------------------------
# contract constants
# ---------------------------------------------------------------------------


class ContractConstantsTest(unittest.TestCase):
    def test_pinned_identifiers(self):
        self.assertEqual(bridge.MODEL_ALIAS, "gpt-image-gen-2")
        self.assertEqual(bridge.HERMES_PROVIDER, "openai-codex")
        self.assertEqual(bridge.HERMES_MODEL, "gpt-image-2-medium")
        self.assertEqual(bridge.PROVIDER_MODEL, "gpt-image-2")
        self.assertEqual(bridge.ASPECT_RATIO, "portrait")


# ---------------------------------------------------------------------------
# input validation
# ---------------------------------------------------------------------------


class InputValidationTest(BridgeTestBase):
    def test_rejects_text_only_use(self):
        runner = _FakeRunner(self.good_result())
        with self.assertRaises(bridge.TextOnlyRejected):
            bridge.edit_image(prompt="make a poster", source_images=[],
                              output_path=self.out, runner=runner)
        self.assertEqual(runner.calls, [], "must not dispatch for text-only")

    def test_rejects_missing_source_image(self):
        runner = _FakeRunner(self.good_result())
        missing = os.path.join(self.tmp, "nope.png")
        with self.assertRaises(bridge.SourceImageError):
            bridge.edit_image(prompt="edit", source_images=[missing],
                              output_path=self.out, runner=runner)
        self.assertEqual(runner.calls, [])

    def test_rejects_empty_source_image(self):
        empty = os.path.join(self.tmp, "empty.png")
        open(empty, "wb").close()
        runner = _FakeRunner(self.good_result())
        with self.assertRaises(bridge.SourceImageError):
            bridge.edit_image(prompt="edit", source_images=[empty],
                              output_path=self.out, runner=runner)

    def test_requires_explicit_output_path(self):
        runner = _FakeRunner(self.good_result())
        with self.assertRaises(bridge.OutputPathError):
            bridge.edit_image(prompt="edit", source_images=[self.src_a],
                              output_path="", runner=runner)
        self.assertEqual(runner.calls, [])

    def test_requires_non_empty_prompt(self):
        runner = _FakeRunner(self.good_result())
        with self.assertRaises(bridge.PromptError):
            bridge.edit_image(prompt="   ", source_images=[self.src_a],
                              output_path=self.out, runner=runner)


# ---------------------------------------------------------------------------
# dispatch payload
# ---------------------------------------------------------------------------


class DispatchPayloadTest(BridgeTestBase):
    def test_sends_first_image_as_image_url_and_rest_as_references(self):
        runner = _FakeRunner(self.good_result())
        bridge.edit_image(prompt="portrait edit",
                          source_images=[self.src_a, self.src_b],
                          output_path=self.out, runner=runner)

        self.assertEqual(len(runner.calls), 1)
        payload = runner.calls[0]
        self.assertEqual(payload["prompt"], "portrait edit")
        self.assertEqual(payload["aspect_ratio"], "portrait")
        self.assertEqual(payload["image_url"], os.path.abspath(self.src_a))
        self.assertEqual(payload["reference_image_urls"],
                         [os.path.abspath(self.src_b)])

    def test_payload_never_carries_credentials(self):
        runner = _FakeRunner(self.good_result())
        bridge.edit_image(prompt="edit", source_images=[self.src_a],
                          output_path=self.out, runner=runner)
        blob = json.dumps(runner.calls[0]).lower()
        for banned in ("token", "authorization", "api_key", "apikey",
                       "access_token", "bearer", "auth"):
            self.assertNotIn(banned, blob)


# ---------------------------------------------------------------------------
# provider / model enforcement
# ---------------------------------------------------------------------------


class ProviderEnforcementTest(BridgeTestBase):
    def test_rejects_wrong_provider(self):
        bad = self.good_result()
        bad["provider"] = "fal"
        with self.assertRaises(bridge.ProviderMismatch):
            bridge.edit_image(prompt="edit", source_images=[self.src_a],
                              output_path=self.out, runner=_FakeRunner(bad))
        self.assertFalse(os.path.exists(self.out))

    def test_rejects_wrong_model(self):
        bad = self.good_result()
        bad["model"] = "gpt-image-2-low"
        with self.assertRaises(bridge.ProviderMismatch):
            bridge.edit_image(prompt="edit", source_images=[self.src_a],
                              output_path=self.out, runner=_FakeRunner(bad))

    def test_rejects_unsuccessful_result(self):
        bad = {"success": False, "error": "auth_required",
               "provider": "openai-codex", "model": "gpt-image-2-medium"}
        with self.assertRaises(bridge.DispatchError):
            bridge.edit_image(prompt="edit", source_images=[self.src_a],
                              output_path=self.out, runner=_FakeRunner(bad))

    def test_rejects_non_image_modality(self):
        bad = self.good_result()
        bad["modality"] = "text"
        with self.assertRaises(bridge.DispatchError):
            bridge.edit_image(prompt="edit", source_images=[self.src_a],
                              output_path=self.out, runner=_FakeRunner(bad))

    def test_rejects_missing_produced_image(self):
        bad = self.good_result()
        bad["image"] = os.path.join(self.tmp, "gone.png")
        with self.assertRaises(bridge.DispatchError):
            bridge.edit_image(prompt="edit", source_images=[self.src_a],
                              output_path=self.out, runner=_FakeRunner(bad))


# ---------------------------------------------------------------------------
# manifest + output copy
# ---------------------------------------------------------------------------


class ManifestTest(BridgeTestBase):
    def test_copies_png_and_returns_manifest(self):
        runner = _FakeRunner(self.good_result())
        manifest = bridge.edit_image(
            prompt="portrait edit", source_images=[self.src_a, self.src_b],
            output_path=self.out, runner=runner)

        self.assertTrue(os.path.exists(self.out), "output png must be copied")
        self.assertEqual(_sha256_file(self.out), _sha256_file(self.produced))

        self.assertEqual(manifest["model_alias"], "gpt-image-gen-2")
        self.assertEqual(manifest["hermes_provider"], "openai-codex")
        self.assertEqual(manifest["hermes_model"], "gpt-image-2-medium")
        self.assertEqual(manifest["provider_model"], "gpt-image-2")
        self.assertEqual(manifest["aspect_ratio"], "portrait")
        self.assertEqual(manifest["output_path"], os.path.abspath(self.out))
        self.assertEqual(manifest["output_sha256"], _sha256_file(self.produced))

        srcs = manifest["source_images"]
        self.assertEqual(len(srcs), 2)
        self.assertEqual(srcs[0]["path"], os.path.abspath(self.src_a))
        self.assertEqual(srcs[0]["sha256"], _sha256_file(self.src_a))
        self.assertEqual(srcs[1]["path"], os.path.abspath(self.src_b))
        self.assertEqual(srcs[1]["sha256"], _sha256_file(self.src_b))

    def test_manifest_is_json_serializable_and_credential_free(self):
        runner = _FakeRunner(self.good_result())
        manifest = bridge.edit_image(prompt="edit",
                                     source_images=[self.src_a],
                                     output_path=self.out, runner=runner)
        blob = json.dumps(manifest).lower()
        for banned in ("access_token", "bearer", "api_key", "authorization"):
            self.assertNotIn(banned, blob)

    def test_creates_missing_output_directories(self):
        deep = os.path.join(self.tmp, "a", "b", "c", "out.png")
        bridge.edit_image(prompt="edit", source_images=[self.src_a],
                          output_path=deep,
                          runner=_FakeRunner(self.good_result()))
        self.assertTrue(os.path.exists(deep))


# ---------------------------------------------------------------------------
# observed portrait output enforcement
# ---------------------------------------------------------------------------


class PortraitEnforcementTest(BridgeTestBase):
    """Portrait must be VERIFIED in the result, not merely requested."""

    def test_accepts_real_provider_941x1672(self):
        """실제 provider 가 돌려준 941x1672 는 9:16 이므로 통과해야 한다.

        941/1672 = 0.56280 vs 9/16 = 0.5625 → 상대오차 0.053%.
        """
        ok = self.good_result()
        ok["pixel_size"] = "941x1672"
        manifest = bridge.edit_image(prompt="edit", source_images=[self.src_a],
                                     output_path=self.out,
                                     runner=_FakeRunner(ok))
        self.assertTrue(os.path.exists(self.out))
        self.assertEqual(manifest["pixel_size"], "941x1672")

    def test_rejects_2x3_1024x1536(self):
        """1024x1536 은 2:3(0.6667) 이라 9:16 영상에 크롭 없이 못 들어간다."""
        bad = self.good_result()
        bad["pixel_size"] = "1024x1536"
        with self.assertRaises(bridge.AspectMismatch):
            bridge.edit_image(prompt="edit", source_images=[self.src_a],
                              output_path=self.out, runner=_FakeRunner(bad))
        self.assertFalse(os.path.exists(self.out))

    def test_rejects_tiny_9x16_image(self):
        """비율만 맞고 해상도가 바닥이면 거부 — 768x1360 합성에 못 쓴다."""
        bad = self.good_result()
        bad["pixel_size"] = "90x160"
        with self.assertRaises(bridge.AspectMismatch):
            bridge.edit_image(prompt="edit", source_images=[self.src_a],
                              output_path=self.out, runner=_FakeRunner(bad))
        self.assertFalse(os.path.exists(self.out))

    def test_rejects_unparseable_pixel_size(self):
        bad = self.good_result()
        bad["pixel_size"] = "who knows"
        with self.assertRaises(bridge.AspectMismatch):
            bridge.edit_image(prompt="edit", source_images=[self.src_a],
                              output_path=self.out, runner=_FakeRunner(bad))
        self.assertFalse(os.path.exists(self.out))

    def test_geometry_rule_is_shared_not_duplicated(self):
        """게이트와 피게이트 대상이 어긋나면 안 된다 — 규칙은 한 곳에서 온다."""
        import video_contracts as vc
        self.assertIs(bridge.assert_first_frame_geometry,
                      vc.assert_first_frame_geometry)

    def test_rejects_non_portrait_aspect_ratio(self):
        bad = self.good_result()
        bad["aspect_ratio"] = "landscape"
        bad["pixel_size"] = "1536x1024"
        with self.assertRaises(bridge.AspectMismatch):
            bridge.edit_image(prompt="edit", source_images=[self.src_a],
                              output_path=self.out, runner=_FakeRunner(bad))
        self.assertFalse(os.path.exists(self.out),
                         "must not write a non-portrait output")

    def test_rejects_missing_aspect_ratio(self):
        bad = self.good_result()
        bad.pop("aspect_ratio")
        with self.assertRaises(bridge.AspectMismatch):
            bridge.edit_image(prompt="edit", source_images=[self.src_a],
                              output_path=self.out, runner=_FakeRunner(bad))
        self.assertFalse(os.path.exists(self.out))

    def test_rejects_non_portrait_pixel_size(self):
        bad = self.good_result()
        bad["pixel_size"] = "1536x1024"
        with self.assertRaises(bridge.AspectMismatch):
            bridge.edit_image(prompt="edit", source_images=[self.src_a],
                              output_path=self.out, runner=_FakeRunner(bad))
        self.assertFalse(os.path.exists(self.out))

    def test_allows_absent_pixel_size(self):
        ok = self.good_result()
        ok.pop("pixel_size")
        manifest = bridge.edit_image(prompt="edit", source_images=[self.src_a],
                                     output_path=self.out,
                                     runner=_FakeRunner(ok))
        self.assertTrue(os.path.exists(self.out))
        self.assertIsNone(manifest["pixel_size"])
        self.assertEqual(manifest["aspect_ratio"], "portrait")

    def test_manifest_records_observed_values(self):
        ok = self.good_result()
        manifest = bridge.edit_image(prompt="edit", source_images=[self.src_a],
                                     output_path=self.out,
                                     runner=_FakeRunner(ok))
        self.assertEqual(manifest["aspect_ratio"], ok["aspect_ratio"])
        self.assertEqual(manifest["pixel_size"], ok["pixel_size"])
        self.assertEqual(manifest["observed_aspect_ratio"], ok["aspect_ratio"])
        self.assertEqual(manifest["observed_pixel_size"], ok["pixel_size"])
        self.assertEqual(manifest["requested_aspect_ratio"],
                         bridge.ASPECT_RATIO)


# ---------------------------------------------------------------------------
# default dispatch seam
# ---------------------------------------------------------------------------


class DefaultRunnerTest(unittest.TestCase):
    def test_default_dispatch_is_the_subprocess_runner(self):
        """runner= is a test seam only; production must not be bypassable."""
        called = {}

        def spy(payload):
            called["payload"] = payload
            raise bridge.DispatchError("stop here")

        real = bridge._subprocess_runner
        bridge._subprocess_runner = spy
        try:
            with self.assertRaises(bridge.DispatchError):
                bridge.edit_image(prompt="edit", source_images=[__file__],
                                  output_path="/tmp/never-written.png")
        finally:
            bridge._subprocess_runner = real
        self.assertIn("payload", called,
                      "edit_image did not dispatch via _subprocess_runner")


# ---------------------------------------------------------------------------
# _subprocess_runner branches (no network, no paid call)
# ---------------------------------------------------------------------------


class SubprocessRunnerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="codexrunner-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        for name in ("HERMES_PYTHON", "HERMES_DISPATCHER_FILE",
                     "HERMES_AGENT_DIR"):
            self.addCleanup(setattr, bridge, name, getattr(bridge, name))

    def _fake_agent_dir(self, body):
        agent = os.path.join(self.tmp, "agent")
        tools = os.path.join(agent, "tools")
        os.makedirs(tools, exist_ok=True)
        open(os.path.join(tools, "__init__.py"), "w").close()
        mod = os.path.join(tools, "image_generation_tool.py")
        with open(mod, "w", encoding="utf-8") as fh:
            fh.write(textwrap.dedent(body))
        bridge.HERMES_PYTHON = sys.executable
        bridge.HERMES_AGENT_DIR = agent
        bridge.HERMES_DISPATCHER_FILE = mod
        return agent

    def test_missing_hermes_python(self):
        bridge.HERMES_PYTHON = os.path.join(self.tmp, "no-such-python")
        with self.assertRaises(bridge.DispatchError) as ctx:
            bridge._subprocess_runner({"prompt": "x"})
        self.assertIn("venv python not found", str(ctx.exception))

    def test_missing_dispatcher_file(self):
        bridge.HERMES_PYTHON = sys.executable
        bridge.HERMES_DISPATCHER_FILE = os.path.join(self.tmp, "no-tool.py")
        with self.assertRaises(bridge.DispatchError) as ctx:
            bridge._subprocess_runner({"prompt": "x"})
        self.assertIn("dispatcher not found", str(ctx.exception))

    def test_no_result_marker(self):
        self._fake_agent_dir("""
            def _handle_image_generate(args, **kw):
                raise RuntimeError("boom-no-marker")
        """)
        with self.assertRaises(bridge.DispatchError) as ctx:
            bridge._subprocess_runner({"prompt": "x"})
        self.assertIn("no result", str(ctx.exception))

    def test_unparseable_blob_after_marker(self):
        self._fake_agent_dir("""
            import sys

            def _handle_image_generate(args, **kw):
                sys.stdout.write("@@RESULT@@{not json at all")
                return {"success": True}
        """)
        with self.assertRaises(bridge.DispatchError) as ctx:
            bridge._subprocess_runner({"prompt": "x"})
        self.assertIn("Unparseable", str(ctx.exception))

    def test_non_object_result(self):
        self._fake_agent_dir("""
            def _handle_image_generate(args, **kw):
                return "[1, 2, 3]"
        """)
        with self.assertRaises(bridge.DispatchError) as ctx:
            bridge._subprocess_runner({"prompt": "x"})
        self.assertIn("not an object", str(ctx.exception))

    def test_parses_dict_result_and_forwards_payload(self):
        self._fake_agent_dir("""
            import json

            def _handle_image_generate(args, **kw):
                return {"success": True, "echo": args}
        """)
        out = bridge._subprocess_runner({"prompt": "hello", "aspect_ratio":
                                         "portrait"})
        self.assertTrue(out["success"])
        self.assertEqual(out["echo"]["prompt"], "hello")
        self.assertEqual(out["echo"]["aspect_ratio"], "portrait")


# ---------------------------------------------------------------------------
# installed-Hermes compatibility (no API call)
# ---------------------------------------------------------------------------


class HermesCompatibilityTest(unittest.TestCase):
    """Fails loudly if a Hermes upgrade changes the contract we depend on."""

    def test_hermes_venv_python_exists(self):
        self.assertTrue(os.path.exists(bridge.HERMES_PYTHON),
                        f"Hermes venv python missing: {bridge.HERMES_PYTHON}")

    def test_dispatcher_function_still_exists(self):
        path = bridge.HERMES_DISPATCHER_FILE
        self.assertTrue(os.path.exists(path), f"missing dispatcher: {path}")
        with open(path, "r", encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        names = {n.name for n in ast.walk(tree)
                 if isinstance(n, ast.FunctionDef)}
        self.assertIn("_handle_image_generate", names)

    def test_dispatcher_accepts_expected_arg_keys(self):
        with open(bridge.HERMES_DISPATCHER_FILE, "r", encoding="utf-8") as fh:
            src = fh.read()
        for key in ("prompt", "aspect_ratio", "image_url",
                    "reference_image_urls"):
            self.assertIn(f'args.get("{key}"', src,
                          f"dispatcher no longer reads args[{key!r}]")

    def test_codex_plugin_constants_unchanged(self):
        path = bridge.HERMES_CODEX_PLUGIN_FILE
        self.assertTrue(os.path.exists(path), f"missing plugin: {path}")
        with open(path, "r", encoding="utf-8") as fh:
            src = fh.read()
        tree = ast.parse(src)

        consts = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(
                    node.value, ast.Constant):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name):
                        consts[tgt.id] = node.value.value

        self.assertEqual(consts.get("API_MODEL"), bridge.PROVIDER_MODEL)
        self.assertEqual(consts.get("DEFAULT_MODEL"), bridge.HERMES_MODEL)

        classes = {n.name for n in ast.walk(tree)
                   if isinstance(n, ast.ClassDef)}
        self.assertIn("OpenAICodexImageGenProvider", classes)

        # NOTE: 플러그인이 요청하는 portrait 픽셀 크기("1024x1536")는 여기서
        # 검사하지 않는다. 실측 결과 provider 는 그 요청을 무시하고 941x1672 를
        # 돌려줬다 — 즉 플러그인 상수는 실제 도착하는 프레임에 대해 아무것도
        # 보증하지 않으므로, 그 상수를 고정하는 테스트는 거짓 안정감만 준다.
        # 실제 형상은 video_contracts.assert_first_frame_geometry 가 판정한다.
        self.assertIn('return "openai-codex"', src,
                      "provider name changed")


if __name__ == "__main__":
    unittest.main()
