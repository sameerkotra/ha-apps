/* Family Tree — exported website. Runs from file:// or any static host; no
   network requests. All names are inserted as text, never as HTML. */
(function () {
  "use strict";
  var root = document.documentElement;
  try {
    var saved = localStorage.getItem("ftSiteTheme");
    if (saved) root.setAttribute("data-theme", saved);
  } catch (e) { /* storage may be unavailable */ }

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  }
  function base() { return document.body.getAttribute("data-base") || ""; }
  function norm(s) { return (s || "").toLowerCase().normalize("NFKD").replace(/[̀-ͯ]/g, ""); }

  function avatar(p) {
    var a = el("span", "av " + (p.gender || "unknown"));
    if (p.photo) {
      var img = document.createElement("img");
      img.src = base() + "media/" + p.photo;
      img.alt = "";
      a.appendChild(img);
    } else {
      a.textContent = ((p.given || p.name || "?").charAt(0) + (p.surname || "").charAt(0)).toUpperCase();
    }
    return a;
  }

  function rel(p) {
    var r = p.relationship;
    if (!r || r === "you" || r === "not related" || !window.TREE.relativeTo) return "";
    return window.TREE.relativeTo + "'s " + r;
  }

  function personLink(p) {
    var a = el("a", "row");
    a.href = base() + p.page;
    a.appendChild(avatar(p));
    var g = el("span", "grow");
    g.appendChild(el("div", "nm", p.name));
    var sub = [p.years, rel(p)].filter(Boolean).join(" · ");
    if (sub) g.appendChild(el("div", "sub", sub));
    a.appendChild(g);
    return a;
  }

  function wireTheme() {
    var sel = document.getElementById("themeSel");
    if (!sel) return;
    sel.value = root.getAttribute("data-theme") || "auto";
    sel.addEventListener("change", function () {
      root.setAttribute("data-theme", sel.value);
      try { localStorage.setItem("ftSiteTheme", sel.value); } catch (e) { /* ignore */ }
    });
  }

  // home page: search everyone in data.js
  function wireHomeSearch() {
    var box = document.getElementById("homeSearch");
    var out = document.getElementById("homeResults");
    if (!box || !out || !window.TREE) return;
    box.addEventListener("input", function () {
      var q = norm(box.value.trim());
      while (out.firstChild) out.removeChild(out.firstChild);
      if (!q) return;
      var parts = q.split(/\s+/);
      var hits = window.TREE.people.filter(function (p) {
        if (!p.page) return false;
        var hay = norm([p.name, p.alt].join(" "));
        return parts.every(function (w) { return hay.indexOf(w) !== -1; });
      }).slice(0, 30);
      if (!hits.length) out.appendChild(el("div", "hint", "Nobody found."));
      hits.forEach(function (p) { out.appendChild(personLink(p)); });
    });
  }

  // people list: filter rows in place; ?s=Surname preselects a surname
  function wirePeopleList() {
    var box = document.getElementById("listSearch");
    if (!box) return;
    var rows = Array.prototype.slice.call(document.querySelectorAll(".plist a.row"));
    var params = new URLSearchParams(location.search);
    if (params.get("s")) box.value = params.get("s");
    function apply() {
      var parts = norm(box.value.trim()).split(/\s+/).filter(Boolean);
      rows.forEach(function (r) {
        var hay = norm(r.getAttribute("data-search"));
        r.classList.toggle("hidden", !parts.every(function (w) { return hay.indexOf(w) !== -1; }));
      });
    }
    box.addEventListener("input", apply);
    apply();
  }

  // tree page: the app's own chart (tree.js), everyone at once
  function wireTree() {
    var canvas = document.getElementById("treeCanvas");
    if (!canvas || !window.FamilyChart || !window.TREE) return;
    var byId = {};
    window.TREE.people.forEach(function (p) { byId[p.id] = p; });
    var params = new URLSearchParams(location.search);
    var data = { people: window.TREE.people, families: window.TREE.families, focus: params.get("p") || window.TREE.focus, meId: null };
    var chart = window.FamilyChart.render(canvas, data, {
      whole: true,
      fitFirst: window.TREE.people.length <= 40,
      photoUrl: function (p) { return "media/" + p.photo; },
      relLabel: function (p) { var r = rel(p); return r ? " — " + r : ""; },
      cardClass: function (p) { return p.id === data.focus ? "hl" : ""; },
      onOpen: function (id) { var p = byId[id]; if (p && p.page) location.href = p.page; },
      onRecenter: function (id) { chart.centerOnId(id, 1); },
    });
    var find = document.getElementById("treeFind");
    if (find) {
      find.addEventListener("change", function () {
        var q = norm(find.value.trim());
        var hit = window.TREE.people.find(function (p) { return p.page && norm(p.name).indexOf(q) !== -1; });
        if (hit) chart.centerOnId(hit.id, 1);
      });
    }
    var z = function (id, fn) { var b = document.getElementById(id); if (b) b.addEventListener("click", fn); };
    z("zIn", function () { chart.zoomIn(); });
    z("zOut", function () { chart.zoomOut(); });
    z("zFit", function () { chart.fit(); });
  }

  document.addEventListener("DOMContentLoaded", function () {
    wireTheme();
    wireHomeSearch();
    wirePeopleList();
    wireTree();
  });
})();
