/**
 * comments.js — Interactive comment behaviours:
 *   - Like / unlike a comment (POST/DELETE /api/comments/<id>/vote)
 *   - Follow / unfollow the post discussion thread (POST/DELETE /api/posts/<slug>/follow)
 *   - Report modal: open, submit, close
 *   - Attach-file UI shell (client-side validation only — no upload in this pass)
 */
(function () {
  "use strict";

  // ── Auth header helper (JWT in localStorage) ────────────────────────────
  function authHeaders() {
    const token = localStorage.getItem("access_token");
    const h = { "Content-Type": "application/json" };
    if (token) h["Authorization"] = "Bearer " + token;
    return h;
  }

  // ── Comment likes ────────────────────────────────────────────────────────

  document.addEventListener("click", async function (e) {
    const btn = e.target.closest(".comment-like-btn");
    if (!btn) return;
    e.preventDefault();

    const commentId = btn.dataset.commentId;
    const voted     = btn.dataset.voted === "true";
    const method    = voted ? "DELETE" : "POST";

    btn.disabled = true;
    try {
      const resp = await fetch("/api/comments/" + commentId + "/vote", {
        method: method,
        headers: authHeaders(),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(function () { return {}; });
        if (resp.status === 401) { window.location.href = "/auth/login"; return; }
        console.warn("Vote failed:", err.error || resp.status);
        return;
      }
      const data = await resp.json().catch(function () { return {}; });

      // Toggle state
      const nowVoted = !voted;
      btn.dataset.voted = nowVoted ? "true" : "false";
      btn.classList.toggle("is-voted", nowVoted);
      btn.setAttribute("aria-pressed", nowVoted ? "true" : "false");

      // Update count label
      const countEl = btn.querySelector(".like-count");
      if (countEl && typeof data.vote_count === "number") {
        countEl.textContent = data.vote_count;
      }
    } catch (err) {
      console.warn("Vote request error:", err);
    } finally {
      btn.disabled = false;
    }
  });

  // ── Follow / Unfollow thread ─────────────────────────────────────────────

  const followThreadBtn = document.getElementById("follow-thread-btn");
  if (followThreadBtn) {
    followThreadBtn.addEventListener("click", async function () {
      const slug      = this.dataset.postSlug;
      const following = this.dataset.following === "true";
      const method    = following ? "DELETE" : "POST";

      this.disabled = true;
      try {
        const resp = await fetch("/api/posts/" + slug + "/follow", {
          method: method,
          headers: authHeaders(),
        });
        if (!resp.ok) {
          if (resp.status === 401) { window.location.href = "/auth/login"; return; }
          return;
        }
        const data = await resp.json().catch(function () { return {}; });
        const nowFollowing = method === "POST";
        this.dataset.following = nowFollowing ? "true" : "false";
        this.textContent = nowFollowing ? "Unfollow thread" : "Follow thread";
        this.classList.toggle("btn-primary", !nowFollowing);
        this.classList.toggle("btn-ghost",   nowFollowing);
      } catch (err) {
        console.warn("Follow thread error:", err);
      } finally {
        this.disabled = false;
      }
    });
  }

  // ── Report modal ─────────────────────────────────────────────────────────

  // Open report modal from any [data-report-target] button
  document.addEventListener("click", function (e) {
    const trigger = e.target.closest("[data-report-target-type]");
    if (!trigger) return;
    e.preventDefault();

    const targetType = trigger.dataset.reportTargetType; // "comment" | "user"
    const targetId   = trigger.dataset.reportTargetId;

    const modal = document.getElementById("report-modal");
    if (!modal) return;

    modal.querySelector("[name=target_type]").value = targetType;
    modal.querySelector("[name=target_id]").value   = targetId;

    // Update modal title display
    const titleEl = modal.querySelector(".report-modal__title");
    if (titleEl) titleEl.textContent = "Report " + targetType;

    // Clear previous state
    modal.querySelectorAll("[name=reason]").forEach(function (r) { r.checked = false; });
    const noteEl = modal.querySelector("[name=note]");
    if (noteEl) noteEl.value = "";
    modal.querySelector(".report-modal__error").textContent = "";

    if (typeof modal.showModal === "function") {
      modal.showModal();
    } else {
      modal.hidden = false;
    }

    // Store trigger for focus-return
    modal._triggerEl = trigger;
  });

  // Close report modal
  document.addEventListener("click", function (e) {
    const closeBtn = e.target.closest("[data-close-modal]");
    if (!closeBtn) return;
    const modal = closeBtn.closest("dialog") || document.getElementById("report-modal");
    if (!modal) return;
    if (typeof modal.close === "function") { modal.close(); } else { modal.hidden = true; }
    if (modal._triggerEl) { modal._triggerEl.focus(); modal._triggerEl = null; }
  });

  // Submit report form
  const reportForm = document.getElementById("report-form");
  if (reportForm) {
    reportForm.addEventListener("submit", async function (e) {
      e.preventDefault();
      const modal     = document.getElementById("report-modal");
      const errorEl   = modal ? modal.querySelector(".report-modal__error") : null;
      const targetType = this.querySelector("[name=target_type]").value;
      const targetId   = this.querySelector("[name=target_id]").value;
      const reasonEl   = this.querySelector("[name=reason]:checked");
      const noteEl     = this.querySelector("[name=note]");

      if (!reasonEl) {
        if (errorEl) errorEl.textContent = "Please select a reason.";
        return;
      }

      const payload = {
        reason: reasonEl.value,
        note: noteEl ? noteEl.value.trim() : "",
      };

      const url = targetType === "user"
        ? "/api/reports/user/" + targetId
        : "/api/reports/comment/" + targetId;

      const submitBtn = this.querySelector("[type=submit]");
      if (submitBtn) submitBtn.disabled = true;

      try {
        const resp = await fetch(url, {
          method: "POST",
          headers: authHeaders(),
          body: JSON.stringify(payload),
        });

        if (resp.status === 401) { window.location.href = "/auth/login"; return; }

        if (!resp.ok) {
          const err = await resp.json().catch(function () { return {}; });
          if (errorEl) errorEl.textContent = err.error || "Report failed. Please try again.";
          return;
        }

        // Success
        if (modal) {
          if (typeof modal.close === "function") { modal.close(); } else { modal.hidden = true; }
          if (modal._triggerEl) { modal._triggerEl.focus(); modal._triggerEl = null; }
        }

        // Show brief confirmation
        const flash = document.createElement("div");
        flash.className = "flash flash-success";
        flash.style.cssText = "position:fixed;bottom:1rem;right:1rem;z-index:9999;max-width:22rem";
        flash.textContent = "Report submitted. Thank you.";
        document.body.appendChild(flash);
        setTimeout(function () { flash.remove(); }, 4000);

      } catch (err) {
        if (errorEl) errorEl.textContent = "Network error.";
        console.warn("Report submit error:", err);
      } finally {
        if (submitBtn) submitBtn.disabled = false;
      }
    });
  }

  // ── Ellipsis comment menus ───────────────────────────────────────────────

  document.addEventListener("click", function (e) {
    // Toggle open state of comment more-menus
    const moreBtn = e.target.closest(".comment-more-btn");
    if (moreBtn) {
      e.stopPropagation();
      const menu = moreBtn.nextElementSibling;
      if (!menu || !menu.classList.contains("comment-more-menu")) return;
      const isOpen = menu.hidden === false;
      // Close all other open menus
      document.querySelectorAll(".comment-more-menu").forEach(function (m) {
        m.hidden = true;
      });
      menu.hidden = isOpen;
      if (!isOpen) moreBtn.setAttribute("aria-expanded", "true");
      return;
    }

    // Close menus on outside click
    const openMenus = document.querySelectorAll(".comment-more-menu:not([hidden])");
    openMenus.forEach(function (m) { m.hidden = true; });
    document.querySelectorAll(".comment-more-btn").forEach(function (b) {
      b.setAttribute("aria-expanded", "false");
    });
  });

  // Copy permalink
  document.addEventListener("click", async function (e) {
    const copyBtn = e.target.closest("[data-copy-permalink]");
    if (!copyBtn) return;
    e.preventDefault();
    const url = copyBtn.dataset.copyPermalink;
    try {
      await navigator.clipboard.writeText(url);
      const orig = copyBtn.textContent;
      copyBtn.textContent = "Copied!";
      setTimeout(function () { copyBtn.textContent = orig; }, 1500);
    } catch (_) {
      // Fallback: select and prompt user to copy
      const ta = document.createElement("textarea");
      ta.value = url;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      ta.remove();
    }
  });

  // ── Attach-file UI shell ─────────────────────────────────────────────────

  const attachBtn  = document.getElementById("attach-file-btn");
  const fileInput  = document.getElementById("comment-attachment");
  const attachList = document.getElementById("attachment-list");

  const ALLOWED_TYPES = new Set([
    "image/jpeg", "image/png", "image/gif", "image/webp",
    "application/pdf",
    "text/plain",
    "application/json",
    "text/markdown",
  ]);
  const ALLOWED_EXTS = new Set([".jpg",".jpeg",".png",".gif",".webp",".pdf",".txt",".log",".json",".yaml",".yml",".toml",".md"]);
  const MAX_BYTES = 2 * 1024 * 1024; // 2 MiB

  if (attachBtn && fileInput) {
    attachBtn.addEventListener("click", function () { fileInput.click(); });

    fileInput.addEventListener("change", function () {
      if (!this.files || !this.files.length) return;
      const file = this.files[0];

      // Client-side validation
      const ext = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
      if (!ALLOWED_EXTS.has(ext)) {
        alert("File type not allowed. Allowed: " + Array.from(ALLOWED_EXTS).join(", "));
        this.value = "";
        return;
      }
      if (file.size > MAX_BYTES) {
        alert("File is too large (max 2 MB).");
        this.value = "";
        return;
      }

      // Show pill
      if (attachList) {
        const pill = document.createElement("span");
        pill.className = "attach-pill";
        pill.innerHTML =
          '<span class="attach-pill__name">' + escHtml(file.name) + '</span>' +
          '<button type="button" class="attach-pill__remove" aria-label="Remove attachment">&times;</button>';
        pill.querySelector(".attach-pill__remove").addEventListener("click", function () {
          fileInput.value = "";
          pill.remove();
        });
        attachList.innerHTML = "";
        attachList.appendChild(pill);
      }
    });
  }

  function escHtml(s) {
    return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
  }
})();
