/* Admin → Users: one person's data (SPEC.md section 20a). Puts the shared "⋯ Data" button (common/person-data.js)
 * on each person's card and "Import a person's data…" above the list. After an import or delete the list is
 * fetched again and swapped in, so the counts and names are current without leaving the dialog's result. */
(function () {
  "use strict";
  if (!window.PeopleData) return;

  function people(box) {
    try { return JSON.parse(box.getAttribute("data-people") || "[]"); } catch (e) { return []; }
  }

  function mount(box) {
    const cfg = { people: people(box), onDone: refresh };
    const slot = box.querySelector("#u-data-import");
    if (slot) slot.replaceChildren(PeopleData.importButton(cfg));
    box.querySelectorAll(".u-data[data-person-id]").forEach((el) => {
      const person = { id: el.getAttribute("data-person-id"), name: el.getAttribute("data-person-name") };
      el.replaceChildren(PeopleData.menu(person, cfg));
    });
  }

  async function refresh() {
    try {
      const res = await fetch(window.location.href, { credentials: "same-origin" });
      if (!res.ok) return;
      const doc = new DOMParser().parseFromString(await res.text(), "text/html");
      const fresh = doc.getElementById("u-people");
      const box = document.getElementById("u-people");
      if (fresh && box) {
        box.replaceWith(fresh);
        mount(fresh);
      }
    } catch (e) { /* the page still works; a reload shows the change */ }
  }

  const box = document.getElementById("u-people");
  if (box) mount(box);
})();
