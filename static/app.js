"use strict";
const $ = (id) => document.getElementById(id),
  money = (units) => `$${(units / 10000).toFixed(3).replace(/0$/, "")}`;
let csrf = "",
  sessionReady = null,
  config = null,
  documentQuote = null,
  uploadId = null,
  confirmId = null,
  timer = null,
  reviewJob = null,
  reviewPages = [],
  retryQuote = null,
  retryId = null,
  currentPage = null;
function status(message, error = false) {
  $("status").textContent = message;
  $("status").classList.toggle("error", error);
}
async function api(path, options = {}) {
  if (path !== "/api/session" && !csrf) {
    if (sessionReady) await sessionReady;
    if (!csrf)
      throw Error("The session is still connecting. Refresh and try again.");
  }
  const r = await fetch(path, {
    credentials: "same-origin",
    ...options,
    headers: { "X-CSRF-Token": csrf, ...options.headers },
  });
  let v;
  try {
    v = await r.json();
  } catch {
    throw Error("The service is temporarily unavailable. Try again.");
  }
  if (!r.ok)
    throw Error(
      typeof v.detail === "string"
        ? v.detail
        : "This request could not be completed.",
    );
  return v;
}
const post = (path, body) =>
  api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
async function busy(button, action) {
  button.disabled = true;
  try {
    await action();
  } catch (e) {
    status(e.message, true);
  } finally {
    button.disabled = false;
  }
}
async function refresh() {
  config = await api("/api/session");
  csrf = config.csrf;
  $("inspect-button").disabled = false;
  $("balance").textContent =
    `${money(config.account.available)} available · ${money(config.account.reserved)} reserved`;
  $("billing-note").textContent = config.billing_enabled
    ? "Processing packs are available. Purchases are one-time; no recurring access fee."
    : "Paid packs open after seller activation. Inspection and the bounded sample are available.";
  for (const id of ["pack5", "pack10"])
    $(id).disabled = !config.billing_enabled;
  $("access-form").hidden = config.billing_provider === "paddle";
  const lang = $("language"),
    selected = lang.value;
  lang.replaceChildren();
  for (const [value, label] of Object.entries(config.languages)) {
    const o = document.createElement("option");
    o.value = value;
    o.textContent = label;
    lang.append(o);
  }
  lang.value = selected || "latin";
  await jobs();
  await retained();
}
function invalidate() {
  uploadRequests.clear();
  documentQuote = null;
  uploadId = null;
  confirmId = null;
  $("quote").hidden = true;
}
$("pdf-file").addEventListener("change", () => {
  invalidate();
  const f = $("pdf-file").files[0];
  $("file-label").textContent = f
    ? `${f.name} · ${(f.size / 1048576).toFixed(2)} MiB`
    : "Up to 64 MiB and 1,000 PDF pages";
});
for (const id of ["page-range", "mode", "language"])
  $(id).addEventListener("change", invalidate);
const uploadRequests = new Map();
$("upload-form").addEventListener("submit", (e) => {
  e.preventDefault();
  busy($("inspect-button"), async () => {
    const files = Array.from($("pdf-file").files);
    if (!files.length) return;
    if (files.length > 5) throw Error("Choose at most 5 PDFs at a time.");
    const failures = [];
    let prepared = 0;
    for (const f of files) {
      try {
        if (f.size > (config?.max_bytes || 67108864))
          throw Error("PDF exceeds 64 MiB.");
        const key = `${f.name}:${f.size}:${f.lastModified}:${$("page-range").value}:${$("mode").value}:${$("language").value}`;
        const request = uploadRequests.get(key) || crypto.randomUUID();
        uploadRequests.set(key, request);
        const form = new FormData();
        form.append("file", f);
        form.append("request_id", request);
        form.append("pages", $("page-range").value);
        form.append("mode", $("mode").value);
        form.append("language", $("language").value);
        status(`Inspecting ${f.name}. Extraction has not started.`);
        documentQuote = await api("/api/documents", {
          method: "POST",
          body: form,
        });
        prepared++;
        $("quote-details").textContent =
          `${documentQuote.filename}: ${documentQuote.inspection.total_pages} source pages, ${documentQuote.selected.length} selected. Maximum service charge ${money(documentQuote.quote.units)}.`;
        $("inspection-note").textContent =
          $("mode").value === "text"
            ? documentQuote.inspection.text_only_note
            : `Text exists on ${documentQuote.inspection.text_layer_sample.filter((p) => p.text_characters > 0).length} sampled pages. Text-only costs less but excludes OCR, images and table/layout reconstruction.`;
        $("quote").hidden = false;
      } catch (error) {
        failures.push(`${f.name}: ${error.message}`);
      }
    }
    await retained();
    status(
      `${prepared} PDF(s) inspected. Confirm each separately using the retained PDF buttons. ${failures.join(" ")}`,
      !!failures.length,
    );
  });
});
async function confirm(trial) {
  const button = $(trial ? "trial-button" : "confirm-button");
  await busy(button, async () => {
    if (!documentQuote) throw Error("Inspect a PDF first.");
    confirmId = confirmId || crypto.randomUUID();
    const r = await post("/api/jobs", {
      document: documentQuote.id,
      request_id: confirmId,
      trial,
      expected_units: documentQuote.quote.units,
    });
    status(
      r.submission_uncertain
        ? r.detail
        : "Conversion submitted. Completed sections will appear below.",
    );
    confirmId = null;
    await refresh();
  });
}
$("trial-button").addEventListener("click", () => confirm(true));
$("confirm-button").addEventListener("click", () => confirm(false));
async function jobs() {
  clearTimeout(timer);
  const rows = await api("/api/jobs");
  const list = $("jobs");
  list.replaceChildren();
  let active = false;
  if (!rows.length) {
    const p = document.createElement("p");
    p.className = "note";
    p.textContent = "No conversions yet. Inspect a PDF to begin.";
    list.append(p);
  }
  for (const job of rows) {
    const card = document.createElement("article");
    card.className = "job";
    const title = document.createElement("strong");
    title.textContent = job.filename;
    const detail = document.createElement("p");
    detail.textContent = `${job.status} · ${job.completed_pages.length} completed pages · ${money(job.charged)} charged`;
    card.append(title, detail);
    const errors = job.parts.filter((p) => p.status === "error");
    if (errors.length) {
      const p = document.createElement("p");
      p.className = "error";
      p.textContent = `${errors.length} section(s) need attention. Reserved allowance released; completed pages remain available.`;
      card.append(p);
    }
    const actions = document.createElement("div");
    actions.className = "actions";
    const button = (label, action) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "secondary";
      b.textContent = label;
      b.addEventListener("click", () => busy(b, action));
      actions.append(b);
    };
    if (job.completed_pages.length) {
      button("Review / correct", () => openReview(job));
      const select = document.createElement("select");
      select.setAttribute("aria-label", "Export layout");
      for (const [v, l] of [
        ["single", "One Markdown file"],
        ["pages", "One file per PDF page"],
        ["sections", "Files in page groups"],
        ["bookmarks", "PDF bookmark sections"],
      ]) {
        const o = document.createElement("option");
        o.value = v;
        o.textContent = l;
        select.append(o);
      }
      actions.append(select);
      const include = document.createElement("input");
      include.type = "checkbox";
      include.checked = true;
      const label = document.createElement("label");
      label.className = "include-source";
      label.append(include, document.createTextNode(" Include source PDF"));
      actions.append(label);
      const a = document.createElement("a");
      a.className = "secondary";
      a.textContent = "Download ZIP ↓";
      function href() {
        a.href = `/api/jobs/${job.id}/download?layout=${select.value}&include_pdf=${include.checked}`;
      }
      href();
      select.addEventListener("change", href);
      include.addEventListener("change", href);
      actions.append(a);
    }
    if (["queued", "running"].includes(job.status)) {
      active = true;
      button("Cancel queued work", async () => {
        const r = await post(`/api/jobs/${job.id}/cancel`, {});
        status(r.notice);
        await refresh();
      });
    } else if (!job.trial && errors.length) {
      button("Retry failed pages", () => openReview(job, true));
    }
    button("Delete files & edits", async () => {
      if (
        !window.confirm(
          "Delete this conversion and saved corrections? Download any files you want to keep first.",
        )
      )
        return;
      const r = await api(`/api/jobs/${job.id}`, { method: "DELETE" });
      status(r.notice);
      if (reviewJob?.id === job.id) $("review").hidden = true;
      await refresh();
    });
    card.append(actions);
    list.append(card);
  }
  if (active)
    timer = setTimeout(
      () => refresh().catch((e) => status(e.message, true)),
      8000,
    );
}
function canLeaveReview() {
  const page = reviewPages.find((p) => p.number === currentPage);
  return (
    $("review").hidden ||
    !page ||
    $("editor").value === page.markdown ||
    window.confirm("Discard the unsaved editor draft?")
  );
}
async function openReview(job, retry = false) {
  if (!canLeaveReview()) return;
  reviewJob = job;
  retryQuote = null;
  retryId = null;
  $("retry-confirm").hidden = true;
  $("review").hidden = false;
  $("review-title").textContent = `Review · ${job.filename}`;
  reviewPages = (await api(`/api/jobs/${job.id}/pages`)).pages;
  $("review-page").replaceChildren();
  for (const p of reviewPages) {
    const o = document.createElement("option");
    o.value = p.number;
    o.textContent = `PDF page ${p.number}${p.edited ? " · corrected" : ""}${p.issues.length ? " · check" : ""}`;
    $("review-page").append(o);
  }
  showPage();
  $("retry-mode").value = job.mode;
  $("retry-pages").value = retry ? "" : String(reviewPages[0]?.number || "");
  $("review").scrollIntoView({ behavior: "instant" });
}
function showPage() {
  const page = reviewPages.find(
    (p) => p.number === Number($("review-page").value),
  );
  currentPage = page?.number || null;
  $("editor").value = page?.markdown || "";
  $("editor").disabled = !page;
  $("save-edit").disabled = !page;
  $("review-issues").textContent = page
    ? page.issues.map((i) => i.detail).join(" ") ||
      "No listed symptoms. This does not certify extraction correctness."
    : "No completed page to review. You can retry failed pages below.";
  $("source-link").href = reviewJob
    ? `/api/jobs/${reviewJob.id}/source#page=${page?.number || 1}`
    : "#";
  $("history").replaceChildren();
}
$("review-page").addEventListener("change", () => {
  const previous = reviewPages.find((p) => p.number === currentPage);
  if (
    previous &&
    $("editor").value !== previous.markdown &&
    !window.confirm("Discard the unsaved editor draft?")
  ) {
    $("review-page").value = String(currentPage);
    return;
  }
  showPage();
});
$("save-edit").addEventListener("click", () =>
  busy($("save-edit"), async () => {
    const p = reviewPages.find(
      (p) => p.number === Number($("review-page").value),
    );
    const r = await post(`/api/jobs/${reviewJob.id}/pages/${p.number}`, {
      text: $("editor").value,
      version: p.version,
    });
    p.version = r.version;
    p.markdown = $("editor").value;
    p.edited = true;
    status("Correction saved. Exports include it.");
  }),
);
$("reset-edit").addEventListener("click", () => {
  const p = reviewPages.find(
    (p) => p.number === Number($("review-page").value),
  );
  if (p) $("editor").value = p.extracted_markdown;
});
$("history-button").addEventListener("click", () =>
  busy($("history-button"), async () => {
    if (!reviewJob) return;
    const rows = await api(
      `/api/jobs/${reviewJob.id}/pages/${$("review-page").value}/history`,
    );
    $("history").replaceChildren();
    for (const row of rows) {
      const details = document.createElement("details"),
        summary = document.createElement("summary"),
        pre = document.createElement("pre");
      summary.textContent = `Saved version ${row.version}`;
      pre.textContent = row.text;
      details.append(summary, pre);
      $("history").append(details);
    }
    if (!rows.length) $("history").textContent = "No saved corrections yet.";
  }),
);
$("retry-quote").addEventListener("click", () =>
  busy($("retry-quote"), async () => {
    retryId = null;
    retryQuote = await post(`/api/jobs/${reviewJob.id}/retry-quote`, {
      pages: $("retry-pages").value,
      mode: $("retry-mode").value,
    });
    $("retry-details").textContent =
      `${retryQuote.pages.length} pages · maximum charge ${money(retryQuote.quote.units)}. Saved corrections will be preserved.`;
    $("retry-confirm").hidden = false;
  }),
);
$("retry-mode").addEventListener("change", () => {
  retryQuote = null;
  $("retry-confirm").hidden = true;
});
$("retry-pages").addEventListener("input", () => {
  retryQuote = null;
  $("retry-confirm").hidden = true;
});
$("retry-confirm").addEventListener("click", () =>
  busy($("retry-confirm"), async () => {
    if (!retryQuote) return;
    retryId = retryId || crypto.randomUUID();
    const r = await post(`/api/jobs/${reviewJob.id}/retry`, {
      request_id: retryId,
      pages: retryQuote.pages,
      mode: $("retry-mode").value,
      expected_units: retryQuote.quote.units,
    });
    status(
      r.submission_uncertain
        ? r.detail
        : "Selected pages submitted for reprocessing.",
    );
    $("retry-confirm").hidden = true;
    await refresh();
  }),
);
$("close-review").addEventListener("click", () => {
  if (canLeaveReview()) $("review").hidden = true;
});
for (const n of [5, 10])
  $(`pack${n}`).addEventListener("click", () =>
    busy($(`pack${n}`), async () => {
      const r = await post("/api/checkout", { pack: n, terms_accepted: $("purchase-consent").checked });
      location.assign(r.url);
    }),
  );
$("access-form").addEventListener("submit", (e) => {
  e.preventDefault();
  busy(e.currentTarget.querySelector("button"), async () => {
    await post("/api/access", { license_key: $("license-key").value });
    $("license-key").value = "";
    invalidate();
    await refresh();
    status("Purchased credits restored.");
  });
});
$("recovery-button").addEventListener("click", () =>
  busy($("recovery-button"), async () => {
    const r = await post("/api/recovery", {});
    $("recovery-key").value = r.recovery_key;
    $("recovery-box").hidden = false;
    await refresh();
    status(r.notice);
    $("recovery-key").select();
  }),
);
$("hide-key").addEventListener("click", () => {
  $("recovery-key").value = "";
  $("recovery-box").hidden = true;
});
$("restore-form").addEventListener("submit", (e) => {
  e.preventDefault();
  busy(e.currentTarget.querySelector("button"), async () => {
    await post("/api/restore", { recovery_key: $("restore-key").value });
    $("restore-key").value = "";
    invalidate();
    $("review").hidden = true;
    await refresh();
    status("Workspace restored.");
  });
});
$("refresh-button").addEventListener("click", () =>
  busy($("refresh-button"), refresh),
);
window.addEventListener("beforeunload", (e) => {
  const p = reviewPages.find(
    (p) => p.number === Number($("review-page").value),
  );
  if (!$("review").hidden && p && $("editor").value !== p.markdown) {
    e.preventDefault();
    e.returnValue = "";
  }
});
sessionReady = refresh();
sessionReady.catch((e) => status(e.message, true));

$("discard-button").addEventListener("click", () =>
  busy($("discard-button"), async () => {
    if (documentQuote) {
      await api("/api/documents/" + documentQuote.id, { method: "DELETE" });
      invalidate();
      status("Inspected source deleted.");
    }
  }),
);

async function retained() {
  const rows = await api("/api/documents");
  $("retained").replaceChildren();
  for (const row of rows) {
    const button = document.createElement("button");
    button.className = "text-button";
    button.textContent = `Resume ${row.filename} · ${row.selected.length} selected pages`;
    button.addEventListener("click", () => {
      documentQuote = row;
      confirmId = null;
      $("mode").value = row.mode;
      $("language").value = row.lang;
      $("page-range").value =
        row.selected.length === row.inspection.total_pages
          ? "all"
          : row.selected.join(",");
      $("quote-details").textContent =
        `${row.filename}: ${row.inspection.total_pages} source pages, ${row.selected.length} selected. Maximum charge ${money(row.quote.units)}.`;
      $("inspection-note").textContent =
        "Retained inspected PDF. No re-upload is needed; source expires after 48 hours.";
      $("quote").hidden = false;
    });
    $("retained").append(button);
  }
}
