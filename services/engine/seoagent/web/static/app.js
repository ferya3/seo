// Theme toggle — remembered across sessions.
(function theme() {
  const saved = localStorage.getItem("seo-theme");
  if (saved) document.documentElement.dataset.theme = saved;

  document.addEventListener("click", (event) => {
    if (!event.target.closest("#theme-toggle")) return;
    const current =
      document.documentElement.dataset.theme ||
      (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    const next = current === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    localStorage.setItem("seo-theme", next);
  });
})();

// Tabs
document.querySelectorAll("[data-tabs]").forEach((group) => {
  const tabs = [...group.querySelectorAll(".tab")];
  tabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      tabs.forEach((t) => {
        const selected = t === tab;
        t.setAttribute("aria-selected", String(selected));
        const panel = document.getElementById(t.dataset.panel);
        if (panel) panel.hidden = !selected;
      });
    });
  });
});

// Issue severity / category filtering on the audit report
document.querySelectorAll("[data-filter-group]").forEach((group) => {
  const key = group.dataset.filterGroup;
  group.querySelectorAll(".chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      group.querySelectorAll(".chip").forEach((c) =>
        c.setAttribute("aria-pressed", String(c === chip))
      );
      const value = chip.dataset.value;
      document.querySelectorAll("[data-issue]").forEach((issue) => {
        issue.hidden = value !== "all" && issue.dataset[key] !== value;
      });
      const visible = [...document.querySelectorAll("[data-issue]")].filter((i) => !i.hidden).length;
      const counter = document.getElementById("issue-count");
      if (counter) counter.textContent = visible;
    });
  });
});

// Live keyword table filter
const keywordFilter = document.getElementById("kw-filter");
if (keywordFilter) {
  keywordFilter.addEventListener("input", () => {
    const needle = keywordFilter.value.trim().toLowerCase();
    let shown = 0;
    document.querySelectorAll("[data-kw-row]").forEach((row) => {
      const match = !needle || row.dataset.kwRow.includes(needle);
      row.hidden = !match;
      if (match) shown += 1;
    });
    const counter = document.getElementById("kw-count");
    if (counter) counter.textContent = shown;
  });
}

// ---------------------------------------------------------------- job runner

function runJob(form, endpoint, payloadFn) {
  const button = form.querySelector("button.primary");
  const progress = form.querySelector(".progress");
  const bar = form.querySelector(".bar > span");
  const msg = form.querySelector(".progress-text .msg");
  const pct = form.querySelector(".progress-text .pct");
  const errorBox = form.querySelector(".alert.error");

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (errorBox) errorBox.hidden = true;
    button.disabled = true;
    progress.hidden = false;
    bar.style.width = "2%";
    msg.textContent = "در حال شروع…";
    pct.textContent = "";

    let job;
    try {
      const response = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payloadFn(new FormData(form))),
      });
      job = await response.json();
      if (!response.ok) throw new Error(job.error || "درخواست رد شد.");
    } catch (err) {
      button.disabled = false;
      progress.hidden = true;
      if (errorBox) {
        errorBox.textContent = err.message;
        errorBox.hidden = false;
      }
      return;
    }

    const poll = setInterval(async () => {
      let state;
      try {
        state = await (await fetch(`/api/job/${job.id}`)).json();
      } catch {
        return; // transient; try again on the next tick
      }
      bar.style.width = `${Math.max(state.percent, 2)}%`;
      msg.textContent = state.message || "";
      pct.textContent = state.total ? `${state.done} / ${state.total}` : "";

      if (state.status === "done") {
        clearInterval(poll);
        window.location.href = `/report/${job.id}`;
      } else if (state.status === "error") {
        clearInterval(poll);
        button.disabled = false;
        progress.hidden = true;
        if (errorBox) {
          errorBox.textContent = state.error || "اجرا با خطا متوقف شد.";
          errorBox.hidden = false;
        }
      }
    }, 900);
  });
}

const auditForm = document.getElementById("audit-form");
if (auditForm) {
  runJob(auditForm, "/api/audit", (data) => ({
    url: data.get("url"),
    keywords: data.get("keywords"),
    max_pages: data.get("max_pages"),
    max_depth: data.get("max_depth"),
    user_agent: data.get("user_agent"),
    respect_robots: data.get("respect_robots") === "on",
    check_links: data.get("check_links") === "on",
    subdomains: data.get("subdomains") === "on",
    include_psi: data.get("include_psi") === "on",
    use_ai: data.get("use_ai") === "on",
  }));
}

const keywordForm = document.getElementById("keyword-form");
if (keywordForm) {
  runJob(keywordForm, "/api/keywords", (data) => ({
    seed: data.get("seed"),
    lang: data.get("lang"),
    country: data.get("country"),
    max_keywords: data.get("max_keywords"),
    questions: data.get("questions") === "on",
    alphabet: data.get("alphabet") === "on",
    comparisons: data.get("comparisons") === "on",
    sources: data.getAll("sources"),
    use_ai: data.get("use_ai") === "on",
  }));
}

// Copy-to-clipboard buttons
document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-copy]");
  if (!button) return;
  const target = document.querySelector(button.dataset.copy);
  if (!target) return;
  try {
    await navigator.clipboard.writeText(target.innerText.trim());
    const original = button.textContent;
    button.textContent = "کپی شد ✓";
    setTimeout(() => (button.textContent = original), 1600);
  } catch {
    button.textContent = "کپی نشد";
  }
});
