"""Editing an item: 👁 shows the saved password (and card number, CVV, PIN, hidden fields) by loading it into the box
on demand, and Save leaves an unchanged secret alone. The page's wiring (the /secret call itself is tested in
test_items)."""
import _env  # noqa: F401

import os
import shutil
import subprocess
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = open(os.path.join(HERE, "app", "static", "app.js"), encoding="utf-8").read()


class EditReveal(unittest.TestCase):
    def test_each_saved_secret_can_be_shown_while_editing(self):
        self.assertIn('load: d && d.password.set ? savedSecret("Password") : null', JS)
        for name in ("Number", "CVV", "PIN"):
            self.assertIn(f'savedSecret("{name}")', JS)
        self.assertIn("savedSecret(f.saved)", JS)              # hidden custom fields, by their saved name
        self.assertIn("items/${d.id}/secret?field=", JS)        # the same on-demand call as the item's own 👁

    def test_save_leaves_an_unchanged_secret_alone(self):
        self.assertIn("(pw.input.value && !pw.unchanged()) || creating", JS)
        for box in ("numberR", "cvvR", "pinR"):
            self.assertIn(f"!{box}.unchanged()", JS)

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_reveal_loads_once_then_toggles(self):
        start = JS.index("function revealInput(")
        end = JS.index("\n}\n", start) + 3
        harness = """
const made = [];
const h = (tag, attrs, ...kids) => { const el = { tag, type: (attrs || {}).type, value: "", disabled: false, attrs: {},
  kids, listeners: {}, setAttribute(k, v) { this.attrs[k] = v; }, addEventListener(t, f) { (this.listeners[t] ||= []).push(f); },
  dispatchEvent(e) { (this.listeners[e.type] || []).forEach((f) => f(e)); } }; made.push(el); return el; };
class Event { constructor(type) { this.type = type; } }
const fail = (e) => { throw e; };
""" + JS[start:end] + """
(async () => {
  const input = h("input", { type: "password" }); let calls = 0;
  const r = revealInput(input, async () => { calls++; return "s3cret"; }, "password");
  const eye = r.el.kids[1];
  const click = async () => { for (const f of eye.listeners.click) await f(); };
  if (r.unchanged()) throw new Error("unchanged before loading");
  await click();
  if (input.value !== "s3cret" || input.type !== "text" || calls !== 1) throw new Error("not loaded and shown");
  if (!r.unchanged()) throw new Error("should be unchanged");
  await click();
  if (input.type !== "password" || calls !== 1) throw new Error("second click should only hide");
  input.value = "s3cret!";
  if (r.unchanged()) throw new Error("an edit is a change");
  // a box someone typed into first never loads the saved value
  const typed = h("input", { type: "password" }); typed.value = "new"; let c2 = 0;
  const r2 = revealInput(typed, async () => { c2++; return "old"; });
  for (const f of r2.el.kids[1].listeners.click) await f();
  if (typed.value !== "new" || c2 !== 0 || typed.type !== "text") throw new Error("typed value replaced");
  console.log("ok");
})().catch((e) => { console.error(e.message); process.exit(1); });
"""
        r = subprocess.run(["node", "-e", harness], capture_output=True, text=True, timeout=60)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "ok"), r.stderr)


if __name__ == "__main__":
    unittest.main()
