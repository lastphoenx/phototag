(function () {
  let tagCatalog = [];
  let browseFolder = "";
  let jobPollTimer = null;
  /** @type {Map<string, object>} nc_path → card ctx while job runs */
  let kiJobCards = new Map();
  /** @type {Map<string, object>} waiting to flush into next job */
  let kiPendingNc = new Map();
  let kiFlushTimer = null;
  let kiJobRunning = false;
  /** @type {string[]} */
  let kiJobOrder = [];
  let browseOffset = 0;
  const BROWSE_PAGE = 80;
  /** @type {Map<string, object>} mount cards on current browse page */
  let mountCardByNc = new Map();
  let folderJobActive = false;
  const telegramEnabled = document.body.dataset.telegramEnabled === "1";
  const faceblurEnabled = document.body.dataset.faceblurEnabled === "1";
  const faceblurRegion = document.body.dataset.faceblurRegion || "face";
  const faceblurStrength = document.body.dataset.faceblurStrength || "1";
  const faceblurMode = document.body.dataset.faceblurMode || "blur";
  const BLUR_UI_LS = "phototag.blurUi.v1";
  let blurDefaults = null;
  let blurUiReady = false;

  function loadBlurUiState() {
    try {
      return JSON.parse(localStorage.getItem(BLUR_UI_LS) || "{}");
    } catch (e) {
      return {};
    }
  }

  function saveBlurUiState() {
    const preset = document.getElementById("blurPreset");
    if (!preset) return;
    const state = {
      preset: preset.value,
      fill: document.getElementById("blurFill")?.value,
      margin: document.getElementById("blurMargin")?.value,
      strength: document.getElementById("blurStrength")?.value,
      mode: document.getElementById("blurMode")?.value,
      parts: [...document.querySelectorAll("#blurParts input:checked")].map((el) => el.value),
    };
    localStorage.setItem(BLUR_UI_LS, JSON.stringify(state));
  }

  function getBlurOptionsPayload() {
    const preset = document.getElementById("blurPreset")?.value || "env";
    if (preset === "env") return { preset: "env" };
    if (preset !== "custom") return { preset };
    const parts = [...document.querySelectorAll("#blurParts input:checked")].map((el) => el.value);
    return {
      preset: "custom",
      region: parts.join(","),
      fill: document.getElementById("blurFill")?.value || "none",
      margin: parseFloat(document.getElementById("blurMargin")?.value || "0.4"),
      strength: parseFloat(document.getElementById("blurStrength")?.value || "1"),
      mode: document.getElementById("blurMode")?.value || "blur",
    };
  }

  function syncBlurCustomPanel() {
    const preset = document.getElementById("blurPreset")?.value || "env";
    const custom = document.getElementById("blurCustomPanel");
    if (custom) custom.classList.toggle("hidden", preset !== "custom");
  }

  function applyBlurPresetToControls(presetId) {
    if (!blurDefaults || presetId === "env" || presetId === "custom") return;
    const p = blurDefaults.presets.find((x) => x.id === presetId);
    if (!p || p.uses_env) return;
    if (document.getElementById("blurFill")) document.getElementById("blurFill").value = p.fill || "none";
    if (document.getElementById("blurMargin")) document.getElementById("blurMargin").value = String(p.margin ?? 0.4);
    if (document.getElementById("blurStrength")) document.getElementById("blurStrength").value = String(p.strength ?? 1);
    if (p.region) {
      const parts = String(p.region).split(/[,+]/).map((s) => s.trim()).filter(Boolean);
      document.querySelectorAll("#blurParts input").forEach((el) => {
        el.checked = parts.includes(el.value);
      });
    }
  }

  async function ensureBlurSettingsBar() {
    if (!faceblurEnabled || blurUiReady) return;
    const bar = document.getElementById("blurSettingsBar");
    if (!bar) return;
    blurDefaults = await api("/api/blur/defaults");
    if (!blurDefaults.ok) return;
    const saved = loadBlurUiState();
    bar.innerHTML = "";
    bar.classList.remove("hidden");
    const hint = document.createElement("span");
    hint.className = "muted-hint";
    hint.textContent = "Face-Blur:";
    const presetSel = document.createElement("select");
    presetSel.id = "blurPreset";
    blurDefaults.presets.forEach((p) => {
      const o = document.createElement("option");
      o.value = p.id;
      o.textContent = p.label;
      presetSel.append(o);
    });
    presetSel.value = saved.preset || "env";
    presetSel.onchange = () => {
      if (presetSel.value !== "custom") applyBlurPresetToControls(presetSel.value);
      syncBlurCustomPanel();
      saveBlurUiState();
    };
    const customPanel = document.createElement("div");
    customPanel.id = "blurCustomPanel";
    customPanel.className = "blur-custom-panel";
    const partsWrap = document.createElement("div");
    partsWrap.id = "blurParts";
    partsWrap.className = "blur-parts";
    blurDefaults.parts.forEach((part) => {
      const lab = document.createElement("label");
      lab.className = "chk blur-part";
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.value = part;
      if ((saved.parts || []).includes(part)) cb.checked = true;
      lab.append(cb, " " + part);
      partsWrap.append(lab);
    });
    const fillSel = document.createElement("select");
    fillSel.id = "blurFill";
    blurDefaults.fills.forEach((f) => {
      const o = document.createElement("option");
      o.value = f;
      o.textContent = f;
      fillSel.append(o);
    });
    fillSel.value = saved.fill || blurDefaults.env.fill || "none";
    const margin = document.createElement("input");
    margin.type = "range";
    margin.id = "blurMargin";
    margin.min = blurDefaults.margin.min;
    margin.max = blurDefaults.margin.max;
    margin.step = "0.01";
    margin.value = saved.margin ?? blurDefaults.margin.default;
    const marginVal = document.createElement("span");
    marginVal.className = "blur-range-val";
    marginVal.id = "blurMarginVal";
    marginVal.textContent = margin.value;
    margin.oninput = () => {
      marginVal.textContent = margin.value;
      saveBlurUiState();
    };
    const strength = document.createElement("input");
    strength.type = "range";
    strength.id = "blurStrength";
    strength.min = blurDefaults.strength.min;
    strength.max = blurDefaults.strength.max;
    strength.step = "0.05";
    strength.value = saved.strength ?? blurDefaults.strength.default;
    const strengthVal = document.createElement("span");
    strengthVal.className = "blur-range-val";
    strengthVal.id = "blurStrengthVal";
    strengthVal.textContent = strength.value;
    strength.oninput = () => {
      strengthVal.textContent = strength.value;
      saveBlurUiState();
    };
    const modeSel = document.createElement("select");
    modeSel.id = "blurMode";
    blurDefaults.modes.forEach((m) => {
      const o = document.createElement("option");
      o.value = m;
      o.textContent = m;
      modeSel.append(o);
    });
    modeSel.value = saved.mode || blurDefaults.env.mode || "blur";
    fillSel.onchange = saveBlurUiState;
    modeSel.onchange = saveBlurUiState;
    partsWrap.querySelectorAll("input").forEach((el) => {
      el.onchange = () => {
        presetSel.value = "custom";
        syncBlurCustomPanel();
        saveBlurUiState();
      };
    });
    margin.onchange = strength.onchange = () => {
      presetSel.value = "custom";
      syncBlurCustomPanel();
      saveBlurUiState();
    };
    customPanel.append(
      document.createTextNode("Teile "),
      partsWrap,
      document.createTextNode(" fill "),
      fillSel,
      document.createTextNode(" margin "),
      margin,
      marginVal,
      document.createTextNode(" strength "),
      strength,
      strengthVal,
      document.createTextNode(" mode "),
      modeSel
    );
    const envNote = document.createElement("span");
    envNote.className = "blur-env-note muted-hint";
    envNote.textContent = " Env: " + (blurDefaults.env.summary || "").slice(0, 140);
    bar.append(hint, presetSel, customPanel, envNote);
    if (presetSel.value !== "custom") applyBlurPresetToControls(presetSel.value);
    syncBlurCustomPanel();
    blurUiReady = true;
  }

  async function withBusyButton(btn, busyLabel, fn) {
    const prev = btn.textContent;
    btn.disabled = true;
    btn.classList.add("btn-busy");
    btn.textContent = busyLabel;
    try {
      return await fn();
    } finally {
      if (!btn.classList.contains("action-ok-btn")) {
        btn.disabled = false;
        btn.classList.remove("btn-busy");
        btn.textContent = prev;
      }
    }
  }

  function flashActionOk(card, btn, okText, resetText) {
    card.classList.remove("save-pending");
    card.classList.add("action-ok");
    btn.classList.remove("btn-busy");
    btn.classList.add("action-ok-btn");
    btn.disabled = false;
    btn.textContent = okText;
    setTimeout(() => {
      card.classList.remove("action-ok");
      btn.classList.remove("action-ok-btn");
      btn.textContent = resetText;
    }, 2800);
  }

  async function persistImageTags(item, tags, caption, push) {
    if ((item.in_review || item.status === "review") && item.id) {
      return api("/api/review/" + item.id + "/approve?push=" + (push ? "1" : "0"), {
        method: "POST",
        body: JSON.stringify({ tags, caption }),
      });
    }
    return api("/api/browse/save?push=" + (push ? "1" : "0"), {
      method: "POST",
      body: JSON.stringify({
        nc_path: item.nc_path,
        tags,
        caption,
      }),
    });
  }

  function setStatus(msg, isErr) {
    const el = document.getElementById("statusLine");
    el.textContent = msg;
    el.className = "status" + (isErr ? " err" : "");
  }

  function isBrowseMode() {
    return document.getElementById("viewMode").value === "browse";
  }

  async function api(path, opts) {
    const headers = { ...(opts && opts.headers) };
    if (opts && opts.body) headers["Content-Type"] = "application/json";
    const res = await fetch(path, {
      ...opts,
      credentials: "same-origin",
      headers,
    });
    if (res.status === 401) {
      window.location.href = "/login";
      throw new Error("Nicht angemeldet");
    }
    const data = res.headers.get("content-type")?.includes("json") ? await res.json() : null;
    if (!res.ok) {
      throw new Error((data && data.error) || res.statusText);
    }
    return data;
  }

  async function loadThumb(img, id) {
    img.src = "/api/images/" + id + "/thumb";
    img.loading = "lazy";
  }

  async function loadThumbNc(img, ncPath, version) {
    img.src =
      "/api/browse/thumb?nc_path=" +
      encodeURIComponent(ncPath) +
      "&v=" +
      encodeURIComponent(version || "");
    img.loading = "lazy";
  }

  function renderTagEditor(container, initialTags, allowed, readOnly) {
    if (readOnly) {
      const chips = document.createElement("div");
      chips.className = "tags";
      (initialTags || []).sort().forEach((t) => {
        const s = document.createElement("span");
        s.className = "tag";
        s.textContent = t;
        chips.appendChild(s);
      });
      container.appendChild(chips);
      return () => [...(initialTags || [])];
    }

    const state = new Set(initialTags);
    const wrap = document.createElement("div");
    wrap.className = "tag-editor";

    const chips = document.createElement("div");
    chips.className = "tags";

    function redraw() {
      chips.innerHTML = "";
      [...state].sort().forEach((t) => {
        const s = document.createElement("button");
        s.type = "button";
        s.className = "tag removable";
        s.textContent = t + " ×";
        s.title = "Tag entfernen";
        s.onclick = () => {
          state.delete(t);
          redraw();
        };
        chips.appendChild(s);
      });
    }
    redraw();

    const addRow = document.createElement("div");
    addRow.className = "add-tag";
    const inp = document.createElement("input");
    inp.type = "text";
    inp.placeholder = "Tag(s): tippen, Komma oder + …";
    inp.setAttribute("list", "tagCatalog");
    const addBtn = document.createElement("button");
    addBtn.type = "button";
    addBtn.textContent = "+";
    addBtn.title = "Tag(s) aus dem Feld übernehmen";

    const pickList = document.createElement("div");
    pickList.className = "tag-pick-list hidden";

    function addTagValue(v) {
      v = v.trim().toLowerCase().replace(/\s+/g, "_");
      if (!v) return false;
      if (allowed.length && !allowed.includes(v)) {
        setStatus("Unbekannter Tag (nicht in categories.txt): " + v, true);
        return false;
      }
      state.add(v);
      return true;
    }

    function commitInput() {
      const raw = inp.value.trim();
      if (!raw) return;
      const parts = raw.split(/[,;]+/).flatMap((chunk) => chunk.split(/\s+/));
      let added = 0;
      parts.forEach((p) => {
        if (addTagValue(p)) added += 1;
      });
      if (added) {
        inp.value = "";
        redraw();
        pickList.classList.add("hidden");
      }
    }

    function refreshPickList() {
      const q = inp.value.trim().toLowerCase().replace(/\s+/g, "_");
      pickList.innerHTML = "";
      if (q.length < 1) {
        pickList.classList.add("hidden");
        return;
      }
      const matches = allowed.filter((t) => t.includes(q) && !state.has(t)).slice(0, 18);
      if (!matches.length) {
        pickList.classList.add("hidden");
        return;
      }
      pickList.classList.remove("hidden");
      matches.forEach((t) => {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "tag-pick";
        b.textContent = t;
        b.onclick = () => {
          addTagValue(t);
          redraw();
          inp.value = "";
          pickList.classList.add("hidden");
        };
        pickList.appendChild(b);
      });
    }

    addBtn.onclick = () => commitInput();
    inp.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        commitInput();
      }
    });
    inp.addEventListener("input", refreshPickList);
    inp.addEventListener("focus", refreshPickList);

    const hint = document.createElement("div");
    hint.className = "tag-hint";
    hint.textContent = "Mehrere Tags: kommagetrennt oder Vorschläge anklicken.";

    addRow.append(inp, addBtn);
    wrap.append(chips, addRow, pickList, hint);
    container.appendChild(wrap);

    function getTags() {
      commitInput();
      return [...state].sort();
    }
    return getTags;
  }

  function updateStatusBadge(badge, status) {
    const st = status || "unindexed";
    badge.textContent = st;
    badge.className =
      "status-badge" +
      (st === "review" ? " review" : st === "tagged" ? " tagged" : "");
  }

  function setTelegramOverlay(card, text, active) {
    if (!card) return;
    card.classList.toggle("tg-sending", !!active);
    let el = card.querySelector(".tg-state");
    if (!el) {
      el = document.createElement("div");
      el.className = "tg-state hidden";
      card.appendChild(el);
    }
    if (!active || !text) {
      el.classList.add("hidden");
      el.textContent = "";
      if (!active) card.classList.remove("tg-sending");
      return;
    }
    el.textContent = text;
    el.classList.remove("hidden");
  }

  function setKiOverlay(ctx, text, kind) {
    if (!ctx || !ctx.card) return;
    let el = ctx.card.querySelector(".ki-state");
    if (!el) {
      el = document.createElement("div");
      el.className = "ki-state hidden";
      ctx.card.appendChild(el);
    }
    el.classList.remove("is-queued", "is-running");
    if (!text) {
      el.classList.add("hidden");
      el.textContent = "";
      return;
    }
    el.textContent = text;
    el.classList.remove("hidden");
    if (kind === "running") {
      el.classList.add("is-running");
    } else {
      el.classList.add("is-queued");
    }
  }

  function markCardKiState(ctx, state, pos, total) {
    if (!ctx || !ctx.tagBtn) return;
    const posLabel = pos && total ? " " + pos + "/" + total : "";
    ctx.card.classList.remove("ki-busy", "ki-queued");
    ctx.tagBtn.classList.remove("ki-busy", "ki-queued");
    if (state === "queued") {
      ctx.tagBtn.disabled = true;
      ctx.tagBtn.classList.add("ki-queued");
      ctx.card.classList.add("ki-queued");
      ctx.tagBtn.textContent = "Warteschlange" + posLabel;
      setKiOverlay(ctx, "Warteschlange" + posLabel, "queued");
    } else if (state === "running") {
      ctx.tagBtn.disabled = true;
      ctx.tagBtn.classList.add("ki-busy");
      ctx.card.classList.add("ki-busy");
      ctx.tagBtn.textContent = "KI läuft" + posLabel + " …";
      setKiOverlay(ctx, "KI aktiv" + posLabel, "running");
    } else {
      ctx.tagBtn.disabled = false;
      ctx.tagBtn.textContent = "KI taggen";
      setKiOverlay(ctx, "", "");
    }
  }

  function resetKiTagButton(ctx) {
    if (!ctx || !ctx.tagBtn) return;
    markCardKiState(ctx, "idle");
  }

  function appendReviewActions(actions, ctx, opts) {
    const { item, card, badge, getTags, capArea } = ctx;
    if (!item.id) return;
    const inReview = item.in_review || item.status === "review";
    if (!opts || !opts.always) {
      if (!inReview) return;
    }

    actions.classList.add("review-actions");

    const approveBtn = document.createElement("button");
    approveBtn.className = "ok";
    approveBtn.textContent = "Freigeben";
    approveBtn.onclick = async () => {
      approveBtn.disabled = true;
      approveBtn.classList.add("btn-busy");
      approveBtn.textContent = "Freigabe …";
      card.classList.add("save-pending");
      try {
        const tags = getTags();
        if (!tags.length) {
          setStatus("Mindestens ein Tag nötig — Feld ausfüllen und + oder Vorschlag klicken", true);
          return;
        }
        const push = document.getElementById("pushAfter").checked;
        if (push) {
          setStatus("Freigabe inkl. push-nc … (kann dauern)");
        }
        await persistImageTags(item, tags, capArea.value.trim(), push);
        card.remove();
        setStatus("Freigegeben: " + item.nc_path);
      } catch (e) {
        setStatus(String(e.message || e), true);
        card.classList.remove("save-pending");
        approveBtn.disabled = false;
        approveBtn.classList.remove("btn-busy");
        approveBtn.textContent = "Freigeben";
      }
    };

    const skipBtn = document.createElement("button");
    skipBtn.className = "warn";
    skipBtn.textContent = "Überspringen";
    skipBtn.onclick = async () => {
      if (!confirm("Aus Review entfernen (status=new)?")) return;
      try {
        await api("/api/review/" + item.id + "/skip", { method: "POST" });
        card.remove();
      } catch (e) {
        setStatus(String(e.message || e), true);
      }
    };
    actions.append(approveBtn, skipBtn);
  }

  /** Face-Blur: preview (no file) or job with options from the browse bar. */
  function appendFaceBlurButton(actions, item, note, card, img) {
    if (!faceblurEnabled || !item.nc_path || item.is_blur_copy) return;
    const previewBtn = document.createElement("button");
    previewBtn.type = "button";
    previewBtn.textContent = "Vorschau";
    previewBtn.title = "Blur-Ergebnis nur in der Karte anzeigen — keine Datei auf dem Mount";
    previewBtn.onclick = async () => {
      previewBtn.disabled = true;
      try {
        const opts = getBlurOptionsPayload();
        const res = await fetch("/api/blur/preview", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "same-origin",
          body: JSON.stringify({ nc_path: item.nc_path, options: opts }),
        });
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          throw new Error(err.error || res.statusText);
        }
        const blob = await res.blob();
        const src = URL.createObjectURL(blob);
        img.src = src;
        img.classList.add("blur-preview-img");
        const faces = res.headers.get("X-Phototag-Blur-Faces") || "?";
        const srcTag = res.headers.get("X-Phototag-Blur-Source") || "env";
        const warn = res.headers.get("X-Phototag-Blur-Warnings");
        let msg = "Vorschau (" + faces + " Gesicht(er), Einstellung: " + srcTag + ")";
        if (warn) msg += " — " + warn;
        if (note) note.textContent = msg;
        setStatus(msg);
      } catch (e) {
        setStatus("Vorschau: " + String(e.message || e), true);
      }
      previewBtn.disabled = false;
    };
    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = "Face-Blur";
    btn.title = "Erzeugt *.ai-blurred.* neben dem Original (Einstellungen aus der Leiste oben)";
    btn.onclick = async () => {
      const nc = item.nc_path;
      btn.disabled = true;
      previewBtn.disabled = true;
      btn.classList.add("blur-busy");
      btn.textContent = "Blur läuft …";
      const opts = getBlurOptionsPayload();
      try {
        const started = await api("/api/blur/jobs", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ nc_path: nc, options: opts }),
        });
        let job = started.job;
        while (job && (job.status === "pending" || job.status === "running")) {
          const pct = Math.round((job.progress || 0) * 100);
          btn.textContent = "Blur " + pct + " %";
          setStatus("Face-Blur: " + nc + " — " + pct + " %");
          await new Promise((r) => setTimeout(r, 1500));
          job = (await api("/api/blur/jobs/" + encodeURIComponent(job.id))).job;
        }
        if (job.status === "done") {
          const r = job.result || {};
          const noteExtra = Array.isArray(r.notes) && r.notes.length ? " [" + r.notes.join("; ") + "]" : "";
          setStatus(
            "Face-Blur fertig: " + r.nc_path + " (" + (r.faces || 0) + " Gesicht(er))" + noteExtra + " — bitte prüfen (blurred_review)"
          );
          if (note) note.textContent = "Blur-Kopie: " + r.nc_path;
          await reload();
          return;
        }
        if (job.status === "no_faces") {
          setStatus("Face-Blur: keine Gesichter erkannt — keine Kopie erzeugt", true);
        } else {
          setStatus("Face-Blur fehlgeschlagen (" + job.status + "): " + (job.error || ""), true);
        }
      } catch (e) {
        setStatus("Face-Blur: " + String(e.message || e), true);
      }
      btn.disabled = false;
      previewBtn.disabled = false;
      btn.classList.remove("blur-busy");
      btn.textContent = "Face-Blur";
    };
    actions.append(previewBtn, btn);
  }

  /** Human check of a blur copy: approve (blurred_ok) or delete the generated file. */
  function appendBlurReviewActions(actions, item, badge, note) {
    if (!item.is_blur_copy || !item.id) return;
    const ok = document.createElement("button");
    ok.className = "ok";
    ok.type = "button";
    ok.textContent = "Blur OK";
    ok.title = "Blur geprüft — Status blurred_ok (kein automatisches KI-Tagging)";
    const say = (text, isErr) => {
      // the status line is at the top of the page and often out of sight: also tell it ON the card
      if (note) {
        note.textContent = text;
        note.classList.toggle("blur-warn", !!isErr);
      }
      setStatus(text, isErr);
    };
    ok.onclick = async () => {
      ok.disabled = true;
      ok.textContent = "Speichere …";
      try {
        await api("/api/blur/" + item.id + "/approve", { method: "POST" });
        item.status = "blurred_ok";
        badge.textContent = "blurred_ok";
        badge.className = "status-badge blurred_ok";
        ok.textContent = "Freigegeben ✓";
        ok.title = "Status blurred_ok. Es wird nichts automatisch getaggt oder gesendet.";
        say("Blur freigegeben (blurred_ok). Nichts wird automatisch getaggt oder gesendet — KI taggen oder → Telegram bei Bedarf selbst drücken.", false);
      } catch (e) {
        ok.disabled = false;
        ok.textContent = "Blur OK";
        say("Blur OK fehlgeschlagen: " + String(e.message || e), true);
      }
    };
    const del = document.createElement("button");
    del.type = "button";
    del.className = "danger";
    del.textContent = "Blur verwerfen";
    del.title = "Löscht die erzeugte Blur-Datei (auch in Nextcloud). Das Original bleibt.";
    del.onclick = async () => {
      if (!confirm("Blur-Kopie löschen?\n\n" + item.nc_path + "\n\nDas Original bleibt unverändert.")) return;
      del.disabled = true;
      try {
        await api("/api/blur/" + item.id + "/reject", { method: "POST" });
        setStatus("Blur-Kopie gelöscht: " + item.nc_path);
        await reload();
      } catch (e) {
        del.disabled = false;
        say("Verwerfen fehlgeschlagen: " + String(e.message || e), true);
      }
    };
    actions.append(ok, del);
  }

  /** After a page reload the running blur job is gone from the screen; pick it up again. */
  async function resumeBlurJob() {
    if (!faceblurEnabled) return;
    let job;
    try {
      job = (await api("/api/blur/jobs/active")).job;
    } catch (e) {
      return;
    }
    if (!job) return;
    while (job && (job.status === "pending" || job.status === "running")) {
      setStatus("Face-Blur läuft: " + job.nc_path + " — " + Math.round((job.progress || 0) * 100) + " %");
      await new Promise((r) => setTimeout(r, 2000));
      try {
        job = (await api("/api/blur/jobs/" + encodeURIComponent(job.id))).job;
      } catch (e) {
        return;
      }
    }
    if (job && job.status === "done") {
      setStatus("Face-Blur fertig: " + ((job.result || {}).nc_path || job.nc_path) + " — bitte prüfen (blurred_review)");
      reload().catch(() => {});
    } else if (job) {
      setStatus("Face-Blur (" + job.status + "): " + (job.error || ""), true);
    }
  }

  function appendTelegramButton(actions, item, capArea) {
    if (!telegramEnabled || !item.nc_path) return;
    const tgBtn = document.createElement("button");
    tgBtn.type = "button";
    tgBtn.textContent = "→ Telegram";
    tgBtn.title =
      "Originaldatei an den konfigurierten Chat senden (Bot-Chat oder Gruppe; Videos bis 50 MB, ggf. Minuten Upload)";
    tgBtn.onclick = async () => {
      const card = tgBtn.closest(".card");
      const nc = item.nc_path;
      setStatus(
        "Telegram: " +
          nc +
          " — Upload (grosse Videos: Staging + Kompression, ggf. 10+ Min.) …"
      );
      setTelegramOverlay(card, "Telegram Upload …", true);
      tgBtn.classList.add("tg-busy");
      try {
        await withBusyButton(tgBtn, "Sende …", async () => {
          const res = await api("/api/browse/telegram", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              nc_path: nc,
              caption: (capArea && capArea.value.trim()) || nc,
            }),
          });
          setStatus(
          "Telegram OK: " +
            nc +
            (res.compressed ? " (komprimiert)" : "") +
            " msg " +
            (res.message_id || "?")
        );
          if (card) flashActionOk(card, tgBtn, "Gesendet ✓", "→ Telegram");
        });
      } catch (e) {
        const msg = String(e.message || e);
        setStatus("Telegram fehlgeschlagen: " + msg, true);
        tgBtn.classList.remove("tg-busy");
        tgBtn.classList.add("tg-err");
        tgBtn.textContent = "Fehler — siehe Status";
        setTimeout(() => {
          tgBtn.classList.remove("tg-err", "btn-busy");
          tgBtn.disabled = false;
          tgBtn.textContent = "→ Telegram";
        }, 5000);
      } finally {
        setTelegramOverlay(card, "", false);
        tgBtn.classList.remove("tg-busy");
      }
    };
    actions.append(tgBtn);
  }

  function updateKiQueueHint() {
    const n = kiPendingNc.size + (kiJobRunning ? kiJobCards.size : 0);
    if (n > 0 && !kiJobRunning) {
      setStatus(n + " Datei(en) in KI-Warteschlange …");
    }
  }

  function scheduleKiJobFlush() {
    if (kiFlushTimer) clearTimeout(kiFlushTimer);
    kiFlushTimer = setTimeout(flushKiPendingJob, 400);
  }

  function tagRetagCheckboxChecked() {
    return !!document.getElementById("tagRetag")?.checked;
  }

  /** @returns {boolean|null} true = proceed with retag, false = abort, null = normal KI (not tagged) */
  function confirmKiForItem(item) {
    if (item.status !== "tagged") return null;
    const viaCheckbox = tagRetagCheckboxChecked();
    const msg = viaCheckbox
      ? "Datei ist bereits getaggt.\n\nKI erneut ausführen und Caption/Tags in der DB überschreiben?"
      : "Datei ist bereits getaggt — ohne Bestätigung startet kein KI-Lauf.\n\nJetzt neu taggen (DB wird überschrieben)?";
    return confirm(msg);
  }

  function queueKiTag(item, ctx, forceRetag) {
    const nc = item.nc_path;
    if (kiPendingNc.has(nc) || kiJobCards.has(nc)) return;
    const useRetag = !!forceRetag || tagRetagCheckboxChecked();
    markCardKiState(ctx, "queued");
    kiPendingNc.set(nc, { ctx, retag: useRetag });
    updateKiQueueHint();
    if (kiJobRunning) return;
    scheduleKiJobFlush();
  }

  async function flushKiPendingJob() {
    kiFlushTimer = null;
    if (kiJobRunning || !kiPendingNc.size) return;
    const entries = [...kiPendingNc.entries()];
    kiPendingNc.clear();
    const nc_paths = entries.map(([p]) => p);
    kiJobOrder = nc_paths;
    entries.forEach(([p, entry], idx) => {
      kiJobCards.set(p, entry.ctx);
      markCardKiState(entry.ctx, "queued", idx + 1, nc_paths.length);
    });
    const retag =
      tagRetagCheckboxChecked() || entries.some(([, entry]) => entry.retag);
    try {
      const data = await api("/api/tag/jobs", {
        method: "POST",
        body: JSON.stringify({ nc_paths, retag, recursive: false }),
      });
      if (!data.ok || !data.job || data.job.total === 0) {
        entries.forEach(([, entry]) => resetKiTagButton(entry.ctx));
        kiJobCards.clear();
        let err =
          (data && data.error) || "Keine Dateien für KI-Job (Mount/Index?)";
        if (data && data.error === "Keine Dateien für Job") {
          err =
            "Kein KI-Lauf: Datei(en) bereits getaggt — «Neu taggen» aktivieren oder beim Klick bestätigen.";
        }
        setStatus(err, true);
        return;
      }
      kiJobRunning = true;
      setStatus("KI-Job " + data.job.id + ": " + data.job.total + " Datei(en) in Stapel");
      showJobPanel(data.job);
      pollTagJob(data.job.id, true);
    } catch (e) {
      entries.forEach(([, entry]) => resetKiTagButton(entry.ctx));
      kiJobCards.clear();
      setStatus(String(e.message || e), true);
    }
  }

  function onKiJobFinished(job) {
    kiJobRunning = false;
    kiJobOrder = [];
    kiJobCards.forEach((ctx) => resetKiTagButton(ctx));
    kiJobCards.clear();
    if (folderJobActive) {
      resetFolderTagButton();
    }
    if (kiPendingNc.size) {
      scheduleKiJobFlush();
    }
    if (job && job.status === "done") {
      setStatus(
        "KI-Job fertig: " + job.ok_count + " ok, " + job.fail_count + " fehler — Ansicht aktualisiert"
      );
      reload().catch(() => {});
    } else if (job && job.status === "error") {
      setStatus("KI-Job fehlgeschlagen: " + (job.last_error || ""), true);
    }
  }

  function renderMountItem(item) {
    const inReview = item.in_review || item.status === "review";
    const card = document.createElement("article");
    card.className = "card";
    if (item.id) card.dataset.id = item.id;
    card.dataset.ncPath = item.nc_path;

    const img = document.createElement("img");
    img.alt = item.nc_path;
    loadThumbNc(img, item.nc_path, item.thumb_v);

    const body = document.createElement("div");
    body.className = "card-body";

    const path = document.createElement("div");
    path.className = "path";
    const idPart = item.id ? "id=" + item.id + "  ·  " : "";
    path.textContent = item.nc_path + "  ·  " + idPart + item.kind;

    const meta = document.createElement("div");
    meta.className = "meta";
    const badge = document.createElement("span");
    const st = item.status || "unindexed";
    badge.className =
      "status-badge" +
      (inReview ? " review" : st === "tagged" ? " tagged" : st.startsWith("blurred_") ? " " + st : "");
    badge.textContent = st;
    meta.append(badge);
    if (item.age_status) {
      meta.append(document.createTextNode("  age_status: " + item.age_status));
    }
    if (item.sanitize_flags && item.sanitize_flags.length) {
      meta.append(document.createTextNode("  ·  flags: " + item.sanitize_flags.join(", ")));
    }

    const capLabel = document.createElement("label");
    capLabel.textContent = "Caption";
    const capArea = document.createElement("textarea");
    capArea.className = "caption-edit";
    capArea.rows = 5;
    capArea.value = item.caption || "";

    const tagBlock = document.createElement("div");
    const tagList = item.tags || item.proposed_tags || [];
    const getTags = renderTagEditor(tagBlock, tagList, tagCatalog, false);
    const getTagsRef = { current: getTags };

    const actions = document.createElement("div");
    actions.className = "actions";

    const saveBtn = document.createElement("button");
    saveBtn.className = "ok";
    saveBtn.textContent = "Speichern";
    saveBtn.onclick = async () => {
      saveBtn.disabled = true;
      saveBtn.classList.add("btn-busy");
      saveBtn.textContent = "Speichere …";
      card.classList.add("save-pending");
      try {
        const tags = getTagsRef.current();
        if (!tags.length) {
          setStatus(
            "Mindestens ein gültiger Tag — aus categories.txt, dann + oder Vorschlag",
            true
          );
          card.classList.remove("save-pending");
          saveBtn.disabled = false;
          saveBtn.classList.remove("btn-busy");
          saveBtn.textContent = "Speichern";
          return;
        }
        const push = document.getElementById("pushAfter").checked;
        const inRev = item.in_review || item.status === "review";
        if (push) {
          setStatus(inRev ? "Freigabe inkl. push-nc …" : "Speichern inkl. push-nc …");
        }
        const res = await persistImageTags(item, tags, capArea.value.trim(), push);
        const inReview =
          res.status === "review" ||
          (res.sanitize_flags && res.sanitize_flags.length > 0);
        if (inReview) {
          updateStatusBadge(badge, "review");
          item.status = "review";
          item.in_review = true;
        } else {
          updateStatusBadge(badge, "tagged");
          item.status = "tagged";
          item.in_review = false;
        }
        if (res.id) {
          card.dataset.id = res.id;
          item.id = res.id;
        }
        if (res.tags && res.tags.length) {
          item.tags = res.tags;
          getTagsRef.current = renderTagEditor(tagBlock, res.tags, tagCatalog, false);
        }
        let msg =
          (inRev ? "Freigabe: " : push && !inReview ? "DB + NC: " : "Gespeichert: ") +
          item.nc_path;
        if (inReview) {
          msg += " → Review (sanitize)";
          if (res.sanitize_flags && res.sanitize_flags.length) {
            msg += ": " + res.sanitize_flags.join(", ");
          }
        } else if (res.pushed_tags) {
          msg += " (" + res.pushed_tags + " Tags NC)";
        }
        setStatus(msg);
        flashActionOk(
          card,
          saveBtn,
          inReview ? "Review ✓" : inRev ? "Freigegeben ✓" : "Gespeichert ✓",
          "Speichern"
        );
      } catch (e) {
        setStatus(String(e.message || e), true);
        card.classList.remove("save-pending");
        saveBtn.disabled = false;
        saveBtn.classList.remove("btn-busy");
        saveBtn.textContent = "Speichern";
      }
    };
    actions.append(saveBtn);

    const tagBtn = document.createElement("button");
    tagBtn.textContent = "KI taggen";
    const kiCtx = {
      card,
      tagBtn,
      badge,
      capArea,
      tagBlock,
      getTagsRef,
    };
    tagBtn.onclick = () => {
      const tagged = item.status === "tagged";
      if (tagged) {
        const ok = confirmKiForItem(item);
        if (!ok) return;
        queueKiTag(item, kiCtx, true);
        return;
      }
      queueKiTag(item, kiCtx, false);
    };
    actions.append(tagBtn);
    appendTelegramButton(actions, item, capArea);
    const blurNote = document.createElement("div");
    blurNote.className = "blur-note";
    if (item.is_blur_copy) {
      blurNote.textContent =
        "AI-Blur-Kopie (" + (item.blur_faces || 0) + " Gesicht(er)) — Original unverändert" +
        (item.blur_info ? "\n" + item.blur_info.replace(/^faceblur:faces=\d+;?\s*/, "") : "");
      blurNote.style.whiteSpace = "pre-wrap";
      if (/WARNUNG/.test(item.blur_info || "")) blurNote.classList.add("blur-warn");
    } else if (item.blur_copies && item.blur_copies.length) {
      blurNote.textContent =
        "Blur-Kopie: " + item.blur_copies.map((c) => c.nc_path + " [" + c.status + "]").join(", ");
    }
    appendFaceBlurButton(actions, item, blurNote, card, img);
    appendBlurReviewActions(actions, item, badge, blurNote);

    mountCardByNc.set(item.nc_path, kiCtx);

    appendReviewActions(actions, {
      item,
      card,
      badge,
      getTags: () => getTagsRef.current(),
      capArea,
    });

    body.append(path, meta, blurNote, capLabel, capArea, tagBlock, actions);
    card.append(img, body);
    return card;
  }

  function renderItem(item) {
    item.status = item.status || "review";
    item.in_review = true;
    const inReview = true;
    const card = document.createElement("article");
    card.className = "card";
    card.dataset.id = item.id;

    const img = document.createElement("img");
    img.alt = item.nc_path;
    loadThumb(img, item.id);

    const body = document.createElement("div");
    body.className = "card-body";

    const path = document.createElement("div");
    path.className = "path";
    path.textContent = item.nc_path + "  ·  id=" + item.id + "  ·  " + item.kind;

    const meta = document.createElement("div");
    meta.className = "meta";
    const badge = document.createElement("span");
    badge.className = "status-badge" + (inReview ? " review" : item.status === "tagged" ? " tagged" : "");
    badge.textContent = item.status || "?";
    meta.append(badge, document.createTextNode("  age_status: " + (item.age_status || "?")));
    if (item.sanitize_flags && item.sanitize_flags.length) {
      meta.append(document.createTextNode("  ·  flags: " + item.sanitize_flags.join(", ")));
    }

    const capLabel = document.createElement("label");
    capLabel.textContent = "Caption";
    const capArea = document.createElement("textarea");
    capArea.className = "caption-edit";
    capArea.rows = 5;
    capArea.value = item.caption || "";

    const tagBlock = document.createElement("div");
    const tagList = item.proposed_tags || item.tags || [];
    const getTags = renderTagEditor(tagBlock, tagList, tagCatalog, false);

    const actions = document.createElement("div");
    actions.className = "actions";

    appendReviewActions(actions, { item, card, badge, getTags, capArea }, { always: true });
    appendTelegramButton(actions, item, capArea);

    body.append(path, meta, capLabel, capArea, tagBlock, actions);
    card.append(img, body);
    return card;
  }

  async function loadCatalog() {
    const data = await api("/api/tags");
    tagCatalog = data.tags || [];
    let dl = document.getElementById("tagCatalog");
    if (!dl) {
      dl = document.createElement("datalist");
      dl.id = "tagCatalog";
      document.body.appendChild(dl);
    }
    dl.innerHTML = "";
    tagCatalog.forEach((t) => {
      const o = document.createElement("option");
      o.value = t;
      dl.appendChild(o);
    });
  }

  function renderBreadcrumb() {
    const nav = document.getElementById("breadcrumb");
    nav.innerHTML = "";
    const parts = browseFolder ? browseFolder.split("/") : [];

    const rootBtn = document.createElement("button");
    rootBtn.type = "button";
    rootBtn.className = "link";
    rootBtn.textContent = "Root";
    rootBtn.onclick = () => {
      browseFolder = "";
      browseOffset = 0;
      loadBrowse();
    };
    nav.appendChild(rootBtn);

    let acc = "";
    parts.forEach((seg) => {
      const sep = document.createElement("span");
      sep.className = "sep";
      sep.textContent = "/";
      nav.appendChild(sep);
      acc = acc ? acc + "/" + seg : seg;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "link";
      btn.textContent = seg;
      const pathHere = acc;
      btn.onclick = () => {
        browseFolder = pathHere;
        browseOffset = 0;
        loadBrowse();
      };
      nav.appendChild(btn);
    });
  }

  async function loadSubfolders() {
    const box = document.getElementById("subfolders");
    box.innerHTML = "";
    const data = await api("/api/browse/folders?folder=" + encodeURIComponent(browseFolder));
    if (!data.folders.length) {
      box.classList.add("hidden");
      return;
    }
    box.classList.remove("hidden");
    const hint = document.createElement("span");
    hint.className = "muted-hint";
    hint.textContent = "Unterordner: ";
    box.appendChild(hint);
    data.folders.forEach((f) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "folder-pick";
      const name = f.includes("/") ? f.split("/").pop() : f;
      b.textContent = "📁 " + name;
      b.onclick = () => {
        browseFolder = f;
        browseOffset = 0;
        loadBrowse();
      };
      box.appendChild(b);
    });
  }

  function resetFolderTagButton() {
    const folderBtn = document.getElementById("tagFolderBtn");
    if (!folderBtn) return;
    folderBtn.disabled = false;
    folderBtn.classList.remove("ki-busy");
    folderBtn.textContent = "Ordner KI-taggen";
    folderJobActive = false;
  }

  function bindFolderJobCards(job) {
    kiJobOrder = Array.isArray(job.paths) ? job.paths : [];
    kiJobCards.clear();
    kiJobOrder.forEach((nc) => {
      const ctx = mountCardByNc.get(nc);
      if (ctx) {
        kiJobCards.set(nc, ctx);
      }
    });
  }

  function syncJobCardStatesFromJob(job) {
    if (!kiJobCards.size || !kiJobOrder.length) return;
    const cur = job.current_nc_path || "";
    const done = job.done || 0;
    const total = kiJobOrder.length;
    kiJobOrder.forEach((nc, idx) => {
      const ctx = kiJobCards.get(nc);
      if (!ctx) return;
      if (job.status === "done" || job.status === "error" || idx < done) {
        resetKiTagButton(ctx);
      } else if (job.status === "running" && nc === cur) {
        markCardKiState(ctx, "running", idx + 1, total);
      } else if (job.status === "running") {
        markCardKiState(ctx, "queued", idx + 1, total);
      }
    });
  }

  function updateFolderJobButton(job) {
    if (!folderJobActive) return;
    const folderBtn = document.getElementById("tagFolderBtn");
    if (!folderBtn) return;
    folderBtn.disabled = true;
    folderBtn.classList.add("ki-busy");
    if (job.status === "running") {
      folderBtn.textContent =
        "KI " + job.done + "/" + job.total + (job.current_nc_path ? " …" : "");
    } else if (job.status === "done") {
      folderBtn.textContent = "Fertig " + job.ok_count + "/" + job.total;
    }
  }

  function escHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/\"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function renderJobPanelContent(job, trackCards) {
    const parts = [];
    parts.push(
      "<strong>Tag-Job " +
        escHtml(job.id) +
        "</strong> " +
        escHtml(job.status) +
        " — " +
        job.done +
        "/" +
        job.total +
        " · ok " +
        job.ok_count +
        ", fehler " +
        job.fail_count
    );
    if (job.current_nc_path && job.status === "running") {
      parts.push("<div class=\"job-current\"><strong>aktiv:</strong> " + escHtml(job.current_nc_path) + "</div>");
    }
    if (job.last_error && job.fail_count) {
      parts.push("<div class=\"job-err\">" + escHtml(job.last_error) + "</div>");
    }
    if (Array.isArray(job.paths) && job.paths.length) {
      const done = job.done || 0;
      const cur = job.current_nc_path || "";
      parts.push('<ol class="job-path-list">');
      const show = job.paths.slice(0, 40);
      show.forEach((nc, idx) => {
        let cls = "job-path-pending";
        if (job.status === "done" || idx < done) cls = "job-path-done";
        else if (nc === cur && job.status === "running") cls = "job-path-active";
        parts.push('<li class="' + cls + '">' + escHtml(nc) + "</li>");
      });
      if (job.paths.length > show.length) {
        parts.push(
          '<li class="job-path-more">… ' +
            (job.paths.length - show.length) +
            " weitere in diesem Job</li>"
        );
      }
      parts.push("</ol>");
      if (trackCards && kiJobCards.size < job.paths.length) {
        parts.push(
          '<p class="job-hint">Nur Dateien auf dieser Seite mit Rahmen markiert (' +
            kiJobCards.size +
            " von " +
            job.paths.length +
            ").</p>"
        );
      }
    }
    return parts.join("");
  }

  function showJobPanel(job, trackCards) {
    const el = document.getElementById("jobPanel");
    if (!job || job.status === "done" || job.status === "error") {
      if (job && (job.status === "done" || job.status === "error")) {
        el.classList.remove("hidden");
        el.innerHTML = renderJobPanelContent(job, trackCards);
        if (trackCards) {
          syncJobCardStatesFromJob(job);
          onKiJobFinished(job);
        } else if (folderJobActive) {
          syncJobCardStatesFromJob(job);
          onKiJobFinished(job);
        } else if (job.status === "done") {
          setTimeout(() => {
            el.classList.add("hidden");
            reload().catch(() => {});
          }, 4000);
        }
      } else {
        el.classList.add("hidden");
      }
      if (jobPollTimer) {
        clearInterval(jobPollTimer);
        jobPollTimer = null;
      }
      return;
    }
    el.classList.remove("hidden");
    el.innerHTML = renderJobPanelContent(job, trackCards);
    updateFolderJobButton(job);
    if (trackCards || folderJobActive) {
      syncJobCardStatesFromJob(job);
    }
  }

  function pollTagJob(jobId, trackCards) {
    if (jobPollTimer) clearInterval(jobPollTimer);
    const tick = async () => {
      try {
        const data = await api("/api/tag/jobs/" + jobId);
        showJobPanel(data.job, trackCards);
      } catch (e) {
        setStatus(String(e.message || e), true);
        clearInterval(jobPollTimer);
        jobPollTimer = null;
        if (trackCards) onKiJobFinished(null);
      }
    };
    tick();
    jobPollTimer = setInterval(tick, 2000);
  }

  async function startFolderTagJob() {
    const folder = browseFolder;
    const retag = tagRetagCheckboxChecked();
    const recursive = document.getElementById("tagRecursive").checked;
    const folderBtn = document.getElementById("tagFolderBtn");
    let preview;
    try {
      preview = await api(
        "/api/tag/jobs/preview?folder=" +
          encodeURIComponent(folder) +
          "&recursive=" +
          (recursive ? "1" : "0") +
          "&retag=" +
          (retag ? "1" : "0")
      );
    } catch (e) {
      setStatus(String(e.message || e), true);
      return;
    }
    if (!preview.ok) {
      setStatus(preview.error || "Vorschau fehlgeschlagen", true);
      return;
    }
    const label = folder || "Root";
    let msg =
      "KI-Tagging für «" +
      label +
      "»" +
      (recursive ? " inkl. Unterordner" : " nur dieser Ordner") +
      "?\n\n" +
      "→ " +
      preview.will_tag +
      " Datei(en) werden getaggt";
    if (!retag && preview.skipped_tagged > 0) {
      msg +=
        "\n→ " +
        preview.skipped_tagged +
        " bereits getaggt — werden übersprungen";
    }
    if (preview.media_files && preview.media_files !== preview.will_tag) {
      msg +=
        "\n→ " +
        preview.media_files +
        " Mediendatei(en) auf dem Mount (gesamt)";
    }
    if (retag) {
      msg +=
        "\n\nNeu taggen ist aktiv — auch getaggte Dateien werden erneut verarbeitet.";
    }
    if (preview.will_tag === 0) {
      setStatus(
        "Kein KI-Job: 0 Dateien zum Taggen" +
          (preview.skipped_tagged
            ? " (" + preview.skipped_tagged + " bereits getaggt)"
            : "") +
          ". Optional «Neu taggen» für Retag.",
        true
      );
      return;
    }
    if (!confirm(msg)) return;
    folderBtn.disabled = true;
    folderBtn.classList.add("ki-busy");
    folderBtn.textContent = "Job startet …";
    try {
      const data = await api("/api/tag/jobs", {
        method: "POST",
        body: JSON.stringify({
          folders: folder ? [folder] : [""],
          retag,
          recursive,
        }),
      });
      folderJobActive = true;
      kiJobRunning = true;
      bindFolderJobCards(data.job);
      setStatus(
        "Tag-Job " +
          data.job.id +
          ": " +
          data.job.total +
          " Datei(en) — Fortschritt unten und auf sichtbaren Karten"
      );
      showJobPanel(data.job, true);
      pollTagJob(data.job.id, true);
    } catch (e) {
      setStatus(String(e.message || e), true);
      resetFolderTagButton();
    }
  }

  function renderBrowsePager(total, offset, limit) {
    const nav = document.getElementById("browsePager");
    if (!isBrowseMode() || total <= limit) {
      nav.classList.add("hidden");
      nav.innerHTML = "";
      return;
    }
    nav.classList.remove("hidden");
    const page = Math.floor(offset / limit) + 1;
    const pages = Math.max(1, Math.ceil(total / limit));
    nav.innerHTML = "";
    const info = document.createElement("span");
    info.className = "pager-info";
    info.textContent =
      "Seite " + page + " / " + pages + " · " + total + " Treffer";
    nav.appendChild(info);
    const prev = document.createElement("button");
    prev.type = "button";
    prev.textContent = "← Zurück";
    prev.disabled = offset <= 0;
    prev.onclick = () => {
      browseOffset = Math.max(0, offset - limit);
      loadBrowse().catch((e) => setStatus(e.message, true));
    };
    nav.appendChild(prev);
    const next = document.createElement("button");
    next.type = "button";
    next.textContent = "Weiter →";
    next.disabled = offset + limit >= total;
    next.onclick = () => {
      browseOffset = offset + limit;
      loadBrowse().catch((e) => setStatus(e.message, true));
    };
    nav.appendChild(next);
  }

  async function loadQueue() {
    setStatus("Lade Review …");
    await loadCatalog();
    const data = await api("/api/review");
    const q = document.getElementById("queue");
    q.innerHTML = "";
    if (!data.items.length) {
      setStatus("Review-Queue leer.");
      return;
    }
    data.items.forEach((item) => q.appendChild(renderItem(item)));
    setStatus(data.count + " in Review — Tags anpassen, dann Freigeben.");
  }

  async function loadBrowse() {
    setStatus("Lade Ordner …");
    await loadCatalog();
    if (faceblurEnabled) await ensureBlurSettingsBar();
    renderBreadcrumb();
    await loadSubfolders();
    mountCardByNc.clear();
    const filter = document.getElementById("browseFilter").value;
    const recEl = document.getElementById("browseRecursive");
    if (filter === "blurred") {
      recEl.checked = true;
    }
    const recursive = filter === "blurred" || recEl.checked;
    const data = await api(
      "/api/browse?folder=" +
        encodeURIComponent(browseFolder) +
        "&filter=" +
        encodeURIComponent(filter) +
        "&recursive=" +
        (recursive ? "1" : "0") +
        "&offset=" +
        browseOffset +
        "&limit=" +
        BROWSE_PAGE
    );
    const q = document.getElementById("queue");
    q.innerHTML = "";
    renderBrowsePager(data.total, data.offset, data.limit);
    const scope = recursive
      ? "Baum unter " + (browseFolder || "Root")
      : browseFolder
        ? browseFolder + "/"
        : "Root/";
    if (!data.items.length) {
      setStatus(
        "Keine Mediendateien in " +
          scope +
          " (Filter: " +
          filter +
          "). Anderen Ordner wählen oder Mount prüfen."
      );
      return;
    }
    data.items.forEach((item) => q.appendChild(renderMountItem(item)));
    const pageNote =
      data.total > data.limit
        ? " · Einträge " +
          (data.offset + 1) +
          "–" +
          Math.min(data.offset + data.count, data.total)
        : "";
    setStatus(
      data.total +
        " Treffer in " +
        scope +
        ", Filter «" +
        filter +
        "»" +
        pageNote +
        " — Speichern schreibt DB" +
        (document.getElementById("pushAfter").checked ? " + push-nc" : "") +
        "."
    );
  }

  function syncBrowseChrome() {
    const browse = isBrowseMode();
    document.getElementById("folderNav").classList.toggle("hidden", !browse);
    document.getElementById("filterWrap").classList.toggle("hidden", !browse);
    document.getElementById("retagWrap").classList.toggle("hidden", !browse);
    document.getElementById("browseRecursiveWrap").classList.toggle("hidden", !browse);
    document.getElementById("subfolders").classList.toggle("hidden", !browse);
    const blurBar = document.getElementById("blurSettingsBar");
    if (blurBar) blurBar.classList.toggle("hidden", !browse || !faceblurEnabled);
  }

  function reload() {
    syncBrowseChrome();
    if (isBrowseMode()) {
      return loadBrowse().catch((e) => setStatus(e.message, true));
    }
    return loadQueue().catch((e) => setStatus(e.message, true));
  }

  document.getElementById("viewMode").onchange = reload;
  document.getElementById("browseFilter").onchange = () => {
    browseOffset = 0;
    if (document.getElementById("browseFilter").value === "blurred") {
      document.getElementById("browseRecursive").checked = true;
    }
    if (isBrowseMode()) loadBrowse().catch((e) => setStatus(e.message, true));
  };
  document.getElementById("browseRecursive").onchange = () => {
    browseOffset = 0;
    if (isBrowseMode()) loadBrowse().catch((e) => setStatus(e.message, true));
  };
  document.getElementById("reloadBtn").onclick = reload;
  document.getElementById("tagFolderBtn").onclick = () =>
    startFolderTagJob().catch((e) => setStatus(e.message, true));

  syncBrowseChrome();
  reload();
  resumeBlurJob();
})();
