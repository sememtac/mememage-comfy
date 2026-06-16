"""ComfyUI nodes for Mememage — stamp / read the pixel bar, fully in memory.

Encode a Mememage bar into a generated image (optionally carrying the ComfyUI
prompt that made it), and read it back. Built on `mememage` core's in-memory
API — PIL in, barred PIL out, no disk round-trip.
"""
import json


def _deps():
    # ComfyUI always ships numpy + torch + Pillow.
    import numpy as np
    import torch
    from PIL import Image
    return np, torch, Image


def _tensor_to_pil(img_hwc, np, Image):
    """One ComfyUI image (a [H,W,C] float 0-1 tensor) -> a PIL Image."""
    arr = (img_hwc.cpu().numpy() * 255.0).clip(0, 255).astype("uint8")
    return Image.fromarray(arr)


def _pil_to_array(pil, np):
    """A PIL Image -> a [H,W,C] float32 0-1 numpy array (ComfyUI's image format)."""
    return np.asarray(pil.convert("RGB")).astype("float32") / 255.0


class MememageEncode:
    """Stamp a Mememage bar into the image and emit its identifier + record.

    The bar carries an identifier (a key to the record) and a content hash. The
    record is JSON you define — optionally including the ComfyUI prompt that
    generated the image, so the picture points back to its own recipe.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {"image": ("IMAGE",)},
            "optional": {
                "fields_json": ("STRING", {"multiline": True, "default": "{}"}),
                "embed_workflow": ("BOOLEAN", {"default": True}),
                "prefix": ("STRING", {"default": "mememage"}),
            },
            "hidden": {"prompt": "PROMPT"},
        }

    RETURN_TYPES = ("IMAGE", "STRING", "STRING")
    RETURN_NAMES = ("image", "identifier", "record")
    FUNCTION = "run"
    CATEGORY = "Mememage"
    DESCRIPTION = ("Stamp a Mememage bar into the image. Outputs the barred image, "
                   "the identifier, and the record JSON (store it anywhere).")

    def run(self, image, fields_json="{}", embed_workflow=True, prefix="mememage", prompt=None):
        import mememage
        np, torch, Image = _deps()

        fields = {}
        if fields_json and fields_json.strip():
            try:
                fields = json.loads(fields_json)
            except Exception as e:
                raise ValueError(f"Mememage: fields_json is not valid JSON — {e}")
            if not isinstance(fields, dict):
                raise ValueError("Mememage: fields_json must be a JSON object")
        if embed_workflow and prompt is not None:
            fields = {**fields, "comfy_prompt": prompt}

        out, identifier, record_json = [], "", ""
        for i in range(image.shape[0]):                  # handle a batch
            pil = _tensor_to_pil(image[i], np, Image)
            result = mememage.encode(pil, fields, prefix=prefix)   # PIL in -> barred PIL out
            out.append(_pil_to_array(result.image, np))
            identifier, record_json = result.identifier, result.to_json()

        barred = torch.from_numpy(np.stack(out))
        return (barred, identifier, record_json)


class MememageDecode:
    """Read the Mememage bar out of an image: identifier + content hash.

    With a record (JSON) wired in, also reports whether it matches the image.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {"image": ("IMAGE",)},
            "optional": {"record_json": ("STRING", {"multiline": True, "default": ""})},
        }

    RETURN_TYPES = ("STRING", "STRING", "BOOLEAN")
    RETURN_NAMES = ("identifier", "content_hash", "matched")
    FUNCTION = "run"
    CATEGORY = "Mememage"
    DESCRIPTION = ("Read the Mememage bar from an image. With a record wired in, "
                   "'matched' is True when the data is intact and belongs to it.")

    def run(self, image, record_json=""):
        import mememage
        np, torch, Image = _deps()
        pil = _tensor_to_pil(image[0], np, Image)

        bar = mememage.decode(pil)                    # read the bar's payload
        if bar is None:
            return ("", "", False)
        matched = False
        if record_json and record_json.strip():       # verify against a wired-in record
            matched = bool(mememage.verify(pil, json.loads(record_json)))
        return (bar.identifier, bar.content_hash, matched)


NODE_CLASS_MAPPINGS = {
    "MememageEncode": MememageEncode,
    "MememageDecode": MememageDecode,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "MememageEncode": "Mememage Encode",
    "MememageDecode": "Mememage Decode",
}
