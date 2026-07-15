# Mememage — ComfyUI nodes

Stamp a **Mememage** bar into a generated image, and read it back — entirely in
memory, no disk round-trip.

A 2-pixel-tall bar carries an **identifier** (a key to a record you store
anywhere) and a **content hash** (proof the data belongs to this image,
untouched). With one click, the bar can also point the image back at the
**ComfyUI prompt that generated it**.

## Nodes

**Mememage Encode** — `IMAGE (+ fields) → IMAGE, identifier, record`
- `image` — the image to bar.
- `fields` *(input socket)* — wire a **Fields / Field** node here.
  Overrides matching keys from `fields_json`.
- `fields_json` *(optional box)* — type a JSON object of fields inline instead.
- `embed_workflow` *(default on)* — include the generating prompt in the record.
- `prefix` *(default `mememage`)* — identifier namespace.
- `password_file` — path to a file holding your passphrase (empty = public). By
  design there is **no plaintext password field** — the password comes only from
  this file or the `MEMEMAGE_PASSWORD` env var, so it can never ride the graph into
  the PNG metadata. Use the **📁 pick password file** button on the node to choose
  the file with a native dialog instead of typing the path. See **Encrypting
  fields** below.
- `private` — comma-separated top-level field names to encrypt; empty + a
  password = encrypt everything.
- `use_identifier` *(default on)* — honor the `identifier` below. Turn it **off** to
  ignore the identifier (content-address as usual) *without disconnecting the wire* —
  handy when a Reserve ID is wired in but you want a fresh identity for this one run.
- `identifier` — pin a reserved identifier (from **Mememage Reserve ID**, or paste a
  `<prefix>-<16 hex>`) to keep iterating **one** piece: each conceive overwrites the
  same record. Honored only when `use_identifier` is on. Empty = content-addressed
  (a fresh identity per change). See **Iterating one piece** below.
- Outputs the **barred image** (wire it into Save Image), the **identifier**, and
  the **record** JSON (store it wherever you keep your data).

### Iterating one piece (the reserved-identifier "pointer")

Mememage is normally *content-addressed* — change the image and you get a new
identifier, a new record. That's right for finished work, but ComfyUI is a
non-destructive workflow: you iterate. **Conceiving is the deliberate act** (running
**Save Record**); while iterating, just don't save.

To keep refining **one** piece under a stable identity, drop a **Mememage Reserve ID**
node — it mints a `<prefix>-<16 hex>` identifier once (the 🎲 button), saved with the
workflow so it stays put — and wire it into Encode's `identifier`. Now the identifier
is a fixed **pointer**; each conceive overwrites the same `<identifier>.json` while the
content hash tracks what actually changed. Roll a new slot to start a fresh piece;
paste an existing identifier to resume one from another session.

### Encrypting fields

Provide a password to encrypt fields with AES-256 (via mememage core) — via
`password_file` (a path to a file holding the passphrase) or the
`MEMEMAGE_PASSWORD` env var. **There is no plaintext password field, on purpose**
(see below). List names in `private` to encrypt just those, or leave it empty to
encrypt every field. The private fields leave the cleartext record and become an
`encrypted_fields` envelope; the record still **verifies without the password**
(the hash covers the ciphertext), and `mememage.unlock(record, password)` (or the
reference decoder's password box) reveals them. Needs the crypto library:
`pip install cryptography` into ComfyUI's Python (or `mememage[encrypt]`).

> **Read this before you rely on it.** ComfyUI, by default, bakes the *entire
> workflow graph* into every saved PNG's metadata (a `prompt` + `workflow` text
> chunk). That means anything on the graph — and the **plaintext of any field
> you're hiding**, if it's entered via graph nodes — gets embedded in the very
> image you share. That's why the password is file/env only (it never touches the
> graph). It's also why, when you encrypt select fields whose values came from graph
> nodes, you'll usually want **`encrypt_workflow`** on: the embedded `comfy_prompt`
> mirrors those values, so sealing it stops them leaking. It's **opt-in** (off by
> default) so your recipe stays shareable when that's what you want — a deliberate
> choice, not a silent one. (Encrypt-everything already covers the workflow.) To keep
> encrypted fields actually secret in a shared image:
> 1. **Run ComfyUI with `--disable-metadata`** so the graph isn't written into the PNG.
> 2. **Provide the password via `password_file` or `MEMEMAGE_PASSWORD`** — never on
>    the graph. Resolution order: `password_file` → env var.
> 3. **Turn on `encrypt_workflow`** if the fields you're hiding were entered via graph
>    nodes (so `comfy_prompt` doesn't carry their plaintext).
>
> The record `.json` itself is always safe — `encrypted_fields` is ciphertext, and
> the node never writes the password into it. The leak is purely ComfyUI's own PNG
> metadata; the two steps above close it.

**Mememage JSON** — `→ fields`
- **Bring an existing JSON object in as fields.** For when you *already have* the
  data as JSON — paste (or wire) a JSON object and it becomes the record's fields,
  validated:

  ```json
  { "creator": "catmemes", "series": "dawn", "tags": ["lake", "mist"] }
  ```

  This is the "I have JSON" node. To build fields up **one at a time**, use
  **Mememage Field** + **Mememage Fields** instead — that's the granular path.
  Wire this node's `fields` output into Encode, or into a **Mememage Fields** node
  to merge it with hand-entered fields.

**Mememage Save Record** — `record →` *(+ optional `image →`)* *(output node)*
- Encode only *returns* the record; nothing stores it. Wire Encode's `record`
  output here to write the record `.json` into the ComfyUI output folder. **Wire
  Encode's `image` output into `image` too** and it also writes the barred image as
  a lossless `.png`. (The image is optional here — any saver works, because the bar
  carries identity, not the file.)
- **Filenames are free — the bar links image to record, not the name.** Mememage
  reunites a body and its soul by math (the identifier + content hash live in the
  pixels), so the two files needn't share a name, or any particular name.
  `record_name` and `image_name` both default to the record's **`<identifier>`** —
  the one default that lets **Find Record** locate the record by identifier with no
  path *and* gives a tidy pair that overwrites in place as you iterate a pinned
  piece. Override either freely (a bare stem gets `.json`/`.png` added; a name with
  its own extension is used as-is). When a name isn't `<identifier>`, load/verify it
  **by path** (By Soul) — that route is name-blind.
- **On drift, for a pinned piece:** with a pinned identifier the record only ever
  holds the *latest* content hash, so only your newest render matches it — earlier
  renders (e.g. numbered Save Image files) won't verify. That's the pointer's
  contract, not a filename issue; renaming can't change it. The default identifier
  pair simply keeps one current pair in front of you. (Keep Save Image too if you
  want a numbered history.)
- The record is plain JSON (a mememage core record); serving it elsewhere is up
  to you.

**Mememage Workflow Fields** — `(base) → fields`
- **Reads the params already in your workflow — no re-typing.** A latent carries no
  metadata; the settings live in the graph, which this node reads directly. It
  promotes the ones you toggle on into a clean **`generation`** field. Each param
  has its own on/off switch (all default on): **model, positive_prompt,
  negative_prompt, seed, steps, cfg, guidance, sampler, scheduler, denoise, loras** —
  so you emit exactly what you want. Wire it into a Mememage Fields node or into Encode.
  (`cfg` and `guidance` are separate tags — a Flux guidance isn't a cfg.)

  **How it finds them — think of it as EXIF for your render.** Rather than hunting for
  specific node *types* (which multiply endlessly), it harvests a fixed vocabulary of
  input *names* (`seed`/`noise_seed`, `steps`, `cfg`/`guidance`, `sampler_name`, …)
  onto those tags — so it maps the same whether you use **KSampler** or a **Flux
  custom-sampling** stack, and future samplers work for free. It follows wired values
  back to their literal (a seed driven from a primitive is captured, not blanked) and
  traces the conditioning graph for the prompt. Toggles are exact — an off param is
  never produced — and it **never guesses**: a param it can't confidently read is
  simply absent. *(Encode's `embed_workflow` still stores the entire graph as
  `comfy_prompt` regardless — this is the tidy, curated summary of it.)*

**Mememage Fields** — `(field_1…field_N, base) → fields`
- **Where your fields come together.** It holds no data itself — each field is
  defined on a **Mememage Field** node and wired into a `field_*` input here. Click
  **+ add field** to grow the inputs one at a time (and they auto-grow as you
  connect the last one); wire the `fields` output into Encode's `fields` socket.
  Later inputs override earlier keys; `base` merges an upstream bundle first. *(The
  growing inputs are drawn by the plugin's web extension. If it doesn't load, the
  node still works — the whole pool of inputs just shows up front and you wire into
  as many as you need.)*

**Mememage Field** *(singular)* — `(base) → fields`
- One field at a time — `key` + `value`, plus a `base` input for everything
  upstream. **Chain them to grow a list**: `Field → Field → Field → Encode`, each
  wiring its `fields` output into the next node's `base`. Unlike the bulk text
  box, each `value` can be converted to an input and **wired from another node**,
  so a field's value can come from elsewhere in the graph. Same smart-typing; a
  blank key passes `base` through; a repeated key overrides upstream.

**Mememage Verify** — `(image / image_path, record) → verdict, matched, identifier, image`
- **The headline check.** Wire an image (or pick an image file) and a **record** (from
  Load / Find / Fetch Record), and get a plain-language **`verdict`**: `VERIFIED —
  record matches, untampered` / `ALTERED — record doesn't match` / `NO BAR`. This is the
  integrity (by-hash) check — the **WITNESSED** badge. Signature (AUTHENTICATED) and
  portrait (EMBODIED) checks live in the decoder web app; this verifies by hash.
  *(Record loading lives in the Load / Find / Fetch Record nodes — Verify just verifies.)*

**Mememage Decode** — `IMAGE → identifier, content_hash, image`
- The low-level reader: pulls the bar's **identifier** and **content hash** out of an
  image (and passes the **image** through so it chains onward). Use the identifier to
  look the record up (e.g. Load Record). To check whether an image *matches* its
  record, use **Mememage Verify** — that's the verification node.

**Mememage Reserve ID** — `→ identifier`
- A stable identifier **pointer** for iterating one piece. The **🎲 new slot** button
  mints a fresh `<prefix>-<16 hex>` (saved with the workflow); wire the output into
  Encode's `identifier` so every conceive overwrites the same record. Paste an existing
  identifier to resume a piece. See **Iterating one piece** above.

**Mememage Load Record** — `path → record, identifier`
- The complement to Save Record: read **one** saved `.json` back from disk by full
  `path` (📁 button) — you have the exact file. Outputs the `record` (wire into
  **Verify** / **Unlock** / a Preview) and its `identifier` (wire into **Encode** to
  resume iterating that piece). To find a record *by its identifier* instead, use
  **Find Record**; over the network, **Fetch Record**.

**Mememage Find Record** — `(identifier, folder, subfolder) → record, identifier, found`
- **Find a record on disk by its identifier — the local resolver.** Give it an
  `identifier` (wire Decode's) and a `folder`, and it returns the record whose
  `identifier` field matches — **by content, not filename**. It tries the fast
  `<identifier>.json` name first, then **scans the folder** (newest wins on ties), so a
  custom-named record (`dawn_soul.json`) is found just the same — which is what lets
  Save Record name files freely. `found` is `False` (and `record` empty) when nothing
  matches — wire it into a Switch to branch. Blank `folder` = ComfyUI's output folder
  (+ `subfolder`).
- This is the **local twin of Fetch Record** (same lookup, over the network), and the
  star of the decode→verify flow: **`Decode.identifier → Find Record → Verify`**.

**Mememage Fetch Record** — `→ record, identifier, found, url`
- The **network** twin of Load Record — the "By Word" path. Load Record reads local
  disk; this GETs the record a **surface** serves for an identifier: Internet
  Archive, a self-hosted souls host, any URL. Wire Decode's `identifier` and set a
  `source` base URL — `{id}` templates per-item layouts (IA is
  `https://archive.org/download/{id}/`; a souls host is just
  `https://souls.example.com/`, default `https://souls.mememage.art/`).
- **Two ways it finds the record:**
  - **Straight to it, by ID (fast).** The identifier *is* the address — it probes
    `<source>/<id>.soul`, `<id>.json`, and (when you wire `content_hash`) the hashed
    `<id>.<hash>.*` forms IA writes, returning the first that answers. Instant, no
    server smarts, works when records are named by their ID (the Mememage way).
  - **Search the host (`search_host`, on by default).** If the by-ID lookup misses —
    the host names records anything, a mess — it looks through *every* record the
    host lists and returns the one whose identity matches your image. Filenames stop
    mattering. It only runs *after* the fast path misses, so on a convention-named
    host it never fires and costs nothing — which is why it's safe to leave on. Turn
    it **off** for a strict, fast, ID-only fetch. Search needs a host that can list
    its files: **S3-style buckets**, **GitHub folders**, and **directory-listing web
    servers** (nginx/Apache autoindex). A host that can't be listed cleanly reports
    "nothing to search" → `found = False`.
- **Best-effort and honest:** a 404, CORS block, timeout, or offline network all
  give `found = False` and an empty `record` — never a crashed graph. It only
  *retrieves*; integrity is **Verify**'s job (wire `record` + the image into Verify —
  its hash check understands every record version and is the authority).

**Mememage Extract Workflow** — `record → workflow, has_workflow`
- If the image was stamped with `embed_workflow` on, its record carries the *whole
  ComfyUI graph that made it* — and it rides inside the verifiable record, so it
  survives even when the PNG's own metadata is stripped. This pulls that graph out
  as a `workflow` string (API format) and a `has_workflow` flag. The **💾 download
  workflow (.json)** button writes it to a file — **never touching your current
  canvas** (queue the node once first so it can read the record). Drag the
  downloaded file onto ComfyUI to open it in a **new tab**, leaving your work intact.
  Or wire `workflow` into a Preview to just read it.

**Mememage Unlock** — `record (+ password) → record, unlocked`
- Decrypt a record's private fields with a password (`password_file` or the
  `MEMEMAGE_PASSWORD` env var). `unlocked` is `True` when the private fields are
  readable in the output.

  > ⚠️ For **round-trip checks only**. Unlocking here brings the plaintext back
  > onto the graph, so it can land in previews and (without `--disable-metadata`)
  > the saved PNG's metadata — re-exposing what you encrypted. To actually *view*
  > private records, use the decoder web app: it decrypts in the browser and
  > forgets the password.

## Example workflows

- `example_workflow_sd15.json` — SD1.5 text-to-image → Encode → Save Image.
- `example_workflow_fields.json` — same, with a **Fields** node feeding Encode.

Drag either onto the ComfyUI canvas (or paste it with Ctrl+V) to populate the graph.

## Install

**ComfyUI Manager (recommended).** Search **"Mememage"** in the Manager and click
**Install** — it pulls the node and its `mememage` dependency (from `requirements.txt`)
for you. Or use **Install via Git URL** with `https://github.com/sememtac/mememage-comfy`.
Restart ComfyUI.

**Manual (git).**

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/sememtac/mememage-comfy
# then install the core into ComfyUI's OWN python:
<ComfyUI-python> -m pip install mememage
```

Restart ComfyUI — the nodes appear under the **Mememage** category.

## Test

`test_nodes.py` runs without pytest:

```bash
python test_nodes.py            # structure checks always; the encode→decode
                                # round-trip runs when torch is available
```

CI (`.github/workflows/test.yml`) runs the full suite on every push and PR — so a
broken change can't be tagged for the registry.

## What's in the bar

- **identifier** — *access*: a key to your record, stored anywhere (your server, a
  CDN, IPFS, a file). The bar survives JPEG, screenshots, and re-uploads.
- **content hash** — *trust*: edit any field of the record and it no longer
  matches → the image was altered.

Built on [`mememage`](https://github.com/sememtac/mememage) core.

## Publishing (maintainer)

Distributed through the **ComfyUI Registry** → **ComfyUI Manager** (one-click install
for users). It's the same rhythm as a PyPI release: bump the version, publish, users
see an update in Manager.

**One-time setup** — *done* (kept here for reference / re-setup):
1. Sign in at **registry.comfy.org** with GitHub.
2. Create a **publisher** whose id matches `PublisherId` in `pyproject.toml` (`catmemes`).
3. Generate an **API key** for that publisher.
4. Add the key to the `sememtac/mememage-comfy` repo as an Actions secret named
   **`REGISTRY_ACCESS_TOKEN`** (Settings → Secrets and variables → Actions).

**Each release:**
- Bump `version` in `pyproject.toml`, commit (CI's `test.yml` runs the suite on the
  push), then `git tag vX.Y.Z && git push --tags`.
- The `.github/workflows/publish.yml` action runs `comfy node publish` for you.
- (Manual alternative: `pip install comfy-cli`, then `comfy node publish` from this folder.)

The pack depends on the core `mememage` library from PyPI (`requirements.txt`), which
ComfyUI installs automatically. Encryption features additionally need `cryptography`
(`pip install mememage[encrypt]`) — kept optional so a plain install stays lean.

## License

MIT.
