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
    const res = await fetch("/api/images/" + id + "/thumb", { credentials: "same-origin" });
    if (res.status === 401) {
      window.location.href = "/login";
      return;
    }
    if (!res.ok) return;
    const blob = await res.blob();
    img.src = URL.createObjectURL(blob);
  }

  async function loadThumbNc(img, ncPath) {
    const res = await fetch(
      "/api/browse/thumb?nc_path=" + encodeURIComponent(ncPath),
      { credentials: "same-origin" }
    );
    if (res.status === 401) {
      window.location.href = "/login";
      return;
    }
    if (!res.ok) return;
    const blob = await res.blob();
    img.src = URL.createObjectURL(blob);
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
    inp.placeholder = "Tag hinzufügen …";
    inp.setAttribute("list", "tagCatalog");
    const addBtn = document.createElement("button");
    addBtn.type = "button";
    addBtn.textContent = "+";
    addBtn.onclick = () => {
      const v = inp.value.trim().toLowerCase().replace(/\s+/g, "_");
      if (!v) return;
      if (allowed.length && !allowed.includes(v)) {
        setStatus("Unbekannter Tag: " + v, true);
        return;
      }
      state.add(v);
      inp.value = "";
      redraw();
    };
    inp.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        addBtn.click();
      }
    });
    addRow.append(inp, addBtn);
    wrap.append(chips, addRow);
    container.appendChild(wrap);
    return () => [...state];
  }

  function updateStatusBadge(badge, status) {
    const st = status || "unindexed";
    badge.textContent = st;
    badge.className =
      "status-badge" +
      (st === "review" ? " review" : st === "tagged" ? " tagged" : "");
  }

  function resetKiTagButton(ctx) {
    if (!ctx || !ctx.tagBtn) return;
    ctx.tagBtn.disabled = false;
    ctx.tagBtn.classList.remove("ki-queued", "ki-busy");
    ctx.tagBtn.textContent = "KI taggen";
    if (ctx.card) ctx.card.classList.remove("ki-busy", "ki-queued");
  }

  function markCardKiState(ctx, state) {
    if (!ctx || !ctx.tagBtn) return;
    if (state === "queued") {
      ctx.tagBtn.disabled = true;
      ctx.tagBtn.classList.add("ki-queued");
      ctx.tagBtn.textContent = "In Warteschlange …";
      ctx.card.classList.add("ki-queued");
    } else if (state === "running") {
      ctx.tagBtn.classList.remove("ki-queued");
      ctx.tagBtn.classList.add("ki-busy");
      ctx.card.classList.remove("ki-queued");
      ctx.card.classList.add("ki-busy");
      ctx.tagBtn.textContent = "KI läuft …";
    }
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
      try {
        const tags = getTags();
        if (!tags.length) {
          setStatus("Mindestens ein Tag nötig", true);
          return;
        }
        const push = document.getElementById("pushAfter").checked;
        await api("/api/review/" + item.id + "/approve?push=" + (push ? "1" : "0"), {
          method: "POST",
          body: JSON.stringify({ tags, caption: capArea.value.trim() }),
        });
        card.remove();
        setStatus("Freigegeben: " + item.nc_path);
      } catch (e) {
        setStatus(String(e.message || e), true);
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

  function queueKiTag(item, ctx) {
    const nc = item.nc_path;
    if (kiPendingNc.has(nc) || kiJobCards.has(nc)) return;
    markCardKiState(ctx, "queued");
    kiPendingNc.set(nc, ctx);
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
    entries.forEach(([p, ctx]) => kiJobCards.set(p, ctx));
    const retag = document.getElementById("tagRetag")?.checked;
    try {
      const data = await api("/api/tag/jobs", {
        method: "POST",
        body: JSON.stringify({ nc_paths, retag, recursive: false }),
      });
      if (!data.ok || !data.job || data.job.total === 0) {
        entries.forEach(([, ctx]) => resetKiTagButton(ctx));
        kiJobCards.clear();
        setStatus((data && data.error) || "Keine Dateien für KI-Job (Mount/Index?)", true);
        return;
      }
      kiJobRunning = true;
      setStatus("KI-Job " + data.job.id + ": " + data.job.total + " Datei(en) in Stapel");
      showJobPanel(data.job);
      pollTagJob(data.job.id, true);
    } catch (e) {
      entries.forEach(([, ctx]) => resetKiTagButton(ctx));
      kiJobCards.clear();
      setStatus(String(e.message || e), true);
    }
  }

  function onKiJobFinished(job) {
    kiJobRunning = false;
    kiJobCards.forEach((ctx) => resetKiTagButton(ctx));
    kiJobCards.clear();
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
    loadThumbNc(img, item.nc_path);

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
      (inReview ? " review" : st === "tagged" ? " tagged" : "");
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

    const actions = document.createElement("div");
    actions.className = "actions";

    const saveBtn = document.createElement("button");
    saveBtn.className = "ok";
    saveBtn.textContent = "Speichern";
    saveBtn.onclick = async () => {
      try {
        const tags = getTags();
        if (!tags.length) {
          setStatus("Mindestens ein Tag nötig", true);
          return;
        }
        const push = document.getElementById("pushAfter").checked;
        const res = await api("/api/browse/save?push=" + (push ? "1" : "0"), {
          method: "POST",
          body: JSON.stringify({
            nc_path: item.nc_path,
            tags,
            caption: capArea.value.trim(),
          }),
        });
        badge.textContent = "tagged";
        if (res.id) {
          card.dataset.id = res.id;
          item.id = res.id;
        }
        setStatus(
          (push ? "DB + NC: " : "DB: ") +
            item.nc_path +
            (res.pushed_tags ? " (" + res.pushed_tags + " Tags NC)" : "")
        );
      } catch (e) {
        setStatus(String(e.message || e), true);
      }
    };
    actions.append(saveBtn);

    const tagBtn = document.createElement("button");
    tagBtn.textContent = "KI taggen";
    const getTagsRef = { current: getTags };
    const kiCtx = {
      card,
      tagBtn,
      badge,
      capArea,
      tagBlock,
      getTagsRef,
    };
    tagBtn.onclick = () => queueKiTag(item, kiCtx);
    actions.append(tagBtn);

    appendReviewActions(actions, {
      item,
      card,
      badge,
      getTags: () => getTagsRef.current(),
      capArea,
    });

    body.append(path, meta, capLabel, capArea, tagBlock, actions);
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
    rootBtn.textContent = "OF";
    rootBtn.onclick = () => {
      browseFolder = "";
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
        loadBrowse();
      };
      box.appendChild(b);
    });
  }

  function showJobPanel(job, fromKiCards) {
    const el = document.getElementById("jobPanel");
    if (!job || job.status === "done" || job.status === "error") {
      if (job && (job.status === "done" || job.status === "error")) {
        el.classList.remove("hidden");
        el.innerHTML =
          "<strong>Tag-Job " +
          job.id +
          "</strong> " +
          job.status +
          " — " +
          job.ok_count +
          " ok, " +
          job.fail_count +
          " fehler (" +
          job.done +
          "/" +
          job.total +
          ")";
        if (fromKiCards) {
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
    el.innerHTML =
      "<strong>Tag-Job " +
      job.id +
      "</strong> " +
      job.status +
      " — " +
      job.done +
      "/" +
      job.total +
      (job.current_nc_path ? " · " + job.current_nc_path : "");
    if (fromKiCards && job.current_nc_path) {
      kiJobCards.forEach((ctx, nc) => {
        if (nc === job.current_nc_path) {
          markCardKiState(ctx, "running");
        } else if (ctx.tagBtn.classList.contains("ki-busy")) {
          resetKiTagButton(ctx);
          markCardKiState(ctx, "queued");
        }
      });
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
    const retag = document.getElementById("tagRetag").checked;
    const recursive = document.getElementById("tagRecursive").checked;
    if (!confirm("KI-Tagging für Ordner «" + (folder || "OF") + "» starten?")) return;
    const folderBtn = document.getElementById("tagFolderBtn");
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
      setStatus("Tag-Job gestartet: " + data.job.id + " (" + data.job.total + " Dateien)");
      showJobPanel(data.job);
      pollTagJob(data.job.id, false);
    } catch (e) {
      setStatus(String(e.message || e), true);
    } finally {
      folderBtn.disabled = false;
      folderBtn.classList.remove("ki-busy");
      folderBtn.textContent = "Ordner KI-taggen";
    }
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
    renderBreadcrumb();
    await loadSubfolders();
    const filter = document.getElementById("browseFilter").value;
    const data = await api(
      "/api/browse?folder=" +
        encodeURIComponent(browseFolder) +
        "&filter=" +
        encodeURIComponent(filter)
    );
    const q = document.getElementById("queue");
    q.innerHTML = "";
    const where = browseFolder ? browseFolder + "/" : "OF/";
    if (!data.items.length) {
      setStatus(
        "Keine Mediendateien in " +
          where +
          " (Filter: " +
          filter +
          "). Anderen Ordner wählen oder Mount prüfen."
      );
      return;
    }
    data.items.forEach((item) => q.appendChild(renderMountItem(item)));
    setStatus(
      data.total +
        " Datei(en) auf Mount in " +
        (browseFolder || "OF") +
        ", Filter «" +
        filter +
        "» — manuell taggen, Speichern schreibt DB" +
        (document.getElementById("pushAfter").checked ? " + push-nc" : "") +
        "."
    );
  }

  function syncBrowseChrome() {
    const browse = isBrowseMode();
    document.getElementById("folderNav").classList.toggle("hidden", !browse);
    document.getElementById("filterWrap").classList.toggle("hidden", !browse);
    document.getElementById("subfolders").classList.toggle("hidden", !browse);
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
    if (isBrowseMode()) loadBrowse().catch((e) => setStatus(e.message, true));
  };
  document.getElementById("reloadBtn").onclick = reload;
  document.getElementById("tagFolderBtn").onclick = () =>
    startFolderTagJob().catch((e) => setStatus(e.message, true));

  syncBrowseChrome();
  reload();
})();
