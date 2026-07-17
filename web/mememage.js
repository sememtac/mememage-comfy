// Mememage — growing field INPUTS for the "Mememage Fields" node (class MememageFieldList).
//
// The node itself holds no data: each field is defined on a Mememage Field node
// and wired into a `field_*` input here. Python declares a pool (field_1..N);
// this extension starts the node with one field input and grows it — +add field,
// and auto-grow when the last input is connected — pruning trailing empties. If
// the extension fails to load, the node still works: the whole pool just shows
// up front and you wire into as many as you need.
import { app } from "../../scripts/app.js";

const NODE = "MememageFieldList";
const PREFIX = "field_";
const MAX = 8;
const TAG = "[Mememage]";
console.log(`${TAG} field-list extension loaded`);

const LG = () => window.LiteGraph || {};
const INPUT = () => (LG().INPUT ?? 1);

function fieldSlots(node) {
  const out = [];
  (node.inputs || []).forEach((inp, i) => { if (inp.name.startsWith(PREFIX)) out.push(i); });
  return out;
}

function renumber(node) {
  let n = 0;
  (node.inputs || []).forEach((inp) => { if (inp.name.startsWith(PREFIX)) inp.name = PREFIX + (++n); });
}

function refresh(node) {
  renumber(node);
  try { node.setSize(node.computeSize()); } catch (e) {}
  node.setDirtyCanvas(true, true);
}

function addField(node) {
  const slots = fieldSlots(node);
  if (slots.length >= MAX) return false;
  node.addInput(PREFIX + (slots.length + 1), "STRING");
  refresh(node);
  return true;
}

function collapseToOne(node) {
  const slots = fieldSlots(node);
  for (let j = slots.length - 1; j >= 1; j--) node.removeInput(slots[j]);
  if (fieldSlots(node).length === 0) node.addInput(PREFIX + "1", "STRING");
  refresh(node);
}

function pruneTrailingEmpties(node) {
  // keep exactly one empty spare at the end
  let slots = fieldSlots(node);
  while (slots.length > 1) {
    const last = node.inputs[slots[slots.length - 1]];
    const prev = node.inputs[slots[slots.length - 2]];
    if (last.link == null && prev.link == null) {
      node.removeInput(slots[slots.length - 1]);
      slots = fieldSlots(node);
    } else break;
  }
  refresh(node);
}

app.registerExtension({
  name: "Mememage.FieldList",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData?.name !== NODE) return;
    console.log(`${TAG} registering growing inputs on ${NODE}`);

    const onCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      onCreated?.apply(this, arguments);
      try {
        collapseToOne(this);                 // start with a single field input
        this.addWidget("button", "+ add field", null, () => addField(this)).serialize = false;
        console.log(`${TAG} field list ready — field inputs:`, fieldSlots(this).length);
      } catch (e) { console.error(`${TAG} onNodeCreated FAILED:`, e); }
    };

    // auto-grow when the last input fills; prune trailing empties on disconnect
    const onConn = nodeType.prototype.onConnectionsChange;
    nodeType.prototype.onConnectionsChange = function (type, index, connected) {
      onConn?.apply(this, arguments);
      try {
        if (type !== INPUT()) return;
        const slots = fieldSlots(this);
        const allFull = slots.length > 0 && slots.every((s) => this.inputs[s].link != null);
        if (connected && allFull) addField(this);
        if (!connected) pruneTrailingEmpties(this);
      } catch (e) { console.error(`${TAG} onConnectionsChange FAILED:`, e); }
    };
  },
});

// "pick file" buttons — open the OS file dialog (server-side) and drop the chosen
// PATH into a widget. Only the path travels; file contents never touch the browser.
const PICK_TARGETS = {
  MememageEncode: [{ widget: "password_file", label: "📁 pick password file" }],
  MememageUnlock: [{ widget: "password_file", label: "📁 pick password file" }],
  MememageLoadRecord: [{ widget: "path", label: "📁 pick record .json" }],
  MememageFindRecord: [{ widget: "folder", label: "📁 pick records folder", dir: true }],
  MememageVerify: [{ widget: "image_path", label: "📁 pick image" }],
};
// Extract Workflow: capture the embedded graph on execute, then let the user
// download it to a .json file (non-destructive — never touches the canvas). The
// graph rides in the record, so this works even when the PNG's metadata is stripped.
app.registerExtension({
  name: "Mememage.ExtractWorkflow",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData?.name !== "MememageExtractWorkflow") return;

    const onCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      onCreated?.apply(this, arguments);
      this._mmWorkflow = "";
      // Externalize, never load-in-place: download the workflow to a .json file.
      // The current graph is never touched; you open the file on your own terms
      // (drag it onto ComfyUI → it opens in a NEW tab, leaving your work intact).
      const btn = this.addWidget("button", "💾 download workflow (.json)", null, () => {
        if (!this._mmWorkflow) {
          alert("Run this node first (queue the graph) so it can read the record.");
          return;
        }
        let obj;
        try { obj = JSON.parse(this._mmWorkflow); } catch (e) { obj = null; }
        if (!obj || !Object.keys(obj).length) {
          alert("This record has no embedded workflow (it was stamped with embed_workflow off, "
                + "or the workflow is still encrypted — run it through Unlock first).");
          return;
        }
        try {
          const blob = new Blob([JSON.stringify(obj, null, 2)], { type: "application/json" });
          const url = URL.createObjectURL(blob);
          const a = document.createElement("a");
          a.href = url;
          a.download = "mememage-workflow.json";
          document.body.appendChild(a);
          a.click();
          a.remove();
          URL.revokeObjectURL(url);
        } catch (e) {
          console.error(`${TAG} download workflow failed:`, e);
          alert("Could not download workflow: " + e);
        }
      });
      btn.serialize = false;
    };

    const onExecuted = nodeType.prototype.onExecuted;
    nodeType.prototype.onExecuted = function (message) {
      onExecuted?.apply(this, arguments);
      const wf = message?.mememage_workflow?.[0];
      if (typeof wf === "string") this._mmWorkflow = wf;
    };
  },
});

app.registerExtension({
  name: "Mememage.PickFile",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    const targets = PICK_TARGETS[nodeData?.name];
    if (!targets) return;
    const onCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      onCreated?.apply(this, arguments);
      if (this._mmPickBtns) return;              // never add the buttons twice
      this._mmPickBtns = [];
      for (const cfg of targets) {
        try {
          const btn = this.addWidget("button", cfg.label, null, async () => {
            if (this._mmPicking) return;         // one dialog at a time — kills the double-prompt
            this._mmPicking = true;
            try {
              const url = cfg.dir ? "/mememage/pick_file?dir=1" : "/mememage/pick_file";
              const resp = await fetch(url, { method: "POST" });
              const { path } = await resp.json();
              if (path) {
                const w = this.widgets?.find((x) => x.name === cfg.widget);
                if (w) { w.value = path; w.callback?.(path); this.setDirtyCanvas(true, true); }
                else console.warn(`${TAG} ${cfg.widget} is an input socket — can't set a widget`);
              }
            } catch (e) { console.error(`${TAG} pick file failed:`, e); }
            finally { this._mmPicking = false; }
          });
          btn.serialize = false;
          this._mmPickBtns.push(btn);
        } catch (e) { console.error(`${TAG} pick-file button FAILED:`, e); }
      }
    };
  },
});

// Reserve ID: a stable identifier "pointer". The user defines the `prefix`
// (namespace); 🎲 randomizes only the 16-hex after it. `identifier` holds the full
// <prefix>-<hex> (the output + paste target); the two stay in sync. Persisted with
// the workflow so it stays put across renders. Wire the output into Encode's id.
function _rollHex() {
  const bytes = new Uint8Array(8);
  (window.crypto || {}).getRandomValues?.(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}
const _MM_HEX16 = /^[0-9a-f]{16}$/;
function _splitIdentifier(id) {            // "phoenix-<16hex>" -> {pre:"phoenix", hex}
  const i = (id || "").lastIndexOf("-");
  if (i <= 0) return null;
  return { pre: id.slice(0, i), hex: id.slice(i + 1) };
}
// Encrypt-workflow mirror: encrypting ANY field seals the embedded workflow, and
// the toggle can't switch that off — a field typed into a node rides the graph as
// a widget value, so publishing comfy_prompt beside its own ciphertext would hand
// back the very thing you encrypted. A switch reading OFF while sealing happens is
// a lie, so mirror the truth: `private` names a field -> force it on and grey it
// out; `private` empty -> a live switch again (a no-op either way — with no private
// list a password already encrypts every field, the graph included).
//
// Cosmetic only. Python is authoritative and seals regardless; if this extension
// fails to load the toggle just sits there inert, exactly as it did before.
const _mmWidget = (node, name) => (node.widgets || []).find((w) => w.name === name);
const _MM_FORCED_TIP = "Forced on: encrypting a field always seals the embedded workflow, "
                     + "because the graph carries that field's plaintext.";

function syncEncryptWorkflow(node) {
  const priv = _mmWidget(node, "private");
  const enc = _mmWidget(node, "encrypt_workflow");
  if (!priv || !enc) return;                       // widgets converted to inputs — nothing to mirror
  if (enc._mmTip === undefined) enc._mmTip = enc.tooltip;   // keep Python's wording to restore
  const sealing = String(priv.value ?? "").trim().length > 0;
  if (sealing) {
    if (!enc.disabled) node._mmUserEncWf = enc.value;       // remember the user's own choice
    enc.value = true;
    enc.disabled = true;
    enc.tooltip = _MM_FORCED_TIP;
  } else {
    if (enc.disabled && node._mmUserEncWf !== undefined) enc.value = node._mmUserEncWf;
    enc.disabled = false;
    enc.tooltip = enc._mmTip;
  }
  node.setDirtyCanvas(true, true);
}

app.registerExtension({
  name: "Mememage.EncryptWorkflowMirror",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData?.name !== "MememageEncode") return;

    const onCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      onCreated?.apply(this, arguments);
      try {
        const node = this;
        const priv = _mmWidget(node, "private");
        if (priv) {                                 // re-mirror whenever `private` changes
          const prev = priv.callback;
          priv.callback = function () {
            const r = prev?.apply(this, arguments);
            try { syncEncryptWorkflow(node); } catch (e) { console.error(`${TAG} mirror FAILED:`, e); }
            return r;
          };
        }
        syncEncryptWorkflow(node);
      } catch (e) { console.error(`${TAG} encrypt-workflow mirror setup FAILED:`, e); }
    };

    // A saved graph restores widget values AFTER onNodeCreated — mirror again once
    // `private` actually holds what the user saved, or a reloaded sealing graph
    // would come back showing an un-forced toggle.
    const onConfigure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function () {
      onConfigure?.apply(this, arguments);
      try { syncEncryptWorkflow(this); } catch (e) { console.error(`${TAG} mirror onConfigure FAILED:`, e); }
    };
  },
});

app.registerExtension({
  name: "Mememage.ReserveId",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData?.name !== "MememageReserveId") return;

    const onCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      onCreated?.apply(this, arguments);
      try {
        const node = this;
        const idW = _mmWidget(node, "identifier");
        const preW = _mmWidget(node, "prefix");
        if (!idW || !preW) return;

        let syncing = false;                       // guard the two-way callbacks
        const setW = (w, v) => { syncing = true; w.value = v; w.callback?.(v); syncing = false; };
        const curPrefix = () => (String(preW.value ?? "").trim() || "mememage");

        // initial state: empty identifier -> roll under the prefix; a pre-filled one
        // (paste / older graph) -> adopt its prefix so the two agree
        if (!idW.value) { setW(idW, `${curPrefix()}-${_rollHex()}`); }
        else { const s = _splitIdentifier(idW.value); if (s) setW(preW, s.pre); }

        // editing the namespace rewrites the identifier, keeping its hex (or a fresh one)
        const prevPre = preW.callback;
        preW.callback = function () {
          const r = prevPre?.apply(this, arguments);
          if (!syncing) {
            const s = _splitIdentifier(idW.value);
            const hex = (s && _MM_HEX16.test(s.hex)) ? s.hex : _rollHex();
            setW(idW, `${curPrefix()}-${hex}`);
            node.setDirtyCanvas(true, true);
          }
          return r;
        };
        // pasting a full identifier updates the namespace field to match
        const prevId = idW.callback;
        idW.callback = function () {
          const r = prevId?.apply(this, arguments);
          if (!syncing) { const s = _splitIdentifier(idW.value); if (s) setW(preW, s.pre); }
          return r;
        };

        const btn = this.addWidget("button", "🎲 new slot", null, () => {
          setW(idW, `${curPrefix()}-${_rollHex()}`);   // roll hex under the CURRENT prefix
          node.setDirtyCanvas(true, true);
        });
        btn.serialize = false;
      } catch (e) { console.error(`${TAG} reserve-id setup FAILED:`, e); }
    };

    // a saved graph restores widget values after creation — re-align the namespace
    // field to whatever identifier came back, so the two don't show out of sync
    const onConfigure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function () {
      onConfigure?.apply(this, arguments);
      try {
        const idW = _mmWidget(this, "identifier");
        const preW = _mmWidget(this, "prefix");
        if (idW && preW) { const s = _splitIdentifier(idW.value); if (s) preW.value = s.pre; }
      } catch (e) { console.error(`${TAG} reserve-id onConfigure FAILED:`, e); }
    };
  },
});
