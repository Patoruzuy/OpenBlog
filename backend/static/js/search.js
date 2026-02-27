/**
 * search.js — Header search icon toggle + live suggestion dropdown.
 *
 * Phase A: expand/collapse search input when the icon button is pressed.
 * Phase B: fetch suggestions from /search/suggest and render grouped dropdown.
 *
 * Keyboard shortcuts:
 *   /      → focus search input (from anywhere on the page)
 *   Escape → close/collapse search
 *   ↑ / ↓  → navigate suggestion items
 *   Enter  → activate focused suggestion
 */
(function () {
  "use strict";

  const toggleBtn  = document.getElementById("search-toggle");
  const expandWrap = document.getElementById("search-expand");
  const inputEl    = document.getElementById("nav-search-input");
  const suggestBox = document.getElementById("nav-suggest-list");

  if (!toggleBtn || !expandWrap || !inputEl || !suggestBox) return;

  let debounceTimer = null;

  // ── Phase A: toggle visibility ──────────────────────────────────────────

  function openSearch() {
    expandWrap.hidden = false;
    toggleBtn.setAttribute("aria-expanded", "true");
    setTimeout(function () { inputEl.focus(); }, 50);
  }

  function closeSearch() {
    expandWrap.hidden = true;
    toggleBtn.setAttribute("aria-expanded", "false");
    clearSuggest();
    inputEl.value = "";
    toggleBtn.focus();
  }

  toggleBtn.addEventListener("click", function () {
    const isOpen = toggleBtn.getAttribute("aria-expanded") === "true";
    if (isOpen) { closeSearch(); } else { openSearch(); }
  });

  // / shortcut: focus search from anywhere except inputs/textareas
  document.addEventListener("keydown", function (e) {
    if (e.key === "/" && !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) {
      e.preventDefault();
      openSearch();
    }
    if (e.key === "Escape" && toggleBtn.getAttribute("aria-expanded") === "true") {
      closeSearch();
    }
  });

  // Click outside collapses
  document.addEventListener("click", function (e) {
    if (expandWrap.hidden) return;
    if (!expandWrap.contains(e.target) && e.target !== toggleBtn) {
      closeSearch();
    }
  });

  // ── Phase B: suggestion dropdown ────────────────────────────────────────

  function clearSuggest() {
    suggestBox.hidden = true;
    suggestBox.innerHTML = "";
    inputEl.setAttribute("aria-expanded", "false");
  }

  function escHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function renderSuggestions(data) {
    suggestBox.innerHTML = "";
    const hasPosts  = data.posts  && data.posts.length;
    const hasTags   = data.tags   && data.tags.length;
    if (!hasPosts && !hasTags) { clearSuggest(); return; }

    function makeGroupHeader(label) {
      const el = document.createElement("div");
      el.className = "suggest-group-hdr";
      el.textContent = label;
      return el;
    }

    if (hasPosts) {
      suggestBox.appendChild(makeGroupHeader(window._i18n && window._i18n.posts || "Posts"));
      data.posts.forEach(function (p) {
        const a = document.createElement("a");
        a.className = "suggest-item suggest-item--post";
        a.href = "/posts/" + p.slug;
        a.setAttribute("role", "option");
        a.innerHTML =
          '<span class="suggest-title">' + escHtml(p.title) + "</span>" +
          (p.excerpt ? '<span class="suggest-excerpt">' + escHtml(p.excerpt) + "</span>" : "");
        suggestBox.appendChild(a);
      });
    }

    if (hasTags) {
      suggestBox.appendChild(makeGroupHeader(window._i18n && window._i18n.topics || "Topics"));
      data.tags.forEach(function (t) {
        const a = document.createElement("a");
        a.className = "suggest-item suggest-item--tag";
        a.href = "/tags/" + t.slug;
        a.setAttribute("role", "option");
        a.innerHTML = '<span class="suggest-tag-name">#' + escHtml(t.name) + "</span>";
        suggestBox.appendChild(a);
      });
    }

    // People stub — shown when /search/suggest is extended with user results
    if (data.users && data.users.length) {
      suggestBox.appendChild(makeGroupHeader(window._i18n && window._i18n.people || "People"));
      data.users.forEach(function (u) {
        const a = document.createElement("a");
        a.className = "suggest-item suggest-item--user";
        a.href = "/users/" + u.username;
        a.setAttribute("role", "option");
        a.innerHTML = '<span class="suggest-title">@' + escHtml(u.username) + "</span>";
        suggestBox.appendChild(a);
      });
    }

    const all = document.createElement("a");
    all.className = "suggest-all";
    all.href = "/search?q=" + encodeURIComponent(inputEl.value.trim());
    all.textContent = (window._i18n && window._i18n.seeAll || "See all results") + " \u2192";
    suggestBox.appendChild(all);

    suggestBox.hidden = false;
    inputEl.setAttribute("aria-expanded", "true");
  }

  inputEl.addEventListener("input", function () {
    clearTimeout(debounceTimer);
    const q = inputEl.value.trim();
    if (q.length < 2) { clearSuggest(); return; }
    debounceTimer = setTimeout(function () {
      fetch("/search/suggest?q=" + encodeURIComponent(q))
        .then(function (r) { return r.json(); })
        .then(renderSuggestions)
        .catch(clearSuggest);
    }, 220);
  });

  // ── ↑/↓ keyboard navigation in dropdown ────────────────────────────────

  inputEl.addEventListener("keydown", function (e) {
    if (suggestBox.hidden) return;
    const items = Array.from(suggestBox.querySelectorAll(".suggest-item, .suggest-all"));
    if (!items.length) return;
    const focused = document.activeElement;
    const idx = items.indexOf(focused);

    if (e.key === "ArrowDown") {
      e.preventDefault();
      const next = idx < items.length - 1 ? items[idx + 1] : items[0];
      next.focus();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      const prev = idx > 0 ? items[idx - 1] : inputEl;
      prev.focus();
    } else if (e.key === "Escape") {
      closeSearch();
    }
  });

  suggestBox.addEventListener("keydown", function (e) {
    const items = Array.from(suggestBox.querySelectorAll(".suggest-item, .suggest-all"));
    const idx = items.indexOf(document.activeElement);
    if (e.key === "ArrowDown") {
      e.preventDefault();
      const next = idx < items.length - 1 ? items[idx + 1] : items[0];
      next.focus();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      if (idx <= 0) { inputEl.focus(); } else { items[idx - 1].focus(); }
    } else if (e.key === "Escape") {
      closeSearch();
    }
  });
})();
