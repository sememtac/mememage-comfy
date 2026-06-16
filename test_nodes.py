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


class TestNodeContract(unittest.TestCase):
    def test_mappings(self):
        self.assertIn("MememageEncode", nodes.NODE_CLASS_MAPPINGS)
        self.assertIn("MememageDecode", nodes.NODE_CLASS_MAPPINGS)
        self.assertIn("MememageEncode", nodes.NODE_DISPLAY_NAME_MAPPINGS)

    def test_comfy_interface(self):
        for cls in nodes.NODE_CLASS_MAPPINGS.values():
            self.assertTrue(callable(cls.INPUT_TYPES))
            self.assertTrue(cls.RETURN_TYPES)
            self.assertTrue(cls.FUNCTION)
            self.assertEqual(cls.CATEGORY, "Mememage")
            self.assertTrue(hasattr(cls, cls.FUNCTION))
            self.assertIn("image", cls.INPUT_TYPES()["required"])


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

        ident, chash, matched = nodes.MememageDecode().run(barred, record_json=record)
        self.assertEqual(ident, identifier)
        self.assertTrue(matched)


if __name__ == "__main__":
    unittest.main()
