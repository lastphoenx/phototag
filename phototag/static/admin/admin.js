(function () {
  let tagCatalog = [];
  let browseFolder = "";

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

  function renderItem(item) {
    const inReview = item.in_review || item.status === "review";
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
    if (!inReview) {
      capArea.readOnly = true;
    }

    const tagBlock = document.createElement("div");
    const tagList = item.proposed_tags || item.tags || [];
    const getTags = renderTagEditor(tagBlock, tagList, tagCatalog, !inReview);

    const actions = document.createElement("div");
    actions.className = "actions";

    if (inReview) {
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
        "Keine indexierten Dateien in " +
          where +
          " (Filter: " +
          filter +
          "). In Unterordner wechseln oder: phototag scan auf PHOTOTAG_SCAN_ROOT."
      );
      return;
    }
    data.items.forEach((item) => q.appendChild(renderItem(item)));
    setStatus(
      data.total +
        " Datei(en) in " +
        (browseFolder || "OF") +
        ", Filter «" +
        filter +
        "» — " +
        data.count +
        " angezeigt."
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

  syncBrowseChrome();
  reload();
})();
