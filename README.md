# Mememage — ComfyUI nodes

Stamp a **Mememage** bar into a generated image, and read it back — entirely in
memory, no disk round-trip.

A 2-pixel-tall bar carries an **identifier** (a key to a record you store
anywhere) and a **content hash** (proof the data belongs to this image,
untouched). With one click, the bar can also point the image back at the
**ComfyUI prompt that generated it**.

## Nodes

**Mememage Encode** — `IMAGE → IMAGE, identifier, record`
- `image` — the image to bar.
- `fields_json` *(optional)* — a JSON object of fields to attach.
- `embed_workflow` *(default on)* — include the generating prompt in the record.
- `prefix` *(default `mememage`)* — identifier namespace.
- Outputs the **barred image** (wire it into Save Image), the **identifier**, and
  the **record** JSON (store it wherever you keep your data).

**Mememage Decode** — `IMAGE (+ record) → identifier, content_hash, matched`
- Reads the bar from an image. With a record wired in, `matched` is `True` when
  the data is intact and belongs to the image.

## Install

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/sememtac/mememage-comfy
```

Then install `mememage` core into **ComfyUI's** Python (the same interpreter
ComfyUI runs):

```bash
<ComfyUI-python> -m pip install mememage          # once it's on PyPI
# until then, from a local checkout:
<ComfyUI-python> -m pip install -e /path/to/mememage
```

Restart ComfyUI — the nodes appear under the **Mememage** category. (ComfyUI
Manager / the registry will handle this automatically once published.)

## Test

`test_nodes.py` runs without pytest:

```bash
python test_nodes.py            # structure checks always; the encode→decode
                                # round-trip runs when torch is available
```

## What's in the bar

- **identifier** — *access*: a key to your record, stored anywhere (your server, a
  CDN, IPFS, a file). The bar survives JPEG, screenshots, and re-uploads.
- **content hash** — *trust*: edit any field of the record and it no longer
  matches → the image was altered.

Built on [`mememage`](https://github.com/sememtac/mememage) core.

## License

MIT.
