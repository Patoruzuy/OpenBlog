/**
 * editor.js — Lightweight markdown toolbar for the post composer and comment
 * form.  No external dependencies — pure DOM manipulation on <textarea>.
 *
 * Usage:
 *   <div data-md-editor>
 *     <div data-md-toolbar></div>   ← toolbar buttons injected here
 *     <textarea data-md-input></textarea>   ← the target textarea
 *     <div data-md-preview hidden></div>    ← preview pane (optional)
 *   </div>
 *
 * The toolbar buttons wrap/insert markdown syntax around the current
 * selection or at the cursor position.
 */
(function () {
  "use strict";

  // ── Toolbar button definitions ──────────────────────────────────────────

  const TOOLBAR_ACTIONS = [
    { key: "bold",        label: "B",    title: "Bold",          wrap: ["**", "**"],   placeholder: "bold text"   },
    { key: "italic",      label: "I",    title: "Italic",        wrap: ["_", "_"],     placeholder: "italic text" },
    { key: "code",        label: "</>",  title: "Inline code",   wrap: ["`", "`"],     placeholder: "code"        },
    { key: "codeblock",   label: "```",  title: "Code block",    block: true, prefix: "```\n", suffix: "\n```", placeholder: "code block" },
    { key: "sep" },
    { key: "h2",          label: "H2",   title: "Heading 2",     linePrefix: "## "     },
    { key: "h3",          label: "H3",   title: "Heading 3",     linePrefix: "### "    },
    { key: "sep" },
    { key: "ul",          label: "• —",  title: "Bullet list",   linePrefix: "- "      },
    { key: "ol",          label: "1.",   title: "Numbered list", linePrefix: "1. "     },
    { key: "blockquote",  label: ">",    title: "Blockquote",    linePrefix: "> "      },
    { key: "hr",          label: "—",    title: "Horizontal rule", insertLine: "---"   },
    { key: "sep" },
    { key: "link",        label: "🔗",   title: "Link",          linkAction: true      },
  ];

  // ── Core insertion helper ───────────────────────────────────────────────

  function insertAt(ta, text) {
    const start = ta.selectionStart;
    const end   = ta.selectionEnd;
    const before = ta.value.slice(0, start);
    const after  = ta.value.slice(end);
    ta.value = before + text + after;
    ta.selectionStart = ta.selectionEnd = start + text.length;
    ta.focus();
    ta.dispatchEvent(new Event("input", { bubbles: true }));
  }

  function wrapSelection(ta, before, after, placeholder) {
    const start = ta.selectionStart;
    const end   = ta.selectionEnd;
    const sel   = ta.value.slice(start, end) || placeholder;
    const replacement = before + sel + after;
    ta.value = ta.value.slice(0, start) + replacement + ta.value.slice(end);
    // Position cursor inside the wrap
    ta.selectionStart = start + before.length;
    ta.selectionEnd   = start + before.length + sel.length;
    ta.focus();
    ta.dispatchEvent(new Event("input", { bubbles: true }));
  }

  function prefixLines(ta, prefix, placeholder) {
    const start = ta.selectionStart;
    const end   = ta.selectionEnd;
    let selected = ta.value.slice(start, end) || placeholder;
    const prefixed = selected.split("\n").map(function (l) {
      return prefix + l;
    }).join("\n");
    ta.value = ta.value.slice(0, start) + prefixed + ta.value.slice(end);
    ta.selectionStart = start;
    ta.selectionEnd   = start + prefixed.length;
    ta.focus();
    ta.dispatchEvent(new Event("input", { bubbles: true }));
  }

  function insertLine(ta, text) {
    // Insert on a new line before/after current position
    const pos    = ta.selectionStart;
    const before = ta.value.slice(0, pos);
    const after  = ta.value.slice(pos);
    const nl     = before.length && !before.endsWith("\n") ? "\n" : "";
    const snl    = after.length  && !after.startsWith("\n") ? "\n" : "";
    const ins    = nl + text + snl;
    ta.value = before + ins + after;
    ta.selectionStart = ta.selectionEnd = pos + ins.length;
    ta.focus();
    ta.dispatchEvent(new Event("input", { bubbles: true }));
  }

  // ── Build toolbar ───────────────────────────────────────────────────────

  function buildToolbar(toolbarEl, ta) {
    TOOLBAR_ACTIONS.forEach(function (action) {
      if (action.key === "sep") {
        const sep = document.createElement("span");
        sep.className = "md-toolbar__sep";
        sep.setAttribute("aria-hidden", "true");
        toolbarEl.appendChild(sep);
        return;
      }

      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "md-toolbar__btn";
      btn.textContent = action.label;
      btn.title = action.title;
      btn.setAttribute("aria-label", action.title);

      btn.addEventListener("click", function (e) {
        e.preventDefault();

        if (action.wrap) {
          wrapSelection(ta, action.wrap[0], action.wrap[1], action.placeholder);

        } else if (action.block) {
          wrapSelection(ta, action.prefix, action.suffix, action.placeholder);

        } else if (action.linePrefix) {
          prefixLines(ta, action.linePrefix, action.placeholder || "text");

        } else if (action.insertLine) {
          insertLine(ta, action.insertLine);

        } else if (action.linkAction) {
          const sel   = ta.value.slice(ta.selectionStart, ta.selectionEnd);
          const url   = prompt("URL:", "https://");
          if (url) {
            const text = sel || prompt("Link text:", "link text") || "link text";
            const md   = "[" + text + "](" + url + ")";
            const s    = ta.selectionStart;
            ta.value   = ta.value.slice(0, s) + md + ta.value.slice(ta.selectionEnd);
            ta.selectionStart = ta.selectionEnd = s + md.length;
            ta.focus();
            ta.dispatchEvent(new Event("input", { bubbles: true }));
          }
        }
      });

      toolbarEl.appendChild(btn);
    });
  }

  // ── Write/Preview tabs ──────────────────────────────────────────────────

  function setupTabs(editorEl, ta) {
    const writeTab   = editorEl.querySelector("[data-tab=write]");
    const previewTab = editorEl.querySelector("[data-tab=preview]");
    const previewPane = editorEl.querySelector("[data-md-preview]");
    const toolbarEl  = editorEl.querySelector("[data-md-toolbar]");
    if (!writeTab || !previewTab || !previewPane) return;

    writeTab.addEventListener("click", function () {
      writeTab.classList.add("active");
      previewTab.classList.remove("active");
      writeTab.setAttribute("aria-selected", "true");
      previewTab.setAttribute("aria-selected", "false");
      ta.hidden = false;
      if (toolbarEl) toolbarEl.hidden = false;
      previewPane.hidden = true;
    });

    previewTab.addEventListener("click", function () {
      previewTab.classList.add("active");
      writeTab.classList.remove("active");
      previewTab.setAttribute("aria-selected", "true");
      writeTab.setAttribute("aria-selected", "false");
      ta.hidden = true;
      if (toolbarEl) toolbarEl.hidden = true;
      previewPane.hidden = false;

      // Fetch rendered HTML from the preview endpoint
      const markdown = ta.value;
      previewPane.innerHTML = '<p style="color:var(--text-muted)">Rendering…</p>';
      fetch("/api/posts/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ markdown: markdown }),
      })
        .then(function (r) { return r.json(); })
        .then(function (d) { previewPane.innerHTML = d.html || "<em>Nothing to preview.</em>"; })
        .catch(function () { previewPane.innerHTML = "<em>Preview unavailable.</em>"; });
    });
  }

  // ── Unsaved-changes guard ───────────────────────────────────────────────

  function setupUnsavedGuard(form) {
    if (!form) return;
    let dirty = false;
    form.addEventListener("input", function () { dirty = true; });
    form.addEventListener("submit", function () { dirty = false; });
    window.addEventListener("beforeunload", function (e) {
      if (dirty) {
        e.preventDefault();
        e.returnValue = "";
      }
    });
  }

  // ── Auto-slug from title ────────────────────────────────────────────────

  function setupSlugSync(titleInput, slugInput) {
    if (!titleInput || !slugInput) return;
    let userEditedSlug = false;
    slugInput.addEventListener("input", function () { userEditedSlug = true; });
    titleInput.addEventListener("input", function () {
      if (userEditedSlug) return;
      const raw = titleInput.value
        .toLowerCase()
        .trim()
        .replace(/[^\w\s-]/g, "")
        .replace(/[\s_]+/g, "-")
        .replace(/-{2,}/g, "-")
        .replace(/^-+|-+$/g, "");
      slugInput.value = raw || "";
    });
  }

  // ── Init all editors on page ────────────────────────────────────────────

  document.querySelectorAll("[data-md-editor]").forEach(function (editorEl) {
    const ta        = editorEl.querySelector("[data-md-input]");
    const toolbarEl = editorEl.querySelector("[data-md-toolbar]");
    if (!ta) return;

    if (toolbarEl) buildToolbar(toolbarEl, ta);
    setupTabs(editorEl, ta);
  });

  // Init unsaved-changes guard on the post composer form
  setupUnsavedGuard(document.getElementById("post-compose-form"));

  // Init slug sync
  setupSlugSync(
    document.getElementById("post-title"),
    document.getElementById("post-slug")
  );

  // Expose helpers for console debugging
  window._mdEditor = { wrapSelection, prefixLines, insertLine };
})();
