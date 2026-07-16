"""ComfyUI nodes for Mememage — stamp / read the pixel bar, fully in memory.

Encode a Mememage bar into a generated image (optionally carrying the ComfyUI
prompt that made it), and read it back. Built on `mememage` core's in-memory
API — PIL in, barred PIL out, no disk round-trip.
"""
import json
import subprocess
import sys


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
                "fields": ("STRING", {"forceInput": True,
                                      "tooltip": "Wire a Mememage Fields / Field node here. "
                                                 "Overrides matching keys typed in fields_json."}),
                "fields_json": ("STRING", {"multiline": True, "default": "{}"}),
                "prefix": ("STRING", {"default": "mememage"}),
                # workflow group — keep these two together
                "embed_workflow": ("BOOLEAN", {"default": True}),
                "encrypt_workflow": ("BOOLEAN", {"default": False,
                                     "tooltip": "Also encrypt the embedded workflow (comfy_prompt) when "
                                                "you're encrypting SELECT fields. Off keeps your recipe "
                                                "shareable. Turn ON if the fields you're hiding were "
                                                "entered via graph nodes — their values live in the "
                                                "workflow, so this seals them too. (Encrypt-everything "
                                                "already covers the workflow.)"}),
                # encryption group
                "password_file": ("STRING", {"default": "",
                                             "tooltip": "To encrypt, put your passphrase in a file and "
                                                        "give its PATH here (or set the MEMEMAGE_PASSWORD "
                                                        "env var). The path rides the graph — the "
                                                        "password never does, so it can't leak into the "
                                                        "PNG metadata. Empty = public record. Needs "
                                                        "mememage[encrypt]."}),
                "private": ("STRING", {"default": "",
                                       "tooltip": "Comma-separated top-level field names to encrypt. "
                                                  "Empty + a password = encrypt EVERY field."}),
                "use_identifier": ("BOOLEAN", {"default": False,
                                   "tooltip": "OFF (default): content-address — a fresh identity per "
                                              "change, ignoring the identifier below EVEN IF a Reserve ID "
                                              "wire is connected. ON: honor the wired/typed identifier to "
                                              "iterate ONE piece (each conceive overwrites the same record). "
                                              "Lets you leave a pin connected but opt in to it deliberately, "
                                              "instead of disconnecting the wire."}),
                "identifier": ("STRING", {"default": "",
                               "tooltip": "Pin a reserved identifier (wire a Mememage Reserve ID node, "
                                          "or paste a <prefix>-<16 hex>) to keep iterating ONE piece — "
                                          "each conceive overwrites the SAME record. Honored only when the "
                                          "'use_identifier' toggle above is ON. Empty = "
                                          "content-addressed (a fresh identity per change)."}),
            },
            "hidden": {"prompt": "PROMPT"},
        }

    RETURN_TYPES = ("IMAGE", "STRING", "STRING")
    RETURN_NAMES = ("image", "identifier", "record")
    FUNCTION = "run"
    CATEGORY = "Mememage"
    DESCRIPTION = ("Stamp a Mememage bar into the image. Outputs the barred image, "
                   "the identifier, and the record JSON (store it anywhere).")

    @classmethod
    def IS_CHANGED(cls, *args, **kwargs):
        # embed_workflow captures the hidden prompt, which ComfyUI doesn't track for
        # caching — always re-run so the stamped record reflects the CURRENT graph.
        return float("nan")

    def run(self, image, fields=None, fields_json="{}", embed_workflow=True, prefix="mememage",
            password_file="", private="", encrypt_workflow=False, use_identifier=False,
            identifier="", prompt=None):
        import mememage
        import copy
        import re
        np, torch, Image = _deps()

        def _parse(js, where):
            # A non-string here (e.g. a stale bool from mismatched widget slots after a
            # plugin update) is never real typed data — ignore it rather than crash. The
            # actual fields come through the `fields` input socket regardless.
            if not isinstance(js, str) or not js.strip():
                return {}
            try:
                obj = json.loads(js)
            except Exception as e:
                extra = ""
                if where == "fields_json":
                    extra = (" — if you didn't type that, this node's widgets are likely stale "
                             "after a plugin update: right-click the node and 'Fix node (Recreate)', "
                             "or delete it and add a fresh one.")
                raise ValueError(f"Mememage: {where} is not valid JSON — {e}{extra}")
            if not isinstance(obj, dict):
                raise ValueError(f"Mememage: {where} must be a JSON object")
            return obj

        fields_ = _parse(fields_json, "fields_json")         # typed defaults
        fields_.update(_parse(fields, "fields"))             # wired input overrides
        fields = fields_
        prefix = (prefix or "").strip() or "mememage"        # blank/stale prefix -> the default
        if embed_workflow and prompt is not None:
            graph = copy.deepcopy(prompt)
            for node in graph.values():
                if not isinstance(node, dict):
                    continue
                node.pop("is_changed", None)                 # ComfyUI cache artifact (can be NaN)
                inp = node.get("inputs")
                if isinstance(inp, dict):
                    inp.pop("password", None)                # never bake a typed password into the record
                    # Drop JS button widgets ComfyUI serialized as phantom inputs — their
                    # keys are labels ("📁 pick password file", "+ add field"), never valid
                    # input identifiers, so they're not real inputs and would break loaders.
                    for k in [k for k in inp if not k.isidentifier()]:
                        del inp[k]
            fields = {**fields, "comfy_prompt": graph}

        # Password comes ONLY from out-of-band sources (a file or the env var), never
        # the graph — there is no plaintext password widget by design.
        pw = _read_password(password_file)
        priv = None
        if private and private.strip():
            priv = [p.strip() for p in re.split(r"[,\n]", private) if p.strip()]
        if priv and pw is None:
            raise ValueError(
                f"Mememage: you asked to encrypt {priv}, but no password is set — so there's "
                f"nothing to encrypt with, and Mememage won't publish those fields in the clear. "
                f"Point password_file at a file holding your passphrase (the 📁 button), or set the "
                f"MEMEMAGE_PASSWORD env var. (Or clear `private` to make a fully public record.)")

        # Opt-in: also seal the embedded workflow when encrypting SELECT fields.
        # comfy_prompt captures the whole graph, including the plaintext of fields you
        # encrypt (their values ride the graph), so encrypt_workflow keeps them from
        # leaking through it. Off by default — the recipe stays shareable, the user's
        # informed choice. (Encrypt-everything, priv is None, already covers the graph.)
        if encrypt_workflow and pw is not None and priv is not None \
                and "comfy_prompt" in fields and "comfy_prompt" not in priv:
            priv = priv + ["comfy_prompt"]

        # The identifier pin is honored ONLY when use_identifier is ON — so a user can
        # leave a Reserve ID wire connected but toggle whether it actually applies,
        # rather than having to disconnect it.
        pin = ((identifier or "").strip() or None) if use_identifier else None
        out, out_identifier, record_json = [], "", ""
        for i in range(image.shape[0]):                  # handle a batch
            pil = _tensor_to_pil(image[i], np, Image)
            result = mememage.encode(pil, fields, prefix=prefix, identifier=pin, password=pw, private=priv)
            out.append(_pil_to_array(result.image, np))
            out_identifier, record_json = result.identifier, _record_json(result.record)

        barred = torch.from_numpy(np.stack(out))
        return (barred, out_identifier, record_json)


def _json_object(js, where):
    """Parse a string as a JSON object (dict), or raise. Empty -> {}."""
    if not js or not js.strip():
        return {}
    try:
        obj = json.loads(js)
    except Exception as e:
        raise ValueError(f"Mememage: {where} is not valid JSON — {e}")
    if not isinstance(obj, dict):
        raise ValueError(f"Mememage: {where} must be a JSON object")
    return obj


def _is_canonical_identifier(s):
    """<prefix>-<16 lower-hex>, prefix >= 3 chars (a light check; core validates strictly)."""
    pre, sep, idhex = (s or "").rpartition("-")
    return bool(sep) and len(pre) >= 3 and len(idhex) == 16 and all(c in "0123456789abcdef" for c in idhex)


def _record_or_none(record):
    """A record string -> dict, or None if blank / not a JSON object. NEVER raises —
    read-side nodes stay robust when an image has no bar, a record is missing, or a
    placeholder ("RECORD NOT FOUND") flows in from a switch."""
    if not isinstance(record, str) or not record.strip():
        return None
    try:
        obj = json.loads(record)
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


# Field order for the written/emitted record — purely cosmetic (the content hash is
# order-independent). Core identity + proof first for scannability, then the signer
# info, then the creator's fields (middle), then bulky/opaque blobs last.
_RECORD_HEAD = ("identifier", "content_hash", "hash_version",
                "signature", "public_key", "key_fingerprint")
_RECORD_TAIL = ("encrypted_fields", "comfy_prompt", "about")


def _order_record(rec):
    head = [k for k in _RECORD_HEAD if k in rec]
    tail = [k for k in _RECORD_TAIL if k in rec]
    placed = set(head) | set(tail)
    middle = [k for k in rec if k not in placed]
    return {k: rec[k] for k in (*head, *middle, *tail)}


def _record_json(rec):
    """A record dict -> a pretty, inspection-friendly JSON string (core fields first)."""
    return json.dumps(_order_record(rec), indent=2, ensure_ascii=False)


def _comfy_output_dir():
    """ComfyUI's output directory (where Save Image writes), or cwd headless."""
    try:
        import folder_paths
        return folder_paths.get_output_directory()
    except Exception:
        import os
        return os.getcwd()


def _read_password(password_file):
    """Resolve a passphrase from a file PATH or the MEMEMAGE_PASSWORD env var (never
    the graph). Returns None if neither is set. Raises a clear error if a path is
    given but unreadable — with a hint for the common env-var-name-as-path mix-up."""
    import os
    pw = ""
    if password_file and password_file.strip():
        path = password_file.strip()
        try:
            with open(path, encoding="utf-8") as f:
                pw = f.read().strip()
        except OSError as e:
            looks_like_env = ("/" not in path and "\\" not in path
                              and path.isupper() and path.replace("_", "").isalnum())
            if looks_like_env:
                raise ValueError(
                    f"Mememage: password_file is a FILE PATH, but {path!r} looks like an "
                    f"environment-variable name. Either (a) leave password_file empty and set "
                    f"{path} in the environment before launching ComfyUI, or (b) put your "
                    f"passphrase in a file and give its path here, e.g. /Users/you/mememage_pw.txt.")
            raise ValueError(f"Mememage: could not read password_file {path!r} — {e}")
    if not pw:
        pw = os.environ.get("MEMEMAGE_PASSWORD", "").strip()
    return pw or None


# ---------------------------------------------------------------------------
# Generation-param extraction — the "EXIF encoder".
#
# A ComfyUI graph is like a camera: node TYPES proliferate endlessly, but the
# input-parameter NAMES are a small, stable, de-facto standard the whole
# ecosystem shares (seed/steps/cfg/guidance/sampler_name/scheduler/denoise/
# ckpt_name/unet_name/lora_name). So we don't enumerate node types (a treadmill) —
# we harvest a fixed vocabulary of input NAMES onto canonical `generation` tags,
# node-type-agnostic, so KSampler and Flux's custom-sampling stack both map on.
# Topology handles the two things names can't: which conditioning is the prompt,
# and following a wired value back to its literal. Never guesses — a tag it can't
# confidently fill is simply absent. (Encode's embed_workflow still stores the
# WHOLE graph as comfy_prompt; this is the readable EXIF summary of it.)
# ---------------------------------------------------------------------------

# The params Workflow Fields can promote, each its own toggle. Robust as a filter
# (deterministic include/exclude); the *finding* of each is best-effort.
_GEN_OPTIONS = ("model", "prompt", "negative_prompt", "seed", "steps", "cfg",
                "guidance", "sampler", "scheduler", "denoise", "loras")

# canonical tag -> the input NAMES that feed it, harvested from ANY node. cfg and
# guidance are SEPARATE tags: a Flux guidance of 3.5 is not a cfg of 3.5. `model`
# is only the diffusion loaders (not `model_name`, which is upscale/controlnet).
_TAG_INPUTS = {
    "model":     ("ckpt_name", "unet_name"),
    "seed":      ("seed", "noise_seed"),
    "steps":     ("steps",),
    "cfg":       ("cfg",),
    "guidance":  ("guidance",),
    "sampler":   ("sampler_name",),
    "scheduler": ("scheduler",),
    "denoise":   ("denoise",),
}


def _is_link(v):
    """A ComfyUI wired input is a [node_id, output_slot] pair, not a literal."""
    return isinstance(v, list) and len(v) == 2 and isinstance(v[1], int)


def _node(prompt, nid):
    n = prompt.get(nid)
    if n is None and not isinstance(nid, str):
        n = prompt.get(str(nid))
    return n if isinstance(n, dict) else None


def _resolve_literal(prompt, value, prefer=(), hops=2):
    """A value -> its literal, following wires up to `hops` hops (covers a seed
    wired from a primitive, or through a reroute). `prefer` = input names to pick
    when a source node holds several literals. None if it can't be resolved."""
    seen = set()
    for _ in range(hops + 1):
        if not _is_link(value):
            return value
        nid = str(value[0])
        if nid in seen:
            return None
        seen.add(nid)
        src = _node(prompt, nid)
        if src is None:
            return None
        ins = src.get("inputs", {}) or {}
        lits = {k: v for k, v in ins.items() if not _is_link(v)}
        for p in prefer:
            if p in lits:
                return lits[p]
        if len(lits) == 1:
            return next(iter(lits.values()))
        links = [v for v in ins.values() if _is_link(v)]
        if not lits and len(links) == 1:          # pure passthrough (reroute)
            value = links[0]
            continue
        return None
    return None


def _trace_text(prompt, link, hops=6):
    """Follow a conditioning link backward to the first `text` string literal —
    through FluxGuidance, guiders, combines, CLIPTextEncode, whatever. The walk
    starts at a conditioning input, so a `text` reached on it IS the prompt."""
    stack, seen = [(link, 0)], set()
    while stack:
        lk, d = stack.pop()
        if not _is_link(lk) or d > hops:
            continue
        nid = str(lk[0])
        if nid in seen:
            continue
        seen.add(nid)
        n = _node(prompt, nid)
        if n is None:
            continue
        ins = n.get("inputs", {}) or {}
        t = _resolve_literal(prompt, ins.get("text"), prefer=("text", "value"))
        if isinstance(t, str) and t.strip():
            return t
        for v in ins.values():                    # keep tracing upstream
            if _is_link(v):
                stack.append((v, d + 1))
    return None


def _prompt_anchor(prompt):
    """(positive_link, negative_link) — the conditioning we trace prompts from.
    KSampler-family exposes positive+negative; Flux/custom routes it through a
    guider. First match wins (one sampler is the norm); a side is None when
    absent (Flux often has no negative). Even if the anchor is a controlnet-style
    node, tracing still reaches the underlying text."""
    for n in prompt.values():
        if isinstance(n, dict):
            ins = n.get("inputs", {}) or {}
            if "positive" in ins and "negative" in ins:        # KSampler family
                return ins.get("positive"), ins.get("negative")
    for n in prompt.values():
        if not isinstance(n, dict):
            continue
        ins = n.get("inputs", {}) or {}
        if _is_link(ins.get("guider")):                        # Flux SamplerCustomAdvanced
            gi = (_node(prompt, ins["guider"][0]) or {}).get("inputs", {}) or {}
            cond = gi.get("conditioning") or gi.get("positive")
            if cond is not None:
                return cond, gi.get("negative")
        if "conditioning" in ins:                              # a lone conditioning consumer
            return ins.get("conditioning"), None
    return None, None


def _extract_generation(prompt, include):
    """Map any prompt graph onto the canonical `generation` tags (see the module
    note above). `include` is the exact set of tags to emit — an off tag is never
    produced. Harvests input NAMES (node-type-agnostic), resolves wired values to
    their literals, and traces conditioning for prompts. Never guesses."""
    gen = {}

    for tag, names in _TAG_INPUTS.items():
        if tag not in include:
            continue
        for n in prompt.values():
            if not isinstance(n, dict):
                continue
            ins = n.get("inputs", {}) or {}
            hit = next((nm for nm in names if nm in ins), None)
            if hit is None:
                continue
            v = _resolve_literal(prompt, ins[hit], prefer=(hit,))
            if v is not None and not _is_link(v):
                gen[tag] = v
                break

    if "loras" in include:
        loras = []
        for n in prompt.values():
            if isinstance(n, dict) and "lora_name" in (n.get("inputs") or {}):
                v = _resolve_literal(prompt, n["inputs"]["lora_name"], prefer=("lora_name",))
                if isinstance(v, str) and v:
                    loras.append(v)
        if loras:
            gen["loras"] = loras

    if "prompt" in include or "negative_prompt" in include:
        pos, neg = _prompt_anchor(prompt)
        if "prompt" in include and pos is not None:
            t = _trace_text(prompt, pos)
            if t:
                gen["prompt"] = t
        if "negative_prompt" in include and neg is not None:
            t = _trace_text(prompt, neg)
            if t:
                gen["negative_prompt"] = t

    return gen


def _coerce(value):
    """A widget string -> a JSON value. '42' -> 42, 'true' -> True,
    '["a","b"]' -> a list; anything that isn't valid JSON stays a string."""
    s = value.strip()
    if s == "":
        return ""
    try:
        return json.loads(s)
    except Exception:
        return s


class MememageFields:
    """Bring an existing JSON object in as the record's fields.

    For when you *already have* the data as JSON — paste (or wire) a JSON object
    and it becomes the fields, validated. This is the "I have JSON" node; to build
    fields up piece by piece instead, use **Mememage Field** (one key/value) and
    **Mememage Fields** (gather several). Wire `fields` into Encode, or into a
    Mememage Fields node to merge it with hand-entered fields.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "optional": {
                "fields_json": ("STRING", {"multiline": True, "default": "{}",
                                           "placeholder": '{\n  "creator": "catmemes",\n'
                                                          '  "series": "dawn",\n'
                                                          '  "tags": ["lake", "mist"]\n}',
                                           "tooltip": "A JSON object to use as the record's fields. "
                                                      "For entering fields one at a time, use "
                                                      "Mememage Field / Mememage Fields instead."}),
            }
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("fields",)
    FUNCTION = "run"
    CATEGORY = "Mememage/Fields"
    DESCRIPTION = ("Bring an existing JSON object in as the record's fields (validated "
                   "passthrough). To enter fields one at a time, use Field / Fields.")

    def run(self, fields_json="{}"):
        return (json.dumps(_json_object(fields_json, "fields_json"), ensure_ascii=False),)


class MememageSaveRecord:
    """Save the record — and, if you wire it, the barred image — to disk.

    `mememage.encode` stamps the bar and *returns* the record, but nothing stores
    it. Wire Encode's `record` output here to write the record `.json` into ComfyUI's
    output folder. Wire Encode's `image` output into `image` too and it also writes
    the barred image as a lossless `.png`.

    **Filenames are free — the bar, not the name, links image to record.** Mememage
    reunites a body and its soul by math (identifier + content hash carried in the
    pixels), so the image and record needn't share a name, or any name in
    particular. Both `record_name` and `image_name` default to the record's
    `<identifier>` — that's the only default that (a) lets **Load Record by
    identifier** find the record with no path, and (b) gives a tidy pair that
    overwrites in place as you iterate a pinned piece. Override either to name it
    whatever you like; when a name isn't `<identifier>`, discover/verify it **by
    path** (By Soul), which is name-blind.

    The record is plain JSON — a mememage core record. Storing or serving it
    anywhere else (a folder, a CDN, the Internet Archive) is up to you; this node
    just writes to disk.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            # Both optional so `image` renders ABOVE `record`: ComfyUI puts every
            # required input above every optional one, so a required `record` forces a
            # crossed wire from Encode (whose image output sits above its record).
            # run() no-ops cleanly when no record is wired, so nothing is lost.
            "optional": {
                "image": ("IMAGE", {"tooltip": "Wire Encode's `image` output here to also save the "
                                               "barred image (lossless PNG). Optional — any saver "
                                               "works; the bar, not the file, carries identity."}),
                "record": ("STRING", {"forceInput": True,
                                      "tooltip": "Wire Encode's `record` output here — the record to save."}),
                "subfolder": ("STRING", {"default": "",
                                         "tooltip": "Optional subfolder under the "
                                                    "output directory."}),
                "record_name": ("STRING", {"default": "",
                                           "tooltip": "Filename for the record. Blank = the record's "
                                                      "<identifier> (keeps Load-Record-by-identifier "
                                                      "working). `.json` added if you omit it."}),
                "image_name": ("STRING", {"default": "",
                                          "tooltip": "Filename for the image (only if wired). Blank = "
                                                     "the <identifier>. Free to differ from the "
                                                     "record. `.png` added if you omit it."}),
            },
        }

    RETURN_TYPES = ()
    OUTPUT_NODE = True            # terminal branch — must be an output node to run at all
    FUNCTION = "run"
    CATEGORY = "Mememage/Records"
    DESCRIPTION = ("Write the record `.json` (and, if `image` is wired, the barred `.png`) to the "
                   "output folder. Filenames default to the record's identifier but are free to "
                   "override — the bar links image and record, not the name.")

    @staticmethod
    def _with_ext(name, ext):
        # respect a name the user already suffixed; otherwise add the natural extension
        import os
        return name if os.path.splitext(name)[1] else name + ext

    def run(self, record="", image=None, subfolder="", record_name="", image_name=""):
        import os
        data = _record_or_none(record)
        ident = data.get("identifier") if data else None
        if not ident:
            # blank / junk / placeholder (e.g. from a switch) — skip, don't crash or write garbage
            return {"ui": {"text": ["nothing saved — not a valid record"]}}
        out_dir = _comfy_output_dir()
        if subfolder:
            out_dir = os.path.join(out_dir, subfolder)
        os.makedirs(out_dir, exist_ok=True)
        fname = self._with_ext((record_name or "").strip() or ident, ".json")
        with open(os.path.join(out_dir, fname), "w", encoding="utf-8") as f:
            f.write(_record_json(data))          # core fields first, pretty (hash is order-independent)
        saved = [fname]
        if image is not None:
            # Lossless PNG — the bar is exact pixels; any recompression risks the read.
            np, torch, Image = _deps()
            pil = _tensor_to_pil(image[0], np, Image)
            img_name = self._with_ext((image_name or "").strip() or ident, ".png")
            pil.save(os.path.join(out_dir, img_name), "PNG")
            saved.append(img_name)
        return {"ui": {"text": ["saved " + " + ".join(saved)]}}


class MememageWorkflowFields:
    """Pull the generation parameters already in the workflow into a clean field.

    A latent carries no metadata — the settings live in the ComfyUI graph, which
    this node reads directly (no re-typing). It promotes the ones you toggle on —
    checkpoint/model, positive/negative prompt, seed, steps, cfg, sampler,
    scheduler, denoise, LoRAs — into a single `generation` field. Each has its own
    on/off switch (all default on), so you emit exactly the params you want. Wire
    `fields` into a Mememage Fields node or straight into Encode's `fields` input.

    The toggles are exact (an off param is never produced); *finding* each is
    best-effort over the standard SD/SDXL nodes — a custom sampler or exotic graph
    may leave some blank even when toggled on. (Encode's `embed_workflow` still
    stores the *entire* graph as `comfy_prompt` regardless — this is the readable,
    curated summary.)
    """

    @classmethod
    def INPUT_TYPES(cls):
        on = lambda: ("BOOLEAN", {"default": True})
        return {
            "optional": {
                "base": ("STRING", {"forceInput": True,
                                    "tooltip": "Optional upstream fields to merge first."}),
                # one toggle per param — turn off what you don't want promoted.
                "model": on(),
                "positive_prompt": on(),   # -> the `prompt` key (can't name it `prompt`: hidden PROMPT)
                "negative_prompt": on(),
                "seed": on(),
                "steps": on(),
                "cfg": on(),
                "guidance": ("BOOLEAN", {"default": True,
                             "tooltip": "Flux's guidance scale — its own tag (a distilled guidance, "
                                        "NOT interchangeable with cfg). Off if you don't want it."}),
                "sampler": on(),
                "scheduler": on(),
                "denoise": on(),
                "loras": on(),
            },
            "hidden": {"prompt": "PROMPT"},
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("fields",)
    FUNCTION = "run"
    CATEGORY = "Mememage/Fields"
    DESCRIPTION = ("Read the workflow's generation params into a clean `generation` field — "
                   "toggle exactly which ones (model, seed, prompt, …). No manual entry.")

    @classmethod
    def IS_CHANGED(cls, *args, **kwargs):
        # Reads the (untracked) hidden prompt, so ComfyUI must never cache it — else
        # a randomized seed etc. goes stale while the rest of the graph re-runs.
        return float("nan")

    def run(self, base="", model=True, positive_prompt=True, negative_prompt=True,
            seed=True, steps=True, cfg=True, guidance=True, sampler=True, scheduler=True,
            denoise=True, loras=True, prompt=None):
        toggles = {"model": model, "prompt": positive_prompt, "negative_prompt": negative_prompt,
                   "seed": seed, "steps": steps, "cfg": cfg, "guidance": guidance, "sampler": sampler,
                   "scheduler": scheduler, "denoise": denoise, "loras": loras}
        include = {name for name, want in toggles.items() if want}
        fields = dict(_json_object(base, "base"))
        if prompt and include:
            gen = _extract_generation(prompt, include)
            if gen:
                fields["generation"] = gen
        return (json.dumps(fields, ensure_ascii=False),)


class MememageField:
    """One field for a record — chain these to build a list, no fixed slots.

    `key` + `value` add a single field; `base` is everything upstream (wire a
    previous Field's `fields` output here). Grow the list by adding Field nodes
    and chaining `fields → base`. Unlike the bulk text box, each `value` can be
    converted to an input and WIRED from another node. Value is smart-typed
    (number / true·false·null / JSON array·object, else text); a blank key just
    passes `base` through, and a repeated key overrides the upstream one.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "optional": {
                "key": ("STRING", {"default": ""}),
                "value": ("STRING", {"default": "",
                                     "tooltip": "The value. Convert to an input to wire it "
                                                "from another node."}),
                "base": ("STRING", {"default": "",
                                    "tooltip": "Upstream fields to merge into — wire a previous "
                                               "Field's (or Fields') output here."}),
            }
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("fields",)
    FUNCTION = "run"
    CATEGORY = "Mememage/Fields"
    DESCRIPTION = ("Add one field (key + value) onto the upstream fields. Chain these "
                   "to build a record a field at a time; each value can be wired in.")

    def run(self, key="", value="", base=""):
        fields = {}
        if base and base.strip():
            try:
                b = json.loads(base)
            except Exception as e:
                raise ValueError(f"Mememage Field: base is not valid JSON — {e}")
            if not isinstance(b, dict):
                raise ValueError("Mememage Field: base must be a JSON object")
            fields.update(b)
        if key and key.strip():
            fields[key.strip()] = _coerce(value)
        return (json.dumps(fields, ensure_ascii=False),)


_LIST_SLOTS = 8   # pool of field inputs; the web UI grows them from one


class MememageFieldList:
    """Your fields, gathered — wire a bunch of Mememage Field nodes in here.

    This is where the fields you make come together into one bundle for Encode. It
    holds no data of its own: each field is defined on a **Mememage Field** node and
    wired into one of the `field_*` inputs, and the **+ add field** button grows the
    inputs as you connect them (without the web extension the inputs just show up
    front). Later inputs override earlier keys; `base` merges an upstream bundle
    first. Wire the `fields` output into Mememage Encode's `fields` socket.
    """

    @classmethod
    def INPUT_TYPES(cls):
        opt = {
            "base": ("STRING", {"forceInput": True,
                                "tooltip": "Optional upstream fields object, merged first "
                                           "(wire a Fields node's output here)."}),
        }
        for i in range(1, _LIST_SLOTS + 1):
            opt[f"field_{i}"] = ("STRING", {"forceInput": True,
                                            "tooltip": "Wire a Mememage Field node's "
                                                       "`fields` output here."})
        return {"optional": opt}

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("fields",)
    FUNCTION = "run"
    CATEGORY = "Mememage/Fields"
    DESCRIPTION = ("Merge several Mememage Field nodes into one fields object for Encode. "
                   "+ add field grows the inputs; later inputs override earlier keys.")

    def run(self, base="", **kwargs):
        fields = dict(_json_object(base, "base"))
        for i in range(1, _LIST_SLOTS + 1):
            v = kwargs.get(f"field_{i}", "")
            if v and v.strip():
                fields.update(_json_object(v, f"field_{i}"))
        return (json.dumps(fields, ensure_ascii=False),)


class MememageDecode:
    """Read the Mememage bar out of an image: its identifier and content hash.

    The low-level reader — "what does the bar say." Use the identifier to look the
    record up (e.g. Load Record). To check whether an image *matches* its record,
    use **Mememage Verify** — that's the verification node.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"image": ("IMAGE",)}}

    RETURN_TYPES = ("STRING", "STRING", "IMAGE")
    RETURN_NAMES = ("identifier", "content_hash", "image")
    FUNCTION = "run"
    CATEGORY = "Mememage"
    DESCRIPTION = ("Read the Mememage bar from an image: its identifier and content hash "
                   "(and passes the image through). To verify against a record, use Mememage Verify.")

    def run(self, image):
        import mememage
        np, torch, Image = _deps()
        pil = _tensor_to_pil(image[0], np, Image)

        bar = mememage.decode(pil)                    # read the bar's payload
        if bar is None:
            return ("", "", image)                    # still pass the image through
        return (bar.identifier, bar.content_hash, image)


class MememageReserveId:
    """A reserved identifier — a stable pointer you keep pointing at new versions.

    Normally every conception is content-addressed: change the image and you get a new
    identifier (a new record). But while you're *iterating one piece* in ComfyUI, you
    want a fixed identity you keep updating. This holds a reserved `<prefix>-<16 hex>`
    identifier — generated once (the 🎲 button) and saved with the workflow, so it stays
    put — that you wire into Encode's `identifier`. Every conceive (Save Record) then
    overwrites the SAME `<identifier>.json` with the current version, while the content
    hash tracks what actually changed. Paste an existing identifier to keep iterating a
    piece from a previous session; roll a new one to start a fresh slot.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "optional": {
                "identifier": ("STRING", {"default": "",
                               "tooltip": "A reserved <prefix>-<16 hex> identity. The 🎲 button fills "
                                          "it; it's saved with the workflow so it stays stable as you "
                                          "iterate. Wire the output into Encode's identifier."}),
            }
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("identifier",)
    FUNCTION = "run"
    CATEGORY = "Mememage/Records"
    DESCRIPTION = ("A reserved identifier — a stable pointer you keep iterating into. Wire into "
                   "Encode's `identifier` so each conceive overwrites the same record.")

    @classmethod
    def IS_CHANGED(cls, *args, **kwargs):
        return float("nan")                              # always emit the current slot value

    def run(self, identifier=""):
        ident = (identifier or "").strip()
        if ident and not _is_canonical_identifier(ident):
            raise ValueError(
                f"Mememage Reserve ID: {ident!r} isn't a canonical <prefix>-<16 hex> identifier. "
                "Use the 🎲 button to generate one, or paste a valid one (e.g. mememage-1a2b3c4d5e6f7a8b).")
        return (ident,)


def _load_record_file(fp):
    """(text, parsed-dict-or-None) for a record .json, or (None, None) if unreadable."""
    try:
        with open(fp, encoding="utf-8") as f:
            text = f.read()
        parsed = json.loads(text)
    except (OSError, ValueError):
        return (None, None)
    return (text, parsed if isinstance(parsed, dict) else None)


class MememageLoadRecord:
    """Load ONE record from a specific file — the complement to Save Record.

    Point it at a record `.json` by full `path` (the 📁 button); it returns the record
    text and its identifier. This is the by-**path** node — you have the exact file. To
    find a record by its identifier in a folder (e.g. from a decoded image), use
    **Mememage Find Record**; to fetch one over the network, **Mememage Fetch Record**.
    Wire `record` into Verify / Unlock; wire `identifier` into Encode to resume a piece.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "optional": {
                "path": ("STRING", {"default": "",
                                    "tooltip": "Full path to a record .json — use the 📁 button. To "
                                               "find one by identifier instead, use Mememage Find "
                                               "Record."}),
            }
        }

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("record", "identifier")
    FUNCTION = "run"
    CATEGORY = "Mememage/Records"
    DESCRIPTION = ("Load one record .json by path. To find a record by identifier, use Mememage Find "
                   "Record; to fetch one over the network, Mememage Fetch Record.")

    @classmethod
    def IS_CHANGED(cls, *args, **kwargs):
        # Reads from disk (untracked by ComfyUI) — always re-read so an edited or
        # newly-saved file isn't hidden behind a stale cache.
        return float("nan")

    def run(self, path=""):
        import os
        fp = (path or "").strip()
        if not fp:
            return ("", "")                            # nothing pointed at yet
        if not os.path.exists(fp):
            return ("", "")                            # not here — may live elsewhere; graceful
        text, parsed = _load_record_file(fp)
        if text is None:
            raise ValueError(f"Mememage Load Record: {fp!r} is missing or not valid JSON.")
        return (text, parsed.get("identifier", "") if parsed else "")


class MememageFindRecord:
    """Find a record on disk **by its identifier** — the local resolver.

    Give it an `identifier` (wire Decode's) and a `folder`; it returns the record whose
    `identifier` field matches — **by content, not filename**, so a custom-named record
    is found just the same. It tries the fast `<identifier>.json` name first, then scans
    the folder (newest wins on ties). This is the local twin of **Mememage Fetch Record**
    (same lookup, over the network); to load one exact file, use **Mememage Load Record**.
    `found` is False (and `record` empty) when no match is here — never a crash. Wire
    `record` into Verify / Unlock; `found` into a Switch to branch on it.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "optional": {
                "identifier": ("STRING", {"default": "",
                                          "tooltip": "The record to find — its `identifier` field must "
                                                     "match. Wire Decode's identifier here."}),
                "folder": ("STRING", {"default": "",
                                      "tooltip": "Folder to search (use the 📁 button). Blank = "
                                                 "ComfyUI's output folder (+ subfolder)."}),
                "subfolder": ("STRING", {"default": "",
                                         "tooltip": "Subfolder under the output folder, if Save Record "
                                                    "used one. Ignored when `folder` is set."}),
            }
        }

    RETURN_TYPES = ("STRING", "STRING", "BOOLEAN")
    RETURN_NAMES = ("record", "identifier", "found")
    FUNCTION = "run"
    CATEGORY = "Mememage/Records"
    DESCRIPTION = ("Find a record on disk by identifier (matched against each record's `identifier` "
                   "field, filename-independent) — the local twin of Fetch Record. Wire `record` into "
                   "Verify; `found` reports whether it was there.")

    @classmethod
    def IS_CHANGED(cls, *args, **kwargs):
        # Scans a folder on disk (untracked by ComfyUI) — always re-scan so a
        # record saved earlier in the same run, or any folder change, is seen.
        return float("nan")

    def run(self, identifier="", folder="", subfolder=""):
        import os, glob
        ident = (identifier or "").strip()
        if not ident:
            return ("", "", False)                     # nothing to look up (e.g. an image with no bar)

        # search root: explicit folder, else the output folder (+ subfolder)
        if folder and folder.strip():
            root = folder.strip()
        else:
            root = _comfy_output_dir()
            if subfolder and subfolder.strip():
                root = os.path.join(root, subfolder.strip())
        if not os.path.isdir(root):
            return ("", ident, False)

        # fast path: <root>/<ident>.json whose *content* identifier matches
        direct = os.path.join(root, f"{ident}.json")
        if os.path.exists(direct):
            text, parsed = _load_record_file(direct)
            if parsed is not None and parsed.get("identifier") == ident:
                return (text, ident, True)             # named by identifier (the common case) — done

        # content scan: any *.json here whose `identifier` field matches; newest wins on ties
        best = None
        for fp in glob.glob(os.path.join(root, "*.json")):
            text, parsed = _load_record_file(fp)       # skip unrelated/corrupt files silently
            if parsed is not None and parsed.get("identifier") == ident:
                mt = os.path.getmtime(fp)
                if best is None or mt > best[0]:
                    best = (mt, text)
        if best is not None:
            return (best[1], ident, True)
        return ("", ident, False)                      # no record with that identifier here


# The reference decoder's surface-fetch contract (docs/js/ui.js:fetchFromSource):
# expand {id} in the base, ensure a trailing slash, then probe filename variants in
# order — the simple self-host names first, then the hashed forms IA writes.
_SURFACE_DEFAULT = "https://souls.mememage.art/"


def _surface_candidates(base, identifier, content_hash=""):
    expanded = base.replace("{id}", identifier)
    if not expanded.endswith("/"):
        expanded += "/"
    names = [f"{identifier}.soul", f"{identifier}.json"]
    if content_hash:
        names += [f"{identifier}.{content_hash}.soul", f"{identifier}.{content_hash}.json"]
    return [expanded + n for n in names]


def _http_get_text(url, timeout, headers=None):
    """GET a URL and return its body text, or None on any non-200 / network failure.
    Adds a cache-buster so a stale listing or record is never served."""
    import urllib.request
    h = {"Cache-Control": "no-store"}
    if headers:
        h.update(headers)
    sep = "&" if "?" in url else "?"
    try:
        req = urllib.request.Request(f"{url}{sep}t=0", headers=h)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if getattr(resp, "status", 200) != 200:
                return None
            return resp.read().decode("utf-8", "replace")
    except Exception:
        return None                                    # 404 / CORS / timeout / offline


def _fetch_record_at(url, timeout):
    """GET one URL -> (raw_text, parsed_dict), or (None, None) if it isn't a JSON record."""
    txt = _http_get_text(url, timeout)
    if txt is None:
        return (None, None)
    try:
        rec = json.loads(txt)
    except ValueError:
        return (None, None)
    return (txt, rec) if isinstance(rec, dict) else (None, None)


# ---- "search the host" listers: turn a host that DOESN'T name records by ID into a
# list of candidate record URLs, so we can read each and match by the identity inside.
# Each returns absolute record URLs, or None if this host isn't of that shape.
_SCAN_CAP = 2000                                       # most records read during one search


def _list_github(base, timeout):
    import urllib.parse
    p = urllib.parse.urlparse(base)
    host = p.netloc.lower()
    parts = [x for x in p.path.split("/") if x]
    if host in ("github.com", "www.github.com"):
        if len(parts) < 2:
            return None
        owner, repo = parts[0], parts[1]
        branch, sub = "", []
        if len(parts) >= 4 and parts[2] in ("tree", "blob"):
            branch, sub = parts[3], parts[4:]
        path = "/".join(sub)
    elif host == "raw.githubusercontent.com":
        if len(parts) < 3:
            return None
        owner, repo, branch = parts[0], parts[1], parts[2]
        path = "/".join(parts[3:])
    else:
        return None
    api = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
    if branch:
        api += f"?ref={branch}"
    txt = _http_get_text(api, timeout, headers={"Accept": "application/vnd.github+json",
                                                "User-Agent": "mememage-comfy"})
    if not txt:
        return None
    try:
        items = json.loads(txt)
    except ValueError:
        return None
    if not isinstance(items, list):
        return None
    urls = [it.get("download_url") for it in items
            if isinstance(it, dict) and it.get("type") == "file"
            and str(it.get("name", "")).lower().endswith((".soul", ".json"))
            and it.get("download_url")]
    return urls or None


def _list_s3(base, timeout):
    import re, urllib.parse
    p = urllib.parse.urlparse(base)
    if not p.scheme:
        return None
    root = f"{p.scheme}://{p.netloc}/"
    prefix = p.path.lstrip("/")
    list_url = f"{root}?list-type=2"
    if prefix:
        list_url += "&prefix=" + urllib.parse.quote(prefix)
    txt = _http_get_text(list_url, timeout)
    if not txt or "ListBucketResult" not in txt:
        return None                                    # not an S3-style bucket (or listing disabled)
    keys = re.findall(r"<Key>([^<]+)</Key>", txt)
    urls = [root + urllib.parse.quote(k, safe="/") for k in keys
            if k.lower().endswith((".soul", ".json"))]
    if "<IsTruncated>true</IsTruncated>" in txt:
        print("[Mememage] search: S3 listing has more than one page; searching the first page only.")
    return urls or None


def _list_autoindex(base, timeout):
    import re, urllib.parse
    b = base if base.endswith("/") else base + "/"
    txt = _http_get_text(b, timeout)
    if not txt:
        return None
    seen, urls = set(), []
    for href in re.findall(r'href=["\']([^"\']+)["\']', txt, re.I):
        if href.startswith(("?", "#")) or ".." in href:
            continue                                   # sort links, anchors, parent-dir
        if href.split("?")[0].lower().endswith((".soul", ".json")):
            u = urllib.parse.urljoin(b, href)
            if u not in seen:
                seen.add(u)
                urls.append(u)
    return urls or None


def _list_host_records(base, timeout):
    """Return (record_urls, host_kind) using whatever listing the host offers, or ([], '')."""
    for kind, fn in (("github", _list_github), ("s3", _list_s3), ("autoindex", _list_autoindex)):
        try:
            urls = fn(base, timeout)
        except Exception:
            urls = None
        if urls:
            return urls, kind
    return [], ""


class MememageFetchRecord:
    """Fetch a record from a **surface** over the network — the "By Word" path.

    Load Record reads local disk; this is its network twin. Give an `identifier` (wire
    Decode's) and a `source` base URL. There are two ways it can find the record:

    **Straight to it, by ID (default, fast).** The identifier IS the address: the node
    goes right to `<source>/<id>.json` (also `.soul`, and the hashed `<id>.<hash>.*`
    forms IA writes when you wire `content_hash`). This is instant and needs no server
    smarts — it just works when records are named by their ID (the Mememage way, and
    how Internet Archive / a souls host store them). The base may template `{id}` for
    per-item layouts: IA is `https://archive.org/download/{id}/`; a souls host is just
    `https://souls.example.com/`.

    **Search the host (`search_host`, on by default).** If the record isn't found by
    ID — the host names records anything, a mess of files — this looks through *every*
    record the host lists and hands back the one whose identity matches your image.
    Filenames stop mattering; the record's own ID does the matching. It only kicks in
    *after* the fast path misses, so on a convention-named host it never runs and costs
    nothing — which is why it's safe to leave on. Turn it **off** for a strict,
    ID-only fetch when you know your records are named by ID and don't want the broader
    search. It needs a host that can list its files — an S3-style bucket, a GitHub
    folder, or a plain web server with directory listing on — and cleanly reports when
    a host can't be listed (nothing to search → `found = False`).

    Best-effort either way: 404 / CORS / timeout / offline all give `found = False` and
    an empty `record`, never a crashed graph. It only *retrieves* — integrity is
    **Verify**'s job (wire `record` + the image into Verify; its hash check understands
    every record version and is the authority).
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "optional": {
                "identifier": ("STRING", {"default": "",
                                          "tooltip": "The record to fetch. Wire Decode's identifier "
                                                     "here (from a second Decode to avoid a loop)."}),
                "source": ("STRING", {"default": _SURFACE_DEFAULT,
                                      "tooltip": "Surface base URL. `{id}` templates per-item layouts. "
                                                 "IA: https://archive.org/download/{id}/"}),
                "content_hash": ("STRING", {"default": "",
                                            "tooltip": "Optional. Wire Decode's content_hash to also "
                                                       "probe the hashed IA filenames "
                                                       "(<id>.<hash>.json). Verify confirms integrity."}),
                "search_host": ("BOOLEAN", {"default": True,
                                            "tooltip": "On (default): if the record isn't found by ID, "
                                                       "search every record the host lists and match "
                                                       "yours by its fingerprint — finds records with "
                                                       "ANY filename. Only runs after the fast ID lookup "
                                                       "misses, so it's free on convention-named hosts. "
                                                       "Turn OFF for a strict, fast, ID-only fetch. "
                                                       "Search needs a listable host (S3, GitHub, "
                                                       "directory-listing web servers)."}),
                "timeout": ("FLOAT", {"default": 8.0, "min": 1.0, "max": 60.0, "step": 1.0,
                                      "tooltip": "Per-request timeout in seconds."}),
            }
        }

    RETURN_TYPES = ("STRING", "STRING", "BOOLEAN", "STRING")
    RETURN_NAMES = ("record", "identifier", "found", "url")
    FUNCTION = "run"
    CATEGORY = "Mememage/Records"
    DESCRIPTION = ("Fetch a record from a surface over the network by identifier — the By-Word path. "
                   "Straight-to-it by ID (fast), then falls back to searching a messy host's files by "
                   "identity (`search_host`, on by default; off = strict ID-only). Wire into Verify.")

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")                            # network state can change — never cache the fetch

    def run(self, identifier="", source=_SURFACE_DEFAULT, content_hash="", search_host=True, timeout=8.0):
        ident = (identifier or "").strip()
        base = (source or "").strip() or _SURFACE_DEFAULT
        chash = (content_hash or "").strip()
        to = float(timeout)
        if not ident:
            return ("", "", False, "")                 # nothing to fetch (e.g. an image with no bar)

        # 1) straight to it, by ID — the naming convention (fast, no server smarts)
        for url in _surface_candidates(base, ident, chash):
            raw, rec = _fetch_record_at(url, to)
            if rec is not None:
                # Retrieve only — integrity is Verify's job (it has the image and knows every
                # record version; recomputing here with core alone would false-reject foreign
                # versions like the reference chain's hash_version 1).
                return (raw, rec.get("identifier", ident), True, url)

        # 2) search the host — read every listed record, match by the identity inside
        if search_host:
            urls, kind = _list_host_records(base, to)
            if not urls:
                print(f"[Mememage] search: nothing to search at {base} — the host offers no file "
                      f"listing (need an S3 bucket, GitHub folder, or directory-listing server).")
            for i, u in enumerate(urls):
                if i >= _SCAN_CAP:
                    print(f"[Mememage] search: stopped after {_SCAN_CAP} records without a match.")
                    break
                raw, rec = _fetch_record_at(u, to)
                if rec is not None and rec.get("identifier") == ident:
                    return (raw, ident, True, u)        # matched by identity, whatever the filename

        return ("", ident, False, "")                  # no surface answered


class MememageUnlock:
    """Decrypt a record's private fields with a password — for round-trip checks.

    ⚠️ This brings the plaintext BACK onto the graph: it can land in previews and,
    unless ComfyUI runs with --disable-metadata, the saved PNG's metadata. For
    actually *viewing* private records, use the decoder web app (it decrypts in the
    browser and forgets the password). This node is meant for verifying your own
    encryption round-trips, not for consuming private data.

    Password comes from `password_file` or the MEMEMAGE_PASSWORD env var — never the
    graph. `unlocked` is True when the private fields are readable in the output.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {"record": ("STRING", {"forceInput": True})},
            "optional": {
                "password_file": ("STRING", {"default": "",
                                             "tooltip": "Path to a file holding the passphrase (or set "
                                                        "MEMEMAGE_PASSWORD). Use the 📁 button."}),
            },
        }

    RETURN_TYPES = ("STRING", "BOOLEAN")
    RETURN_NAMES = ("record", "unlocked")
    FUNCTION = "run"
    CATEGORY = "Mememage/Records"
    DESCRIPTION = ("Decrypt a record's private fields with a password (file/env). Round-trip "
                   "check only — re-exposes plaintext on the graph; use the decoder to view.")

    def run(self, record, password_file=""):
        import mememage
        rec = _record_or_none(record)
        if rec is None:
            return (record, False)                     # not a record (blank / junk) — pass through
        if "encrypted_fields" not in rec:
            return (record, True)                      # already open — nothing to unlock
        pw = _read_password(password_file)
        if pw is None:
            return (record, False)                     # locked, no password provided
        try:
            merged = mememage.unlock(rec, pw)
        except Exception:
            return (record, False)                     # wrong password / corrupt envelope — didn't unlock, no crash
        return (_record_json(merged), True)


class MememageExtractWorkflow:
    """Pull the embedded ComfyUI workflow back out of a record.

    If the image was stamped with `embed_workflow` on, its record carries
    `comfy_prompt` — the full graph that generated it (and it rides inside the
    verifiable record, so it survives even when the PNG's own metadata is stripped).
    This surfaces that graph as a `workflow` string (API format) and a
    `has_workflow` flag. The **💾 download workflow (.json)** button writes it out
    to a file — it never touches your current canvas. Drag that file onto ComfyUI to
    open it in a NEW tab, leaving your work intact.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"record": ("STRING", {"forceInput": True})}}

    RETURN_TYPES = ("STRING", "BOOLEAN")
    RETURN_NAMES = ("workflow", "has_workflow")
    FUNCTION = "run"
    CATEGORY = "Mememage/Records"
    OUTPUT_NODE = True            # always run, so the load button has fresh data
    DESCRIPTION = ("Extract the embedded ComfyUI workflow (comfy_prompt) from a record. "
                   "Outputs the graph JSON; the button downloads it to a .json file "
                   "(non-destructive — open it in a new tab).")

    def run(self, record):
        rec = _record_or_none(record)
        wf = rec.get("comfy_prompt") if rec else None
        if not isinstance(wf, dict) or not wf:
            return {"ui": {"mememage_workflow": [""]}, "result": ("", False)}
        wf_json = json.dumps(wf, ensure_ascii=False)
        return {"ui": {"mememage_workflow": [wf_json]}, "result": (wf_json, True)}


class MememageVerify:
    """Prove an image against its record — the headline check, in one node.

    Drop an image and its `.json` record (or wire an image you just generated) and
    get a plain-language **verdict**: is this image what its record claims? This is
    the WITNESSED / by-hash check — the record's content hash is recomputed and
    compared to the image's bar. (Signature/portrait checks — AUTHENTICATED /
    EMBODIED — live in the decoder web app; this node verifies integrity by hash.)
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "optional": {
                "image": ("IMAGE",),                         # wire one you just made (takes priority)
                "image_path": ("STRING", {"default": "",
                                          "tooltip": "Path to an image file (use the 📁 button). "
                                                     "Ignored if an image is wired in."}),
                "record": ("STRING", {"forceInput": True,
                                      "tooltip": "Wire a record in — from Mememage Load / Find / "
                                                 "Fetch Record, or Encode."}),
            }
        }

    RETURN_TYPES = ("STRING", "BOOLEAN", "STRING", "IMAGE")
    RETURN_NAMES = ("verdict", "matched", "identifier", "image")
    FUNCTION = "run"
    CATEGORY = "Mememage"
    DESCRIPTION = ("Verify an image against its record and report a plain-language verdict "
                   "(VERIFIED / ALTERED / UNSUPPORTED / NO BAR). Integrity by hash — the WITNESSED "
                   "check. UNSUPPORTED = the record's hash_version is an app-defined model core "
                   "doesn't implement (not tampered — verify it in that app's decoder).")

    def run(self, image=None, image_path="", record=None):
        import mememage
        np, torch, Image = _deps()

        if image is not None:
            pil = _tensor_to_pil(image[0], np, Image)
            img_out = image
        elif image_path and image_path.strip():
            try:
                pil = Image.open(image_path.strip()).convert("RGB")
            except Exception as e:
                raise ValueError(f"Mememage Verify: could not open image {image_path.strip()!r} — {e}")
            img_out = torch.from_numpy(np.stack([_pil_to_array(pil, np)]))
        else:
            raise ValueError("Mememage Verify: wire an image or pick an image_path.")

        rec = _record_or_none(record)                # tolerant of blank / non-JSON / placeholders

        bar = mememage.decode(pil)
        if bar is None:
            return ("NO BAR — this image has no readable Mememage bar (not a Mememage image, or it "
                    "was resized/cropped past recovery).", False, "", img_out)
        identifier = bar.identifier
        if rec is None:
            return (f"NO RECORD — the bar reads {identifier}, but no record was given to check it "
                    f"against. Wire a record in — from Load / Find / Fetch Record.", False, identifier, img_out)
        v = mememage.verify(pil, rec)
        matched = bool(v)
        if matched:
            verdict = (f"VERIFIED — the record matches this image by hash; the data is intact and "
                       f"belongs to it. ({identifier})")
        elif not getattr(v, "supported", True):
            # The record declares a hash_version core doesn't implement (an app-defined
            # model, e.g. the canonical chain's V1). Not tampered — core just can't judge it.
            hv = rec.get("hash_version", "?")
            verdict = (f"UNSUPPORTED — this record uses hash_version {hv!r}, a hash model this "
                       f"mememage core build doesn't implement, so it can't be verified here. "
                       f"Verify it with the application that defines this version (e.g. its web "
                       f"decoder at souls.mememage.art). This is NOT tamper evidence. ({identifier})")
        else:
            verdict = (f"ALTERED — the record does NOT match this image (tampered data, or the wrong "
                       f"record for it). ({identifier})")
        return (verdict, matched, identifier, img_out)


def _native_pick_file(title="Select your Mememage password file", directory=False):
    """Open the OS's native file (or folder, if `directory`) dialog on the machine
    running ComfyUI, and return the chosen path (or "" if cancelled/unavailable).
    Returns a PATH only — file contents never leave disk. Local ComfyUI only (the
    dialog is server-side).
    """
    plat = sys.platform
    try:
        if plat == "darwin":
            verb = "choose folder" if directory else "choose file"
            script = f'POSIX path of ({verb} with prompt "{title}")'
            r = subprocess.run(["osascript", "-e", script], capture_output=True,
                               text=True, timeout=300)
            if r.returncode == 0:
                return r.stdout.strip()
        elif plat.startswith("linux"):
            zenity = ["zenity", "--file-selection", f"--title={title}"]
            if directory:
                zenity.append("--directory")
            kdialog = ["kdialog", "--getexistingdirectory", "."] if directory \
                else ["kdialog", "--getopenfilename"]
            for tool in (zenity, kdialog):
                try:
                    r = subprocess.run(tool, capture_output=True, text=True, timeout=300)
                    if r.returncode == 0:
                        return r.stdout.strip()
                except FileNotFoundError:
                    continue
        elif plat.startswith("win"):
            if directory:
                ps = ("Add-Type -AssemblyName System.Windows.Forms; "
                      "$f = New-Object System.Windows.Forms.FolderBrowserDialog; "
                      f"$f.Description='{title}'; if($f.ShowDialog() -eq 'OK'){{$f.SelectedPath}}")
            else:
                ps = ("Add-Type -AssemblyName System.Windows.Forms; "
                      "$f = New-Object System.Windows.Forms.OpenFileDialog; "
                      f"$f.Title='{title}'; if($f.ShowDialog() -eq 'OK'){{$f.FileName}}")
            r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                               capture_output=True, text=True, timeout=300)
            if r.returncode == 0:
                return r.stdout.strip()
    except Exception:
        pass
    return ""


# Register a tiny backend route so the node's "pick file" button can open the OS
# dialog. No-ops outside a running ComfyUI server (e.g. headless imports / tests).
# Register the pick-file endpoint the node's button calls. Must go straight onto
# the app: ComfyUI (0.27) consumes its @routes table before custom nodes load, so
# the decorator would silently no-op. Wrapped so headless imports (tests) skip it.
try:
    from server import PromptServer          # ComfyUI's server
    from aiohttp import web

    async def _mememage_pick_file(request):
        if request.query.get("probe"):
            return web.json_response({"ok": True})     # routing health check, no dialog
        if request.query.get("dir"):                    # ?dir=1 -> folder picker
            return web.json_response({"path": _native_pick_file(title="Select a folder",
                                                                directory=True)})
        return web.json_response({"path": _native_pick_file()})     # file, keeps its default title

    PromptServer.instance.app.add_routes([web.post("/mememage/pick_file", _mememage_pick_file)])
except Exception:
    pass


NODE_CLASS_MAPPINGS = {
    "MememageEncode": MememageEncode,
    "MememageSaveRecord": MememageSaveRecord,
    "MememageWorkflowFields": MememageWorkflowFields,
    "MememageFields": MememageFields,
    "MememageField": MememageField,
    "MememageFieldList": MememageFieldList,
    "MememageDecode": MememageDecode,
    "MememageLoadRecord": MememageLoadRecord,
    "MememageFindRecord": MememageFindRecord,
    "MememageFetchRecord": MememageFetchRecord,
    "MememageUnlock": MememageUnlock,
    "MememageExtractWorkflow": MememageExtractWorkflow,
    "MememageVerify": MememageVerify,
    "MememageReserveId": MememageReserveId,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "MememageEncode": "Mememage Encode",
    "MememageReserveId": "Mememage Reserve ID",
    "MememageSaveRecord": "Mememage Save Record",
    "MememageWorkflowFields": "Mememage Workflow Fields",
    "MememageFields": "Mememage JSON",               # the "I already have JSON" node
    "MememageField": "Mememage Field",
    "MememageFieldList": "Mememage Fields",          # friendly name — "the node your fields go into"
    "MememageDecode": "Mememage Decode",
    "MememageLoadRecord": "Mememage Load Record",
    "MememageFindRecord": "Mememage Find Record",
    "MememageFetchRecord": "Mememage Fetch Record",
    "MememageUnlock": "Mememage Unlock",
    "MememageExtractWorkflow": "Mememage Extract Workflow",
    "MememageVerify": "Mememage Verify",
}
