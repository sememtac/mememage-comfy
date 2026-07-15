"""Tests for the Mememage ComfyUI nodes.

Structure checks run anywhere; the encode/decode round-trip runs only when torch
is present (ComfyUI's runtime). Run from this directory: `python -m pytest`.
"""
import json
import unittest

import nodes


def _has_torch():
    try:
        import torch  # noqa: F401
        import numpy   # noqa: F401
        return True
    except Exception:
        return False


def _has_crypto():
    try:
        from mememage import crypto
        return crypto.is_encryption_available()
    except Exception:
        return False


class TestNodeContract(unittest.TestCase):
    def test_mappings(self):
        for k in ("MememageEncode", "MememageFields", "MememageDecode"):
            self.assertIn(k, nodes.NODE_CLASS_MAPPINGS)
            self.assertIn(k, nodes.NODE_DISPLAY_NAME_MAPPINGS)

    def test_comfy_interface(self):
        for cls in nodes.NODE_CLASS_MAPPINGS.values():
            self.assertTrue(callable(cls.INPUT_TYPES))
            self.assertIsInstance(cls.RETURN_TYPES, tuple)   # () is valid for output nodes
            self.assertTrue(cls.FUNCTION)
            self.assertTrue(cls.CATEGORY == "Mememage" or cls.CATEGORY.startswith("Mememage/"))
            self.assertTrue(hasattr(cls, cls.FUNCTION))

    def test_image_nodes_take_an_image(self):
        for k in ("MememageEncode", "MememageDecode"):
            self.assertIn("image", nodes.NODE_CLASS_MAPPINGS[k].INPUT_TYPES()["required"])


class TestFieldsJson(unittest.TestCase):
    """Mememage Fields = bring an existing JSON object in as fields."""

    def _build(self, **kw):
        return json.loads(nodes.MememageFields().run(**kw)[0])

    def test_passes_a_json_object_through(self):
        obj = {"creator": "catmemes", "tags": ["lake", "mist"], "take": 42, "ok": True}
        self.assertEqual(self._build(fields_json=json.dumps(obj)), obj)

    def test_types_are_preserved_from_json(self):
        got = self._build(fields_json='{"n": 42, "ok": true, "tags": ["a", "b"]}')
        self.assertEqual(got, {"n": 42, "ok": True, "tags": ["a", "b"]})

    def test_empty_is_empty_object(self):
        self.assertEqual(self._build(), {})
        self.assertEqual(self._build(fields_json="{}"), {})

    def test_invalid_json_raises(self):
        with self.assertRaises(ValueError):
            nodes.MememageFields().run(fields_json="not json")

    def test_non_object_raises(self):
        with self.assertRaises(ValueError):
            nodes.MememageFields().run(fields_json="[1, 2, 3]")

    def test_output_wires_into_encode(self):
        fields_json = nodes.MememageFields().run(fields_json='{"by": "catmemes"}')[0]
        self.assertIsInstance(json.loads(fields_json), dict)


class TestFieldList(unittest.TestCase):
    """The list is an aggregator: each field_* input is a Field node's output."""

    def _build(self, **kw):
        return json.loads(nodes.MememageFieldList().run(**kw)[0])

    def test_declares_a_pool_of_field_inputs(self):
        opt = nodes.MememageFieldList.INPUT_TYPES()["optional"]
        self.assertIn("base", opt)
        self.assertIn("field_1", opt)
        self.assertIn(f"field_{nodes._LIST_SLOTS}", opt)
        self.assertTrue(opt["field_1"][1].get("forceInput"))   # a socket, not a widget

    def test_merges_several_field_inputs(self):
        got = self._build(field_1='{"by": "catmemes"}', field_2='{"take": 42}')
        self.assertEqual(got, {"by": "catmemes", "take": 42})

    def test_later_input_overrides_earlier(self):
        self.assertEqual(self._build(field_1='{"x": 1}', field_2='{"x": 2}'), {"x": 2})

    def test_base_merged_first(self):
        self.assertEqual(self._build(base='{"a": 1}', field_1='{"b": 2}'), {"a": 1, "b": 2})

    def test_empty_and_gaps_are_fine(self):
        self.assertEqual(self._build(), {})
        # a gap (field_2 unset) must not break the merge
        self.assertEqual(self._build(field_1='{"a": 1}', field_3='{"c": 3}'),
                         {"a": 1, "c": 3})

    def test_field_from_a_field_node_flows_through(self):
        one = nodes.MememageField().run(key="by", value="catmemes")[0]
        self.assertEqual(self._build(field_1=one), {"by": "catmemes"})

    def test_bad_json_raises(self):
        with self.assertRaises(ValueError):
            nodes.MememageFieldList().run(field_1="not json")


class TestSaveRecord(unittest.TestCase):
    def test_writes_record_core_fields_first(self):
        import tempfile, os
        from unittest.mock import patch
        # deliberately messy order in; core fields should come out first
        record = '{"generation": {"seed": 1}, "Creator": "x", "hash_version": "open", ' \
                 '"identifier": "mememage-abcdef0123456789", "content_hash": "0011223344556677", ' \
                 '"comfy_prompt": {"1": {}}}'
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(nodes, "_comfy_output_dir", return_value=tmp):
                nodes.MememageSaveRecord().run(record)
            path = os.path.join(tmp, "mememage-abcdef0123456789.json")
            self.assertTrue(os.path.exists(path))
            with open(path) as f:
                text = f.read()
                data = json.loads(text)
            keys = list(data.keys())
            self.assertEqual(keys[:3], ["identifier", "content_hash", "hash_version"])
            self.assertEqual(keys[-1], "comfy_prompt")           # bulky blob last
            self.assertEqual(data, json.loads(record))           # same data, just reordered
            self.assertIn("\n", text)                            # pretty-printed for inspection

    def test_subfolder(self):
        import tempfile, os
        from unittest.mock import patch
        record = '{"identifier": "mememage-abcdef0123456789"}'
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(nodes, "_comfy_output_dir", return_value=tmp):
                nodes.MememageSaveRecord().run(record, subfolder="records")
            self.assertTrue(os.path.exists(os.path.join(tmp, "records", "mememage-abcdef0123456789.json")))

    def test_is_an_output_node(self):
        self.assertTrue(getattr(nodes.MememageSaveRecord, "OUTPUT_NODE", False))

    def test_junk_record_is_skipped_not_crashed(self):
        # placeholders / non-JSON (e.g. from a switch) must not crash or write garbage
        import tempfile, os, glob
        from unittest.mock import patch
        for junk in ("RECORD NOT FOUND", "", '{"content_hash": "no id"}'):
            with tempfile.TemporaryDirectory() as tmp:
                with patch.object(nodes, "_comfy_output_dir", return_value=tmp):
                    out = nodes.MememageSaveRecord().run(junk)   # must not raise
                self.assertEqual(glob.glob(os.path.join(tmp, "*.json")), [])   # nothing written
                self.assertIn("nothing saved", out["ui"]["text"][0])

    @unittest.skipUnless(_has_torch(), "torch required (ComfyUI runtime)")
    def test_image_saved_as_matched_pair(self):
        # wiring the image writes <identifier>.png alongside the .json, same name
        import tempfile, os, torch
        from unittest.mock import patch
        record = '{"identifier": "mememage-abcdef0123456789", "content_hash": "0011223344556677"}'
        img = torch.full((1, 8, 8, 3), 0.5)
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(nodes, "_comfy_output_dir", return_value=tmp):
                out = nodes.MememageSaveRecord().run(record, image=img)
            self.assertTrue(os.path.exists(os.path.join(tmp, "mememage-abcdef0123456789.json")))
            self.assertTrue(os.path.exists(os.path.join(tmp, "mememage-abcdef0123456789.png")))
            self.assertIn("+", out["ui"]["text"][0])                # "saved …json + …png"

    @unittest.skipUnless(_has_torch(), "torch required (ComfyUI runtime)")
    def test_pair_overwrites_not_accumulates(self):
        # re-running the same pinned identifier overwrites one pair — never numbers new files
        import tempfile, os, glob, torch
        from unittest.mock import patch
        record = '{"identifier": "mememage-abcdef0123456789"}'
        img = torch.full((1, 8, 8, 3), 0.5)
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(nodes, "_comfy_output_dir", return_value=tmp):
                nodes.MememageSaveRecord().run(record, image=img)
                nodes.MememageSaveRecord().run(record, image=img)
            self.assertEqual(len(glob.glob(os.path.join(tmp, "*.png"))), 1)
            self.assertEqual(len(glob.glob(os.path.join(tmp, "*.json"))), 1)

    @unittest.skipUnless(_has_torch(), "torch required (ComfyUI runtime)")
    def test_custom_names_are_free_and_independent(self):
        # the bar links image<->record; filenames need not match, or match the identifier
        import tempfile, os, torch
        from unittest.mock import patch
        record = '{"identifier": "mememage-abcdef0123456789"}'
        img = torch.full((1, 8, 8, 3), 0.5)
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(nodes, "_comfy_output_dir", return_value=tmp):
                nodes.MememageSaveRecord().run(
                    record, image=img, record_name="my_soul", image_name="dawn_lake")
            self.assertTrue(os.path.exists(os.path.join(tmp, "my_soul.json")))     # stem + .json added
            self.assertTrue(os.path.exists(os.path.join(tmp, "dawn_lake.png")))    # differs from record
            # the identifier-named defaults were NOT written
            self.assertFalse(os.path.exists(os.path.join(tmp, "mememage-abcdef0123456789.json")))
            self.assertFalse(os.path.exists(os.path.join(tmp, "mememage-abcdef0123456789.png")))

    def test_record_name_respects_explicit_extension(self):
        # a name already carrying an extension is used verbatim (no double .json)
        import tempfile, os
        from unittest.mock import patch
        record = '{"identifier": "mememage-abcdef0123456789"}'
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(nodes, "_comfy_output_dir", return_value=tmp):
                nodes.MememageSaveRecord().run(record, record_name="notes.soul")
            self.assertTrue(os.path.exists(os.path.join(tmp, "notes.soul")))
            self.assertFalse(os.path.exists(os.path.join(tmp, "notes.soul.json")))


class TestWorkflowFields(unittest.TestCase):
    """Promote generation params straight out of a ComfyUI prompt graph."""

    SD_PROMPT = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "v1-5-pruned-emaonly.safetensors"}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": "a lake at dawn"}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": "blurry, watermark"}},
        "4": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 512, "batch_size": 1}},
        "5": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "positive": ["2", 0],
              "negative": ["3", 0], "latent_image": ["4", 0], "seed": 42, "steps": 20, "cfg": 7.5,
              "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0}},
    }

    def _gen(self, **kw):
        return json.loads(nodes.MememageWorkflowFields().run(**kw)[0]).get("generation", {})

    def test_extracts_standard_params(self):
        g = self._gen(prompt=self.SD_PROMPT)
        self.assertEqual(g["model"], "v1-5-pruned-emaonly.safetensors")
        self.assertEqual(g["seed"], 42)
        self.assertEqual(g["steps"], 20)
        self.assertEqual(g["cfg"], 7.5)
        self.assertEqual(g["sampler"], "euler")
        self.assertEqual(g["scheduler"], "normal")
        self.assertEqual(g["prompt"], "a lake at dawn")            # traced +positive
        self.assertEqual(g["negative_prompt"], "blurry, watermark")  # traced -negative

    def test_no_prompt_is_empty(self):
        self.assertEqual(json.loads(nodes.MememageWorkflowFields().run()[0]), {})

    def test_base_is_kept_alongside_generation(self):
        out = json.loads(nodes.MememageWorkflowFields().run(base='{"by": "catmemes"}', prompt=self.SD_PROMPT)[0])
        self.assertEqual(out["by"], "catmemes")
        self.assertIn("generation", out)

    def test_unresolvable_wired_param_is_absent_not_a_link(self):
        import copy
        p = copy.deepcopy(self.SD_PROMPT)
        p["5"]["inputs"]["seed"] = ["99", 0]     # wired from a node that doesn't exist
        g = self._gen(prompt=p)
        self.assertNotIn("seed", g)              # dangling wire -> absent, never a raw [node,slot]
        self.assertEqual(g["steps"], 20)         # the literal ones still land

    def test_wired_seed_from_primitive_is_resolved(self):
        import copy
        p = copy.deepcopy(self.SD_PROMPT)
        p["5"]["inputs"]["seed"] = ["10", 0]     # seed wired from a primitive (very common)
        p["10"] = {"class_type": "PrimitiveNode", "inputs": {"value": 777}}
        g = self._gen(prompt=p)
        self.assertEqual(g["seed"], 777)         # followed the wire to its literal

    def test_toggle_off_excludes_exactly_that_param(self):
        g = self._gen(prompt=self.SD_PROMPT, seed=False, negative_prompt=False)
        self.assertNotIn("seed", g)             # turned off -> guaranteed absent
        self.assertNotIn("negative_prompt", g)
        self.assertEqual(g["steps"], 20)        # untouched toggles still land
        self.assertEqual(g["prompt"], "a lake at dawn")

    def test_only_selected_params_emitted(self):
        g = self._gen(prompt=self.SD_PROMPT, model=False, positive_prompt=True,
                      negative_prompt=False, seed=False, steps=False, cfg=False,
                      sampler=False, scheduler=False, denoise=False, loras=False)
        self.assertEqual(set(g.keys()), {"prompt"})   # exactly the one left on

    def test_all_off_yields_no_generation(self):
        out = json.loads(nodes.MememageWorkflowFields().run(
            prompt=self.SD_PROMPT, model=False, positive_prompt=False, negative_prompt=False,
            seed=False, steps=False, cfg=False, sampler=False, scheduler=False,
            denoise=False, loras=False)[0])
        self.assertNotIn("generation", out)

    def test_never_cached(self):
        import math
        # reads the untracked hidden prompt -> must always re-run (NaN sentinel)
        self.assertTrue(math.isnan(nodes.MememageWorkflowFields.IS_CHANGED()))
        self.assertTrue(math.isnan(nodes.MememageEncode.IS_CHANGED()))

    def test_toggles_default_on_extracts_everything(self):
        # defaults preserved: with no toggles passed, behaves as before
        g = self._gen(prompt=self.SD_PROMPT)
        for k in ("model", "seed", "steps", "cfg", "sampler", "scheduler", "denoise",
                  "prompt", "negative_prompt"):
            self.assertIn(k, g)

    def test_flux_unet_loader_model(self):
        p = {"1": {"class_type": "UNETLoader", "inputs": {"unet_name": "flux1-dev-Q4_K_S.gguf"}}}
        self.assertEqual(self._gen(prompt=p).get("model"), "flux1-dev-Q4_K_S.gguf")

    # A real Flux custom-sampling graph: the params live spread across
    # RandomNoise / BasicScheduler / KSamplerSelect / FluxGuidance / BasicGuider —
    # NOT on one KSampler. The EXIF-encoder maps them all by input name + topology.
    FLUX_PROMPT = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "flux1-dev.safetensors"}},
        "2": {"class_type": "DualCLIPLoader", "inputs": {"clip_name1": "t5xxl.safetensors",
                                                         "clip_name2": "clip_l.safetensors"}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": "a fox in snow"}},
        "4": {"class_type": "FluxGuidance", "inputs": {"conditioning": ["3", 0], "guidance": 3.5}},
        "5": {"class_type": "BasicGuider", "inputs": {"model": ["1", 0], "conditioning": ["4", 0]}},
        "6": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
        "7": {"class_type": "BasicScheduler", "inputs": {"model": ["1", 0], "scheduler": "simple",
                                                         "steps": 25, "denoise": 1.0}},
        "8": {"class_type": "RandomNoise", "inputs": {"noise_seed": 123456}},
        "9": {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["8", 0], "guider": ["5", 0],
                                                                "sampler": ["6", 0], "sigmas": ["7", 0],
                                                                "latent_image": ["0", 0]}},
    }

    def test_flux_custom_sampling_stack(self):
        g = self._gen(prompt=self.FLUX_PROMPT)
        self.assertEqual(g["model"], "flux1-dev.safetensors")   # UNET loader
        self.assertEqual(g["seed"], 123456)                     # RandomNoise.noise_seed
        self.assertEqual(g["steps"], 25)                        # BasicScheduler
        self.assertEqual(g["scheduler"], "simple")              # BasicScheduler
        self.assertEqual(g["denoise"], 1.0)                     # BasicScheduler
        self.assertEqual(g["sampler"], "euler")                 # KSamplerSelect
        self.assertEqual(g["guidance"], 3.5)                    # FluxGuidance — its own tag
        self.assertNotIn("cfg", g)                              # Flux has no cfg
        self.assertEqual(g["prompt"], "a fox in snow")          # traced through the guider chain
        self.assertNotIn("negative_prompt", g)                  # Flux: no negative -> absent


class TestFieldChain(unittest.TestCase):
    def test_single_field(self):
        out = json.loads(nodes.MememageField().run(key="by", value="catmemes")[0])
        self.assertEqual(out, {"by": "catmemes"})

    def test_value_is_smart_typed(self):
        self.assertEqual(json.loads(nodes.MememageField().run(key="take", value="42")[0]),
                         {"take": 42})

    def test_chaining_grows_the_list(self):
        # Field -> Field -> Field, each merging the last: the "growing list"
        a = nodes.MememageField().run(key="by", value="catmemes")[0]
        b = nodes.MememageField().run(key="series", value="ComfyUI test", base=a)[0]
        c = nodes.MememageField().run(key="take", value="42", base=b)[0]
        self.assertEqual(json.loads(c), {"by": "catmemes", "series": "ComfyUI test", "take": 42})

    def test_blank_key_passes_base_through(self):
        a = nodes.MememageField().run(key="x", value="1")[0]
        b = nodes.MememageField().run(base=a)[0]
        self.assertEqual(json.loads(b), {"x": 1})

    def test_repeated_key_overrides_upstream(self):
        a = nodes.MememageField().run(key="x", value="1")[0]
        b = nodes.MememageField().run(key="x", value="2", base=a)[0]
        self.assertEqual(json.loads(b), {"x": 2})


@unittest.skipUnless(_has_torch(), "torch required (ComfyUI runtime)")
class TestRoundTrip(unittest.TestCase):
    def test_encode_then_decode(self):
        import torch
        img = torch.full((1, 512, 768, 3), 0.5)            # one gray IMAGE
        barred, identifier, record = nodes.MememageEncode().run(
            img, fields_json='{"by": "catmemes"}', embed_workflow=False)
        self.assertEqual(barred.shape, img.shape)
        self.assertTrue(identifier.startswith("mememage-"))
        self.assertEqual(json.loads(record)["by"], "catmemes")

        # Decode is a pure reader: identifier + content_hash, and passes the image through
        ident, chash, img_out = nodes.MememageDecode().run(barred)
        self.assertEqual(ident, identifier)
        self.assertTrue(chash)
        self.assertEqual(img_out.shape, barred.shape)      # image chains onward
        # verification lives in Verify
        _, matched, _, _ = nodes.MememageVerify().run(image=barred, record=record)
        self.assertTrue(matched)

    def test_record_core_fields_first_and_still_verifies(self):
        import torch
        img = torch.full((1, 512, 512, 3), 0.5)
        barred, _, record = nodes.MememageEncode().run(
            img, fields_json='{"by": "catmemes"}', embed_workflow=False)
        keys = list(json.loads(record).keys())
        self.assertEqual(keys[:3], ["identifier", "content_hash", "hash_version"])
        # reordered record still verifies — the hash is order-independent
        _, matched, _, _ = nodes.MememageVerify().run(image=barred, record=record)
        self.assertTrue(matched)

    def test_survives_stale_scrambled_widgets(self):
        # after a plugin update, mismatched widget slots can feed fields_json a bool
        # and blank the prefix; the node should self-heal (data comes via the socket).
        import torch, json as _json
        img = torch.full((1, 512, 512, 3), 0.5)
        barred, identifier, record = nodes.MememageEncode().run(
            img, fields=_json.dumps({"Creator": "Catmemes"}),
            fields_json=True, prefix="", embed_workflow=False)     # scrambled values
        self.assertTrue(identifier.startswith("mememage-"))         # prefix defaulted
        self.assertEqual(_json.loads(record)["Creator"], "Catmemes")  # real data intact

    def test_wired_fields_input_overrides_typed(self):
        import torch
        img = torch.full((1, 512, 512, 3), 0.5)
        _, _, record = nodes.MememageEncode().run(
            img, fields_json='{"by": "typed", "keep": 1}',
            fields='{"by": "wired"}', embed_workflow=False)
        r = json.loads(record)
        self.assertEqual(r["by"], "wired")     # wired input wins
        self.assertEqual(r["keep"], 1)          # typed default still present


class TestNativePicker(unittest.TestCase):
    """The picker returns a PATH (never file contents); mocked so no dialog opens."""

    def test_darwin_returns_chosen_path(self):
        from unittest.mock import patch, MagicMock
        result = MagicMock(returncode=0, stdout="/Users/me/pw.txt\n")
        with patch.object(nodes.sys, "platform", "darwin"), \
             patch.object(nodes.subprocess, "run", return_value=result) as run:
            self.assertEqual(nodes._native_pick_file(), "/Users/me/pw.txt")
        self.assertEqual(run.call_args[0][0][0], "osascript")

    def test_cancel_returns_empty(self):
        from unittest.mock import patch, MagicMock
        result = MagicMock(returncode=1, stdout="")   # user cancelled
        with patch.object(nodes.sys, "platform", "darwin"), \
             patch.object(nodes.subprocess, "run", return_value=result):
            self.assertEqual(nodes._native_pick_file(), "")

    def test_directory_uses_folder_chooser(self):
        from unittest.mock import patch, MagicMock
        result = MagicMock(returncode=0, stdout="/Users/me/records\n")
        with patch.object(nodes.sys, "platform", "darwin"), \
             patch.object(nodes.subprocess, "run", return_value=result) as run:
            self.assertEqual(nodes._native_pick_file(directory=True), "/Users/me/records")
        self.assertIn("choose folder", run.call_args[0][0][-1])   # osascript verb, not "choose file"


class TestLoadRecord(unittest.TestCase):
    def test_reads_by_path_and_outputs_identifier(self):
        import tempfile, os
        rec = '{"identifier": "mememage-abcdef0123456789", "Creator": "Catmemes"}'
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "r.json")
            with open(p, "w") as f:
                f.write(rec)
            record, ident = nodes.MememageLoadRecord().run(path=p)
            self.assertEqual(json.loads(record)["Creator"], "Catmemes")
            self.assertEqual(ident, "mememage-abcdef0123456789")   # chains into Encode.identifier

    def test_missing_file_is_empty_not_crash(self):
        # not where we looked -> ("", "") (it may live elsewhere), not an error
        self.assertEqual(nodes.MememageLoadRecord().run(path="/nope/does-not-exist.json"), ("", ""))

    def test_nothing_given_is_empty(self):
        # no path yet -> graceful empty
        self.assertEqual(nodes.MememageLoadRecord().run(), ("", ""))

    def test_present_but_corrupt_still_raises(self):
        import tempfile, os
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "r.json")
            with open(p, "w") as f:
                f.write("not json at all")
            with self.assertRaises(ValueError):        # a real, present-but-broken file IS surfaced
                nodes.MememageLoadRecord().run(path=p)


class TestFindRecord(unittest.TestCase):
    def test_finds_by_identifier_in_output_dir(self):
        import tempfile, os
        from unittest.mock import patch
        rec = '{"identifier": "mememage-abcdef0123456789"}'
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "mememage-abcdef0123456789.json"), "w") as f:
                f.write(rec)
            with patch.object(nodes, "_comfy_output_dir", return_value=tmp):
                record, ident, found = nodes.MememageFindRecord().run(identifier="mememage-abcdef0123456789")
        self.assertEqual(record, rec)
        self.assertEqual(ident, "mememage-abcdef0123456789")
        self.assertTrue(found)

    def test_nothing_given_is_empty_not_found(self):
        self.assertEqual(nodes.MememageFindRecord().run(), ("", "", False))

    def test_finds_custom_named_record_by_content(self):
        # the identifier lives INSIDE the record, so a custom filename is found by scanning
        import tempfile, os
        from unittest.mock import patch
        rec = '{"identifier": "mememage-abcdef0123456789", "Creator": "Cat"}'
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "dawn_lake.json"), "w") as f:   # NOT named by identifier
                f.write(rec)
            with patch.object(nodes, "_comfy_output_dir", return_value=tmp):
                record, ident, found = nodes.MememageFindRecord().run(identifier="mememage-abcdef0123456789")
        self.assertEqual(json.loads(record)["Creator"], "Cat")
        self.assertTrue(found)

    def test_scan_ignores_non_matching_and_corrupt(self):
        import tempfile, os
        from unittest.mock import patch
        want = '{"identifier": "mememage-1111111111111111", "k": "want"}'
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "a.json"), "w") as f:
                f.write('{"identifier": "mememage-2222222222222222"}')   # different id
            with open(os.path.join(tmp, "b.json"), "w") as f:
                f.write("not json")                                      # corrupt -> skipped
            with open(os.path.join(tmp, "c.json"), "w") as f:
                f.write(want)
            with patch.object(nodes, "_comfy_output_dir", return_value=tmp):
                record, ident, found = nodes.MememageFindRecord().run(identifier="mememage-1111111111111111")
        self.assertEqual(json.loads(record)["k"], "want")
        self.assertTrue(found)

    def test_custom_folder_search_root(self):
        import tempfile, os
        rec = '{"identifier": "mememage-abcdef0123456789"}'
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "whatever.json"), "w") as f:
                f.write(rec)
            record, ident, found = nodes.MememageFindRecord().run(
                identifier="mememage-abcdef0123456789", folder=tmp)     # scan an explicit dir
        self.assertEqual(record, rec)
        self.assertTrue(found)

    def test_no_matching_identifier_in_folder_is_empty(self):
        import tempfile, os
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "x.json"), "w") as f:
                f.write('{"identifier": "mememage-9999999999999999"}')
            with patch.object(nodes, "_comfy_output_dir", return_value=tmp):
                out = nodes.MememageFindRecord().run(identifier="mememage-0000000000000000")
        self.assertEqual(out, ("", "mememage-0000000000000000", False))


class TestVerify(unittest.TestCase):
    def _make(self):
        import torch, mememage
        img = torch.full((1, 512, 512, 3), 0.5)
        barred, ident, record = nodes.MememageEncode().run(
            img, fields_json='{"by": "catmemes"}', embed_workflow=False)
        return barred, ident, record

    def test_verified(self):
        barred, ident, record = self._make()
        verdict, matched, identifier, img = nodes.MememageVerify().run(image=barred, record=record)
        self.assertTrue(matched)
        self.assertTrue(verdict.startswith("VERIFIED"))
        self.assertEqual(identifier, ident)
        self.assertEqual(img.shape, barred.shape)          # image passes through

    def test_altered_record(self):
        import json as _json
        barred, ident, record = self._make()
        tampered = _json.loads(record); tampered["by"] = "someone-else"
        verdict, matched, _, _ = nodes.MememageVerify().run(image=barred, record=_json.dumps(tampered))
        self.assertFalse(matched)
        self.assertTrue(verdict.startswith("ALTERED"))

    def test_no_bar(self):
        import torch
        plain = torch.full((1, 256, 256, 3), 0.5)         # never barred
        verdict, matched, identifier, _ = nodes.MememageVerify().run(image=plain, record='{"x": 1}')
        self.assertFalse(matched)
        self.assertTrue(verdict.startswith("NO BAR"))
        self.assertEqual(identifier, "")

    def test_no_record(self):
        barred, ident, _ = self._make()
        verdict, matched, identifier, _ = nodes.MememageVerify().run(image=barred)  # no record
        self.assertFalse(matched)
        self.assertTrue(verdict.startswith("NO RECORD"))
        self.assertEqual(identifier, ident)                # bar still read

    def test_junk_record_is_no_record_not_crash(self):
        barred, ident, _ = self._make()
        # a placeholder string from a switch must not crash json.loads
        verdict, matched, identifier, _ = nodes.MememageVerify().run(image=barred, record="RECORD NOT FOUND")
        self.assertFalse(matched)
        self.assertTrue(verdict.startswith("NO RECORD"))
        self.assertEqual(identifier, ident)

    def test_needs_an_image(self):
        with self.assertRaises(ValueError):
            nodes.MememageVerify().run(record='{"x": 1}')


class TestExtractWorkflow(unittest.TestCase):
    def test_extracts_embedded_graph(self):
        graph = {"5": {"class_type": "KSampler", "inputs": {"seed": 7}}}
        rec = json.dumps({"identifier": "mememage-abcdef0123456789", "comfy_prompt": graph})
        out = nodes.MememageExtractWorkflow().run(rec)
        wf, has = out["result"]
        self.assertTrue(has)
        self.assertEqual(json.loads(wf), graph)
        self.assertEqual(out["ui"]["mememage_workflow"], [wf])   # surfaced to the button

    def test_junk_record_is_no_workflow_not_crash(self):
        for junk in ("RECORD NOT FOUND", "", "[not a dict]"):
            out = nodes.MememageExtractWorkflow().run(junk)   # must not raise
            wf, has = out["result"]
            self.assertFalse(has)
            self.assertEqual(wf, "")

    def test_no_workflow_present(self):
        rec = '{"identifier": "mememage-abcdef0123456789", "Creator": "x"}'
        out = nodes.MememageExtractWorkflow().run(rec)
        wf, has = out["result"]
        self.assertFalse(has)
        self.assertEqual(wf, "")

    def test_is_an_output_node(self):
        self.assertTrue(getattr(nodes.MememageExtractWorkflow, "OUTPUT_NODE", False))


@unittest.skipUnless(_has_crypto(), "cryptography required")
class TestUnlock(unittest.TestCase):
    def _encrypted_record(self):
        import mememage
        from PIL import Image
        img = Image.new("RGB", (512, 512), (100, 90, 80))
        rec = mememage.encode(img, {"pub": "y", "secret": "hidden"},
                              password="pw", private=["secret"])
        return rec.to_json()

    def test_junk_record_passes_through_not_crash(self):
        for junk in ("RECORD NOT FOUND", "", "[1,2,3]"):
            out, unlocked = nodes.MememageUnlock().run(junk)   # must not raise
            self.assertFalse(unlocked)
            self.assertEqual(out, junk)

    def test_unlock_reveals_private(self):
        import os
        from unittest.mock import patch
        rec = self._encrypted_record()
        with patch.dict(os.environ, {"MEMEMAGE_PASSWORD": "pw"}, clear=False):
            out, unlocked = nodes.MememageUnlock().run(rec)
        self.assertTrue(unlocked)
        self.assertEqual(json.loads(out)["secret"], "hidden")

    def test_public_record_passes_through(self):
        rec = '{"identifier": "mememage-abcdef0123456789", "pub": "y"}'
        out, unlocked = nodes.MememageUnlock().run(rec)
        self.assertTrue(unlocked)
        self.assertEqual(out, rec)

    def test_locked_without_password_returns_false(self):
        import os
        from unittest.mock import patch
        rec = self._encrypted_record()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MEMEMAGE_PASSWORD", None)
            out, unlocked = nodes.MememageUnlock().run(rec)
        self.assertFalse(unlocked)
        self.assertNotIn("secret", json.loads(out))

    def test_wrong_password_reports_not_unlocked(self):
        import os
        from unittest.mock import patch
        rec = self._encrypted_record()
        with patch.dict(os.environ, {"MEMEMAGE_PASSWORD": "wrong"}, clear=False):
            out, unlocked = nodes.MememageUnlock().run(rec)   # graceful, no crash
        self.assertFalse(unlocked)
        self.assertNotIn("secret", json.loads(out))            # stays sealed


class TestReserveId(unittest.TestCase):
    def test_valid_identifier_passes(self):
        self.assertEqual(nodes.MememageReserveId().run("mememage-1a2b3c4d5e6f7a8b"),
                         ("mememage-1a2b3c4d5e6f7a8b",))

    def test_empty_passes_through(self):
        self.assertEqual(nodes.MememageReserveId().run(""), ("",))   # -> Encode content-addresses

    def test_bad_identifier_raises(self):
        for bad in ("not-an-id", "mememage-XYZ", "mememage-1a2b", "nope"):
            with self.assertRaises(ValueError):
                nodes.MememageReserveId().run(bad)

    def test_never_cached(self):
        import math
        self.assertTrue(math.isnan(nodes.MememageReserveId.IS_CHANGED()))


@unittest.skipUnless(_has_torch(), "torch required (ComfyUI runtime)")
class TestPinnedIdentifier(unittest.TestCase):
    def test_pin_holds_while_content_changes(self):
        import torch
        pin = "mememage-1a2b3c4d5e6f7a8b"
        # two different images / fields, same pinned identifier = same pointer
        _, id1, rec1 = nodes.MememageEncode().run(
            torch.full((1, 512, 512, 3), 0.4), fields_json='{"v": 1}',
            identifier=pin, embed_workflow=False)
        _, id2, rec2 = nodes.MememageEncode().run(
            torch.full((1, 512, 512, 3), 0.6), fields_json='{"v": 2}',
            identifier=pin, embed_workflow=False)
        self.assertEqual(id1, pin)
        self.assertEqual(id2, pin)                                   # identity stayed put
        h1 = json.loads(rec1)["content_hash"]; h2 = json.loads(rec2)["content_hash"]
        self.assertNotEqual(h1, h2)                                  # content hash tracked the change

    def test_empty_identifier_is_content_addressed(self):
        import torch
        _, ident, _ = nodes.MememageEncode().run(
            torch.full((1, 512, 512, 3), 0.5), fields_json='{"v": 1}', embed_workflow=False)
        self.assertTrue(ident.startswith("mememage-"))              # auto-derived, not pinned

    def test_toggle_off_ignores_pinned_identifier(self):
        import torch
        pin = "mememage-1a2b3c4d5e6f7a8b"
        # A pin is supplied (as if a Reserve ID wire is connected) but the toggle is
        # OFF — it must be ignored and the record content-addressed instead, so a user
        # can leave the wire connected without adhering to it.
        _, ident, _ = nodes.MememageEncode().run(
            torch.full((1, 512, 512, 3), 0.5), fields_json='{"v": 1}',
            identifier=pin, use_identifier=False, embed_workflow=False)
        self.assertNotEqual(ident, pin)                            # pin ignored
        self.assertTrue(ident.startswith("mememage-"))             # content-addressed

    def test_toggle_on_honors_pinned_identifier(self):
        import torch
        pin = "mememage-1a2b3c4d5e6f7a8b"
        _, ident, _ = nodes.MememageEncode().run(
            torch.full((1, 512, 512, 3), 0.5), fields_json='{"v": 1}',
            identifier=pin, use_identifier=True, embed_workflow=False)
        self.assertEqual(ident, pin)                               # pin honored (explicit ON)


@unittest.skipUnless(_has_torch(), "torch required (ComfyUI runtime)")
class TestEncodePromptScrub(unittest.TestCase):
    def test_password_scrubbed_from_comfy_prompt(self):
        import torch
        img = torch.full((1, 512, 512, 3), 0.5)
        # some other node in the graph carries a "password" input — never record it
        graph = {"7": {"class_type": "SomeNode", "inputs": {"password": "leak", "seed": 5}}}
        _, _, rec = nodes.MememageEncode().run(img, embed_workflow=True, prompt=graph)
        cp = json.loads(rec)["comfy_prompt"]
        self.assertNotIn("password", cp["7"]["inputs"])   # scrubbed out of the record
        self.assertEqual(cp["7"]["inputs"]["seed"], 5)     # everything else intact

    def test_is_changed_nan_scrubbed_from_comfy_prompt(self):
        import torch, json as _json
        img = torch.full((1, 512, 512, 3), 0.5)
        # ComfyUI injects node["is_changed"] (float NaN for always-run nodes); it must
        # never reach the record — NaN would break the content hash.
        graph = {"5": {"class_type": "KSampler", "inputs": {"seed": 1},
                       "is_changed": [float("nan")]}}
        _, _, rec = nodes.MememageEncode().run(img, embed_workflow=True, prompt=graph)  # must not raise
        cp = _json.loads(rec)["comfy_prompt"]
        self.assertNotIn("is_changed", cp["5"])
        self.assertEqual(cp["5"]["inputs"]["seed"], 1)

    def test_button_widget_phantoms_scrubbed_from_comfy_prompt(self):
        import torch, json as _json
        img = torch.full((1, 512, 512, 3), 0.5)
        # JS buttons leak into the prompt as fake inputs with label keys — must not be recorded
        graph = {"34": {"class_type": "MememageEncode",
                        "inputs": {"prefix": "mememage", "📁 pick password file": None, "seed": 5}},
                 "22": {"class_type": "MememageFieldList",
                        "inputs": {"field_1": ["12", 0], "+ add field": None}}}
        _, _, rec = nodes.MememageEncode().run(img, embed_workflow=True, prompt=graph)
        cp = _json.loads(rec)["comfy_prompt"]
        self.assertNotIn("📁 pick password file", cp["34"]["inputs"])
        self.assertNotIn("+ add field", cp["22"]["inputs"])
        self.assertEqual(cp["34"]["inputs"]["prefix"], "mememage")   # real inputs kept
        self.assertEqual(cp["22"]["inputs"]["field_1"], ["12", 0])

    def test_no_plaintext_password_input(self):
        # the password must not be a graph widget at all — file/env only
        self.assertNotIn("password", nodes.MememageEncode.INPUT_TYPES()["optional"])
        self.assertIn("password_file", nodes.MememageEncode.INPUT_TYPES()["optional"])


@unittest.skipUnless(_has_torch() and _has_crypto(), "torch + cryptography required")
class TestEncryption(unittest.TestCase):
    """Password is out-of-band only: a file or the MEMEMAGE_PASSWORD env var."""

    def _rec(self, env_password=None, **kw):
        import torch, os
        from unittest.mock import patch
        img = torch.full((1, 512, 512, 3), 0.5)
        environ = {"MEMEMAGE_PASSWORD": env_password} if env_password is not None else {}
        with patch.dict(os.environ, environ, clear=False):
            if env_password is None:
                os.environ.pop("MEMEMAGE_PASSWORD", None)    # clean slate for public/no-pw tests
            _, _, rec = nodes.MememageEncode().run(img, embed_workflow=False, **kw)
        return json.loads(rec)

    def test_no_password_is_public(self):
        r = self._rec(fields_json='{"secret": "x"}')
        self.assertEqual(r.get("secret"), "x")
        self.assertNotIn("encrypted_fields", r)

    def test_env_password_encrypts_everything(self):
        r = self._rec(env_password="pw", fields_json='{"a": "1", "b": "2"}')
        self.assertNotIn("a", r)
        self.assertNotIn("b", r)
        self.assertIn("encrypted_fields", r)

    def test_private_list_encrypts_only_those(self):
        r = self._rec(env_password="pw", fields_json='{"pub": "y", "secret": "x"}', private="secret")
        self.assertEqual(r.get("pub"), "y")          # left public
        self.assertNotIn("secret", r)                # gone from cleartext
        self.assertIn("encrypted_fields", r)

    def test_unlock_roundtrips(self):
        import mememage
        r = self._rec(env_password="pw", fields_json='{"secret": "hi there"}', private="secret")
        self.assertEqual(mememage.unlock(r, "pw").get("secret"), "hi there")

    def test_password_file_is_read(self):
        import tempfile, os, mememage
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("filepw\n")                       # trailing newline is stripped
            path = f.name
        try:
            r = self._rec(fields_json='{"secret": "hi"}', password_file=path, private="secret")
            self.assertIn("encrypted_fields", r)
            self.assertEqual(mememage.unlock(r, "filepw").get("secret"), "hi")
        finally:
            os.unlink(path)

    def test_password_file_wins_over_env(self):
        import tempfile, os, mememage
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            f.write("filepw")
            path = f.name
        try:
            r = self._rec(env_password="envpw", fields_json='{"s": "x"}',
                          password_file=path, private="s")
            self.assertEqual(mememage.unlock(r, "filepw").get("s"), "x")   # file wins
        finally:
            os.unlink(path)

    def test_env_var_name_as_path_gives_helpful_error(self):
        import torch, os
        from unittest.mock import patch
        img = torch.full((1, 512, 512, 3), 0.5)
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MEMEMAGE_PASSWORD", None)
            with self.assertRaises(ValueError) as ctx:
                nodes.MememageEncode().run(img, fields_json='{"s": "1"}', private="s",
                                           password_file="MEMEMAGE_PASSWORD", embed_workflow=False)
        self.assertIn("environment-variable name", str(ctx.exception))

    def test_private_without_password_raises(self):
        import torch, os
        from unittest.mock import patch
        img = torch.full((1, 512, 512, 3), 0.5)
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MEMEMAGE_PASSWORD", None)
            with self.assertRaises(ValueError):
                nodes.MememageEncode().run(img, fields_json='{"s": "1"}', private="s", embed_workflow=False)

    def test_encrypted_record_still_verifies(self):
        import torch, os, mememage
        from unittest.mock import patch
        img = torch.full((1, 512, 512, 3), 0.5)
        with patch.dict(os.environ, {"MEMEMAGE_PASSWORD": "pw"}, clear=False):
            barred, _, rec = nodes.MememageEncode().run(
                img, fields_json='{"secret": "x"}', private="secret", embed_workflow=False)
        np, _torch, Image = nodes._deps()
        pil = nodes._tensor_to_pil(barred[0], np, Image)
        self.assertTrue(mememage.verify(pil, json.loads(rec)))   # proof over the ciphertext shell

    def test_workflow_stays_public_by_default(self):
        # separation of concerns: selective encryption leaves comfy_prompt public
        # unless the user opts in.
        import torch, os
        from unittest.mock import patch
        img = torch.full((1, 512, 512, 3), 0.5)
        graph = {"11": {"class_type": "TextNode", "inputs": {"value": "v"}}}
        with patch.dict(os.environ, {"MEMEMAGE_PASSWORD": "pw"}, clear=False):
            _, _, rec = nodes.MememageEncode().run(
                img, fields_json='{"secret": "x"}', private="secret",
                embed_workflow=True, prompt=graph)   # encrypt_workflow defaults False
        r = json.loads(rec)
        self.assertIn("comfy_prompt", r)             # recipe stays shareable
        self.assertNotIn("secret", r)                # the field is still encrypted

    def test_encrypt_workflow_opt_in_seals_it(self):
        import torch, os, mememage
        from unittest.mock import patch
        img = torch.full((1, 512, 512, 3), 0.5)
        graph = {"11": {"class_type": "TextNode", "inputs": {"value": "v"}}}
        with patch.dict(os.environ, {"MEMEMAGE_PASSWORD": "pw"}, clear=False):
            _, _, rec = nodes.MememageEncode().run(
                img, fields_json='{"secret": "x", "pub": "ok"}', private="secret",
                embed_workflow=True, encrypt_workflow=True, prompt=graph)
        r = json.loads(rec)
        self.assertNotIn("comfy_prompt", r)          # opted in -> sealed
        self.assertNotIn("secret", r)
        self.assertEqual(r.get("pub"), "ok")         # unrelated public field stays public
        self.assertIn("comfy_prompt", mememage.unlock(r, "pw"))   # recoverable


class TestFetchRecord(unittest.TestCase):
    """Surface fetch is mocked at urllib — no real network in the suite."""

    def _fake_urlopen(self, responses):
        # responses keyed by URL minus the trailing t=0 cache-buster (so meaningful
        # query like ?list-type=2 / ?ref=main is preserved); anything else -> 404.
        import urllib.error

        class _Resp:
            def __init__(self, status, body):
                self.status = status
                self._body = body.encode("utf-8")
            def read(self):
                return self._body
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        def fake(req, timeout=None):
            url = req.full_url
            for suf in ("?t=0", "&t=0"):               # strip only the cache-buster we appended
                if url.endswith(suf):
                    url = url[:-len(suf)]
                    break
            if url in responses:
                return _Resp(*responses[url])
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        return fake

    def test_candidate_order_and_id_templating(self):
        cands = nodes._surface_candidates("https://archive.org/download/{id}/",
                                          "mememage-abcdef0123456789", "0011223344556677")
        self.assertEqual(cands, [
            "https://archive.org/download/mememage-abcdef0123456789/mememage-abcdef0123456789.soul",
            "https://archive.org/download/mememage-abcdef0123456789/mememage-abcdef0123456789.json",
            "https://archive.org/download/mememage-abcdef0123456789/mememage-abcdef0123456789.0011223344556677.soul",
            "https://archive.org/download/mememage-abcdef0123456789/mememage-abcdef0123456789.0011223344556677.json",
        ])

    def test_trailing_slash_added(self):
        cands = nodes._surface_candidates("https://souls.example.com", "mememage-1111111111111111")
        self.assertTrue(cands[0].startswith("https://souls.example.com/mememage-1111111111111111.soul"))

    def test_fetches_first_answering_variant(self):
        from unittest.mock import patch
        rec = '{"identifier": "mememage-abcdef0123456789", "k": "v"}'
        # .soul 404s, .json answers
        urls = {"https://souls.mememage.art/mememage-abcdef0123456789.json": (200, rec)}
        with patch("urllib.request.urlopen", self._fake_urlopen(urls)):
            record, ident, found, url = nodes.MememageFetchRecord().run(
                identifier="mememage-abcdef0123456789")
        self.assertTrue(found)
        self.assertEqual(json.loads(record)["k"], "v")
        self.assertEqual(ident, "mememage-abcdef0123456789")
        self.assertTrue(url.endswith(".json"))

    def test_miss_is_graceful(self):
        from unittest.mock import patch
        with patch("urllib.request.urlopen", self._fake_urlopen({})):   # everything 404s
            out = nodes.MememageFetchRecord().run(identifier="mememage-abcdef0123456789")
        self.assertEqual(out, ("", "mememage-abcdef0123456789", False, ""))

    def test_no_identifier_is_graceful(self):
        # nothing to fetch (e.g. an image with no bar) — no network call, empty out
        self.assertEqual(nodes.MememageFetchRecord().run(identifier=""), ("", "", False, ""))

    def test_content_hash_retrieves_and_does_not_reject(self):
        # the node RETRIEVES (Verify judges integrity) — passing a content_hash that
        # differs from the record's own must NOT cause a false rejection here.
        from unittest.mock import patch
        rec = '{"identifier": "mememage-abcdef0123456789", "hash_version": "1", "content_hash": "aaaa"}'
        urls = {"https://souls.mememage.art/mememage-abcdef0123456789.soul": (200, rec)}
        with patch("urllib.request.urlopen", self._fake_urlopen(urls)):
            record, ident, found, url = nodes.MememageFetchRecord().run(
                identifier="mememage-abcdef0123456789", content_hash="deadbeefdeadbeef")
        self.assertTrue(found)                         # served -> returned; no in-node hash gate
        self.assertEqual(json.loads(record)["content_hash"], "aaaa")

    def test_hashed_filename_variant_is_probed(self):
        # when only the hashed IA-style name exists, content_hash lets us find it
        from unittest.mock import patch
        rec = '{"identifier": "mememage-abcdef0123456789"}'
        hashed = ("https://archive.org/download/mememage-abcdef0123456789/"
                  "mememage-abcdef0123456789.0011223344556677.json")
        with patch("urllib.request.urlopen", self._fake_urlopen({hashed: (200, rec)})):
            record, ident, found, url = nodes.MememageFetchRecord().run(
                identifier="mememage-abcdef0123456789",
                source="https://archive.org/download/{id}/",
                content_hash="0011223344556677")
        self.assertTrue(found)
        self.assertEqual(url, hashed)

    def test_always_reruns(self):
        # network state can change -> IS_CHANGED is NaN (never cached)
        v = nodes.MememageFetchRecord.IS_CHANGED()
        self.assertNotEqual(v, v)                       # NaN != NaN

    # ---- "search the host" (search_host, on by default) ----

    ID = "mememage-abcdef0123456789"

    def _rec(self, extra=""):
        return '{"identifier": "%s"%s}' % (self.ID, (", " + extra) if extra else "")

    def test_search_off_skips_the_search(self):
        # a matching record exists under a garbage name, but with search OFF and no
        # by-ID hit, we must NOT find it (proves the host-wide search didn't run)
        from unittest.mock import patch
        urls = {"https://bucket.s3.amazonaws.com/?list-type=2":
                    (200, '<ListBucketResult><Contents><Key>junk.json</Key></Contents></ListBucketResult>'),
                "https://bucket.s3.amazonaws.com/junk.json": (200, self._rec())}
        with patch("urllib.request.urlopen", self._fake_urlopen(urls)):
            out = nodes.MememageFetchRecord().run(
                identifier=self.ID, source="https://bucket.s3.amazonaws.com/", search_host=False)
        self.assertEqual(out, ("", self.ID, False, ""))

    def test_search_s3_finds_garbage_named(self):
        from unittest.mock import patch
        listing = ('<?xml version="1.0"?><ListBucketResult>'
                   '<Contents><Key>notes.txt</Key></Contents>'
                   '<Contents><Key>weird_name.json</Key></Contents></ListBucketResult>')
        urls = {"https://bucket.s3.amazonaws.com/?list-type=2": (200, listing),
                "https://bucket.s3.amazonaws.com/weird_name.json": (200, self._rec('"k": "s3"'))}
        with patch("urllib.request.urlopen", self._fake_urlopen(urls)):
            record, ident, found, url = nodes.MememageFetchRecord().run(
                identifier=self.ID, source="https://bucket.s3.amazonaws.com/")   # search on by default
        self.assertTrue(found)
        self.assertEqual(json.loads(record)["k"], "s3")
        self.assertEqual(url, "https://bucket.s3.amazonaws.com/weird_name.json")

    def test_search_github_finds(self):
        from unittest.mock import patch
        api = "https://api.github.com/repos/owner/repo/contents/records?ref=main"
        dl = "https://raw.githubusercontent.com/owner/repo/main/records/whatever.json"
        listing = json.dumps([{"type": "file", "name": "readme.md", "download_url": "x"},
                              {"type": "file", "name": "whatever.json", "download_url": dl}])
        urls = {api: (200, listing), dl: (200, self._rec('"k": "gh"'))}
        with patch("urllib.request.urlopen", self._fake_urlopen(urls)):
            record, ident, found, url = nodes.MememageFetchRecord().run(
                identifier=self.ID, source="https://github.com/owner/repo/tree/main/records")
        self.assertTrue(found)
        self.assertEqual(json.loads(record)["k"], "gh")
        self.assertEqual(url, dl)

    def test_search_autoindex_finds(self):
        from unittest.mock import patch
        html = ('<html><body><a href="../">../</a>'
                '<a href="mess.json">mess.json</a><a href="a.txt">a.txt</a></body></html>')
        urls = {"https://mysite.com/recs/": (200, html),
                "https://mysite.com/recs/mess.json": (200, self._rec('"k": "idx"'))}
        with patch("urllib.request.urlopen", self._fake_urlopen(urls)):
            record, ident, found, url = nodes.MememageFetchRecord().run(
                identifier=self.ID, source="https://mysite.com/recs/")
        self.assertTrue(found)
        self.assertEqual(json.loads(record)["k"], "idx")
        self.assertEqual(url, "https://mysite.com/recs/mess.json")

    def test_search_skips_non_matching_records(self):
        from unittest.mock import patch
        listing = ('<ListBucketResult>'
                   '<Contents><Key>a.json</Key></Contents>'
                   '<Contents><Key>b.json</Key></Contents></ListBucketResult>')
        urls = {"https://bucket.s3.amazonaws.com/?list-type=2": (200, listing),
                "https://bucket.s3.amazonaws.com/a.json": (200, '{"identifier": "mememage-9999999999999999"}'),
                "https://bucket.s3.amazonaws.com/b.json": (200, self._rec('"k": "mine"'))}
        with patch("urllib.request.urlopen", self._fake_urlopen(urls)):
            record, ident, found, url = nodes.MememageFetchRecord().run(
                identifier=self.ID, source="https://bucket.s3.amazonaws.com/")
        self.assertTrue(found)
        self.assertEqual(json.loads(record)["k"], "mine")

    def test_search_no_listing_is_graceful(self):
        # search on, but the host offers no listing (everything 404s) -> graceful miss
        from unittest.mock import patch
        with patch("urllib.request.urlopen", self._fake_urlopen({})):
            out = nodes.MememageFetchRecord().run(
                identifier=self.ID, source="https://locked-down.example.com/")
        self.assertEqual(out, ("", self.ID, False, ""))

    def test_by_id_wins_before_search(self):
        # when the record IS named by id, the fast path returns it and search never runs
        from unittest.mock import patch
        urls = {"https://souls.mememage.art/%s.json" % ID_: (200, '{"identifier": "%s", "k": "byid"}' % ID_)
                for ID_ in [self.ID]}
        with patch("urllib.request.urlopen", self._fake_urlopen(urls)):
            record, ident, found, url = nodes.MememageFetchRecord().run(identifier=self.ID)
        self.assertTrue(found)
        self.assertEqual(json.loads(record)["k"], "byid")
        self.assertTrue(url.endswith("%s.json" % self.ID))


if __name__ == "__main__":
    unittest.main()
