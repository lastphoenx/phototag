(function () {
  const TOKEN_KEY = "phototag_admin_token";
  let tagCatalog = [];

  function token() {
    return sessionStorage.getItem(TOKEN_KEY) || "";
  }

  function setStatus(msg, isErr) {
    const el = document.getElementById("statusLine");
    el.textContent = msg;
    el.className = "status" + (isErr ? " err" : "");
  }

  function authHeaders(json) {
    const h = { "X-Phototag-Token": token() };
    if (json) h["Content-Type"] = "application/json";
    return h;
  }

  async function api(path, opts) {
    const res = await fetch(path, {
      ...opts,
      headers: { ...authHeaders(opts && opts.body), ...(opts && opts.headers) },
    });
    if (res.status === 401) {
      sessionStorage.removeItem(TOKEN_KEY);
      showLogin();
      throw new Error("Token ungültig");
    }
    const data = res.headers.get("content-type")?.includes("json") ? await res.json() : null;
    if (!res.ok) {
      throw new Error((data && data.error) || res.statusText);
    }
    return data;
  }

  async function loadThumb(img, id) {
    const res = await fetch("/api/images/" + id + "/thumb", {
      headers: authHeaders(false),
    });
    if (!res.ok) return;
    const blob = await res.blob();
    img.src = URL.createObjectURL(blob);
  }

  function showLogin() {
    document.getElementById("loginPanel").classList.remove("hidden");
    document.getElementById("queue").innerHTML = "";
  }

  function hideLogin() {
    document.getElementById("loginPanel").classList.add("hidden");
  }

  function renderTagEditor(container, initialTags, allowed) {
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
        s.title = "Entfernen";
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
    meta.textContent =
      "age_status: " +
      (item.age_status || "?") +
      (item.sanitize_flags && item.sanitize_flags.length
        ? "  ·  flags: " + item.sanitize_flags.join(", ")
        : "") +
      (item.error ? "  ·  " + item.error : "");

    const capLabel = document.createElement("label");
    capLabel.textContent = "Caption";
    const capArea = document.createElement("textarea");
    capArea.className = "caption-edit";
    capArea.rows = 2;
    capArea.value = item.caption || "";

    const tagBlock = document.createElement("div");
    const getTags = renderTagEditor(tagBlock, item.proposed_tags || [], tagCatalog);

    const actions = document.createElement("div");
    actions.className = "actions";

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

  document.getElementById("reloadBtn").onclick = () =>
    loadQueue().catch((e) => setStatus(e.message, true));

  document.getElementById("loginBtn").onclick = () => {
    const v = document.getElementById("tokenInput").value.trim();
    if (!v) return;
    sessionStorage.setItem(TOKEN_KEY, v);
    hideLogin();
    loadQueue().catch((e) => setStatus(e.message, true));
  };

  if (!token()) {
    showLogin();
  } else {
    hideLogin();
    loadQueue().catch((e) => setStatus(e.message, true));
  }
})();
