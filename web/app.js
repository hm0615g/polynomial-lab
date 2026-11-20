const $ = (id) => document.getElementById(id);
const form = $("calculator");
const input = $("polynomial");
const field = $("field");
const prime = $("prime");
const scope = $("root-scope");
const digits = $("digits");
const calculateButton = $("calculate");
const cancelButton = $("cancel");
const results = $("results");
const displayStorageKey = "polynomial-lab.display";
const INIT_TIMEOUT = 90000;
const CALCULATION_TIMEOUT = 30000;
let worker = null;
let ready = false;
let busy = false;
let jobId = 0;
let activeJob = null;
let initTimer = null;
let jobTimer = null;
let feedbackTimer = null;
let lastResult = null;
let expandedRoots = false;
let currentDisplay = "both";
let katexFailed = false;

const examples = {
  eisenstein: { polynomial: "x^4 - 4x^2 + 2", field: "Q", p: "2", root_scope: "all", title: "アイゼンシュタイン多項式" },
  quintic: { polynomial: "x^5 - x + 1", field: "Q", p: "2", root_scope: "all", title: "5次多項式" },
  finite: { polynomial: "x^3 + x + 1", field: "Fp", p: "2", root_scope: "all", title: "有限体の拡大体" },
  multiple: { polynomial: "(x - 1)^3 * (x + 2)^2", field: "Q", p: "2", root_scope: "all", title: "重根のある多項式" },
};

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function announce(text) {
  window.clearTimeout(feedbackTimer);
  $("feedback").textContent = "";
  feedbackTimer = window.setTimeout(() => {
    $("feedback").textContent = text;
    feedbackTimer = null;
  }, 30);
}

function setEngineStatus(message, status = "loading") {
  $("engine-status").textContent = message;
  $("runtime-state").dataset.state = status;
}

function setNote(message = "") {
  $("calculation-note").textContent = message;
  $("calculation-note").hidden = !message;
}

function markPreviousResult() {
  if (lastResult) $("result-context-note").hidden = false;
}

function updateControls() {
  calculateButton.disabled = !ready || busy;
  calculateButton.textContent = busy ? "計算しています…" : ready ? "根の計算・既約性判定" : "準備中…";
  cancelButton.hidden = !busy;
  for (const control of [input, field, scope]) control.disabled = busy;
  prime.disabled = busy || field.value !== "Fp";
  digits.disabled = busy || field.value !== "Q";
  document.querySelectorAll("[data-example]").forEach((button) => { button.disabled = busy; });
  $("result-panel").setAttribute("aria-busy", String(busy));
}

function updateField() {
  const previous = scope.value;
  scope.replaceChildren();
  const choices = field.value === "Q"
    ? [["all", "代数的閉包 — 全ての根"], ["real", "実数の根のみ"], ["base", "有理数の根のみ"]]
    : [["all", "代数的閉包 — 全ての根"], ["base", "F_p 内の根のみ"]];
  for (const [value, label] of choices) {
    const option = element("option", "", label);
    option.value = value;
    scope.append(option);
  }
  scope.value = choices.some(([value]) => value === previous) ? previous : "all";
  $("prime-field").hidden = field.value !== "Fp";
  $("precision-field").hidden = field.value !== "Q";
  updateControls();
}

function clearError() {
  $("error-message").hidden = true;
  $("error-message").textContent = "";
  for (const node of [input, prime, digits, scope]) {
    node.removeAttribute("aria-invalid");
    const description = (node.getAttribute("aria-describedby") || "").split(/\s+/).filter((id) => id && id !== "error-message").join(" ");
    if (description) node.setAttribute("aria-describedby", description);
    else node.removeAttribute("aria-describedby");
  }
}

function showError(message, target = null) {
  window.clearTimeout(feedbackTimer);
  feedbackTimer = null;
  $("feedback").textContent = "";
  clearError();
  $("error-message").textContent = message;
  $("error-message").hidden = false;
  const node = { polynomial: input, p: prime, digits, root_scope: scope }[target];
  if (node) {
    node.setAttribute("aria-invalid", "true");
    node.setAttribute("aria-describedby", `${node.getAttribute("aria-describedby") || ""} error-message`.trim());
    node.focus();
  }
}

function finishJob() {
  window.clearTimeout(jobTimer);
  jobTimer = null;
  activeJob = null;
  busy = false;
  if (ready) setEngineStatus("計算エンジン準備完了", "ready");
  updateControls();
}

function stopWorker() {
  window.clearTimeout(initTimer);
  window.clearTimeout(jobTimer);
  initTimer = null;
  jobTimer = null;
  if (worker) worker.terminate();
  worker = null;
  ready = false;
  activeJob = null;
  busy = false;
  updateControls();
}

function initializationFailed(message) {
  stopWorker();
  setEngineStatus("計算エンジンを読み込めませんでした", "error");
  $("retry-engine").hidden = false;
  calculateButton.textContent = "計算エンジンの準備が必要です";
  showError(message);
}

function startWorker() {
  stopWorker();
  $("retry-engine").hidden = true;
  setEngineStatus("計算エンジンを準備しています…");
  if (window.location.protocol === "file:") {
    initializationFailed("このアプリはWebサーバー経由で開いてください。ダウンロードした場合は、付属のREADMEにあるローカル起動手順を使えます。");
    return;
  }
  let current;
  try {
    current = new Worker(new URL("./worker.js", import.meta.url), { type: "module" });
    worker = current;
  } catch (error) {
    initializationFailed(`計算エンジンを起動できませんでした。ブラウザの設定と接続を確認し、再読み込みしてください。${error?.message ? `（${error.message}）` : ""}`);
    return;
  }
  initTimer = window.setTimeout(() => {
    if (worker === current && !ready) initializationFailed("計算エンジンの準備が90秒以内に終わりませんでした。インターネット接続を確認して「再読み込み」を押してください。");
  }, INIT_TIMEOUT);

  current.onmessage = (event) => {
    if (worker !== current) return;
    const message = event.data || {};
    if (message.type === "status") {
      setEngineStatus(message.message || "計算エンジンを準備しています…", ready ? "ready" : "loading");
    } else if (message.type === "ready") {
      window.clearTimeout(initTimer);
      initTimer = null;
      ready = true;
      setEngineStatus("計算エンジン準備完了", "ready");
      updateControls();
    } else if (message.type === "result" && message.id === activeJob) {
      finishJob();
      const result = message.result;
      if (!result?.ok) {
        showError(result?.error?.message || "計算を完了できませんでした。入力を確認してください。", result?.error?.field);
        return;
      }
      clearError();
      setNote();
      lastResult = result;
      renderResults(result);
      $("results-heading").focus();
      const total = result.summary?.total_multiplicity;
      announce(`計算が完了しました。${result.irreducibility?.label || ""}。${total === null || total === undefined ? "" : `重複度込みで${total}個の根を表示しています。`}`);
    } else if (message.type === "error") {
      if (message.id !== undefined && message.id !== null && message.id !== activeJob) return;
      if (!ready) {
        initializationFailed(message.message || "計算エンジンを読み込めませんでした。接続を確認して再読み込みしてください。");
      } else {
        finishJob();
        showError(message.message || "計算中にエラーが発生しました。式を確認して再度お試しください。");
      }
    }
  };

  current.onerror = (event) => {
    if (worker !== current) return;
    event.preventDefault();
    const duringCalculation = busy;
    initializationFailed(duringCalculation
      ? "計算エンジンが停止しました。入力を小さくして、再読み込み後にお試しください。"
      : "計算エンジンを読み込めませんでした。インターネット接続を確認し、最新のブラウザで再読み込みしてください。");
  };
}

function collectRequest() {
  const polynomial = input.value.trim();
  if (!polynomial) {
    showError("多項式を入力してください。例：x^4-4x^2+2", "polynomial");
    return null;
  }
  if (polynomial.length > 3000) {
    showError("多項式は3000文字以内で入力してください。", "polynomial");
    return null;
  }
  if (field.value === "Fp" && (!/^\d+$/.test(prime.value) || Number(prime.value) < 2 || Number(prime.value) > 2147483647)) {
    showError("pには2以上2147483647以下の素数を入力してください。", "p");
    return null;
  }
  const requestedDigits = Number(digits.value);
  if (field.value === "Q" && (!Number.isInteger(requestedDigits) || requestedDigits < 5 || requestedDigits > 50)) {
    showError("近似値の有効桁数は5から50までの整数で指定してください。", "digits");
    return null;
  }
  return { polynomial, field: field.value, p: prime.value, root_scope: scope.value, digits: field.value === "Q" ? requestedDigits : 15 };
}

function calculate(event) {
  event.preventDefault();
  if (!ready || busy || !worker) return;
  clearError();
  setNote();
  const request = collectRequest();
  if (!request) return;
  markPreviousResult();
  busy = true;
  activeJob = ++jobId;
  updateControls();
  announce("計算を開始しました。");
  const sentId = activeJob;
  jobTimer = window.setTimeout(() => {
    if (activeJob !== sentId) return;
    stopWorker();
    setNote("計算が30秒以内に終わらなかったため中止しました。結果は未完了です。式や求める範囲を変更して再計算できます。");
    announce("計算が時間制限に達したため中止しました。結果は未完了です。");
    startWorker();
  }, CALCULATION_TIMEOUT);
  worker.postMessage({ type: "calculate", id: sentId, request });
}

function formula(text, latex) {
  const container = element("div", "formula");
  const visual = element("div", "formula-visual");
  visual.setAttribute("aria-hidden", "true");
  visual.dataset.latex = latex || "";
  visual.dataset.fallback = text || "";
  renderMath(visual);
  const plain = element("div", "formula-text", text || "");
  container.append(visual, plain);
  return container;
}

function renderMath(node) {
  if (window.katex && node.dataset.latex) {
    try {
      window.katex.render(node.dataset.latex, node, { throwOnError: false, trust: false, output: "html", displayMode: false, strict: "warn" });
      return;
    } catch { /* The exact plain-text representation remains usable. */ }
  }
  node.textContent = node.dataset.fallback || "";
}

function setDisplay(mode) {
  if (!["text", "math", "both"].includes(mode)) mode = "both";
  currentDisplay = mode;
  document.body.dataset.display = mode;
  document.querySelectorAll('input[name="display"]').forEach((radio) => { radio.checked = radio.value === mode; });
  $("math-notice").hidden = !katexFailed || mode === "text";
  try { window.localStorage.setItem(displayStorageKey, mode); } catch { /* Local storage is optional. */ }
}

function section(title, className = "") {
  const node = element("section", `result-section ${className}`.trim());
  if (title) node.append(element("h3", "", title));
  return node;
}

function safeExtensionId(value) { return `extension-${String(value).replace(/[^a-zA-Z0-9_-]/g, "-")}`; }

function renderResults(result) {
  results.replaceChildren();
  results.hidden = false;
  $("empty-state").hidden = true;
  $("copy-toolbar").hidden = false;
  $("result-scope-tag").textContent = result.field.label;
  $("result-scope-tag").hidden = false;
  $("result-context-note").hidden = true;

  const intro = section(null);
  intro.append(element("p", "result-label", `${result.field.label} 上での多項式`));
  const normalized = element("div", "polynomial-result");
  normalized.append(formula(result.normalized.text, result.normalized.latex));
  intro.append(normalized);
  const decision = element("div", "decision-line");
  decision.append(element("span", "decision-label", "既約性"));
  decision.append(element("span", `decision-badge ${result.irreducibility.status}`, result.irreducibility.label));
  intro.append(decision);
  if (result.irreducibility.reason) intro.append(element("p", "decision-reason", result.irreducibility.reason));
  results.append(intro);

  const summary = section(null);
  const counts = element("dl", "summary-grid");
  const countItems = [
    ["多項式の次数", result.summary.degree, "定義なし"],
    ["異なる根の個数", result.summary.distinct, "個別に列挙しません"],
    ["重複度込みの個数", result.summary.total_multiplicity, "個別に列挙しません"],
  ];
  for (const [label, value, fallback] of countItems) {
    const item = element("div");
    item.append(element("dt", "", label), element("dd", value === null || value === undefined ? "special-count" : "", value ?? fallback));
    counts.append(item);
  }
  summary.append(counts);
  let coverage = result.summary.special || "";
  if (!coverage && result.summary.all_roots_found) coverage = `全根を確認しました。重複度の合計 ${result.summary.total_multiplicity} は次数 ${result.summary.degree} と一致しています。`;
  else if (!coverage) coverage = `${result.summary.scope_label}を表示しています。`;
  summary.append(element("p", `coverage-note${result.summary.all_roots_found ? "" : " neutral"}`, coverage));
  results.append(summary);

  const factorization = section("因数分解");
  factorization.append(formula(result.factorization.text, result.factorization.latex));
  results.append(factorization);

  const rootsSection = section(null);
  const rootHeading = element("div", "root-heading");
  rootHeading.append(element("h3", "", `根の一覧${result.summary.scope_label ? ` — ${result.summary.scope_label}` : ""}`));
  if (result.roots.some((root) => root.multiplicity > 1)) {
    const label = element("label", "expand-control");
    const checkbox = element("input");
    checkbox.type = "checkbox";
    checkbox.id = "expand-multiplicities";
    checkbox.checked = expandedRoots;
    checkbox.addEventListener("change", () => {
      expandedRoots = checkbox.checked;
      renderRootList(list, result);
      announce(expandedRoots ? "重複度分の行に展開しました。" : "同じ根をまとめて表示しました。");
    });
    label.append(checkbox, element("span", "", "重複度分に展開"));
    rootHeading.append(label);
  }
  rootsSection.append(rootHeading);
  const list = element("ol", "root-list");
  renderRootList(list, result);
  rootsSection.append(list);
  if (result.roots.length === 0) rootsSection.append(element("p", "no-roots", result.summary.special || "指定した範囲には根がありません。"));
  results.append(rootsSection);

  if (result.extensions.length) {
    const extensions = section("根を表す拡大体");
    for (const extension of result.extensions) {
      const item = element("div", "extension-item");
      item.id = safeExtensionId(extension.id);
      item.append(element("h4", "", extension.label));
      item.append(formula(extension.definition_text, extension.definition_latex));
      if (extension.modulus_text) {
        item.append(element("p", "extension-description", "この拡大体の定義多項式"));
        item.append(formula(extension.modulus_text, extension.modulus_latex));
      }
      extensions.append(item);
    }
    extensions.append(element("p", "field-hint", "各記号は、対応する既約因子で定義した拡大体の元です。異なる拡大体の根は、それぞれの表現で示しています。"));
    results.append(extensions);
  }
  if (result.warnings.length) {
    const warnings = section("計算について", "warning-section");
    const list = element("ul", "warning-list");
    result.warnings.forEach((warning) => list.append(element("li", "", warning)));
    warnings.append(list);
    results.append(warnings);
  }
}

function visibleRoots(result) {
  const list = [];
  for (const root of result.roots) {
    const occurrences = expandedRoots ? root.multiplicity : 1;
    for (let occurrence = 1; occurrence <= occurrences; occurrence++) list.push({ root, occurrence });
  }
  return list;
}

function renderRootList(list, result) {
  list.replaceChildren();
  visibleRoots(result).forEach(({ root, occurrence }, index) => {
    const item = element("li", "root-item");
    const title = element("h4", "root-title", `根 ${index + 1}`);
    const multiplicity = expandedRoots && root.multiplicity > 1
      ? `同じ根の ${occurrence} / ${root.multiplicity} 個目`
      : `重複度 ${root.multiplicity}`;
    title.append(element("span", "multiplicity-tag", multiplicity));
    item.append(title);
    const exact = element("div", "root-value-row");
    exact.append(element("span", "value-label", "厳密値"), formula(root.text, root.latex));
    item.append(exact);
    if (root.approx_text !== null && root.approx_text !== undefined && root.approx_text !== "") {
      const approximate = element("div", "root-value-row");
      approximate.append(element("span", "value-label", "近似値"), formula(root.approx_text, root.approx_latex));
      item.append(approximate);
    }
    if (root.field_label) {
      const context = element("p", "root-context", root.field_label);
      if (root.extension_id) {
        const link = element("a", "", "拡大体の定義");
        link.href = `#${safeExtensionId(root.extension_id)}`;
        context.append(link);
      }
      item.append(context);
    }
    list.append(item);
  });
}

function plainReport(result, latex = false) {
  const math = (text, source) => latex && source ? `$${source}$` : text;
  const lines = [
    "Polynomial Lab — 多項式の根・既約性",
    `係数体: ${result.field.label}`,
    `多項式: ${math(result.normalized.text, result.normalized.latex)}`,
    `次数: ${result.summary.degree ?? "定義なし"}`,
    `既約性: ${result.irreducibility.label}`,
    result.irreducibility.reason,
    `因数分解: ${math(result.factorization.text, result.factorization.latex)}`,
    `根の範囲: ${result.summary.scope_label}`,
    `異なる根の個数: ${result.summary.distinct ?? "個別に列挙しません"}`,
    `重複度込みの個数: ${result.summary.total_multiplicity ?? "個別に列挙しません"}`,
  ];
  if (result.summary.special) lines.push(result.summary.special);
  if (result.summary.all_roots_found) lines.push("全根を確認済み（重複度の合計と次数が一致）");
  lines.push("", "根:");
  if (!result.roots.length) lines.push(result.summary.special || "指定した範囲には根がありません。");
  result.roots.forEach((root, index) => {
    lines.push(`${index + 1}. ${math(root.text, root.latex)}（重複度 ${root.multiplicity}）`);
    if (root.approx_text !== null && root.approx_text !== undefined && root.approx_text !== "") lines.push(`   近似値: ${math(root.approx_text, root.approx_latex)}`);
    if (root.field_label) lines.push(`   ${root.field_label}`);
  });
  if (result.extensions.length) {
    lines.push("", "拡大体:");
    result.extensions.forEach((extension) => {
      lines.push(extension.label, math(extension.definition_text, extension.definition_latex));
      if (extension.modulus_text) lines.push(`定義多項式: ${math(extension.modulus_text, extension.modulus_latex)}`);
    });
  }
  if (result.warnings.length) lines.push("", "計算について:", ...result.warnings);
  return lines.filter((line) => line !== undefined && line !== null).join("\n");
}

async function copyReport(latex) {
  if (!lastResult) return;
  const text = plainReport(lastResult, latex);
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
    } else {
      const previous = document.activeElement;
      const area = element("textarea", "sr-only");
      area.value = text;
      area.setAttribute("aria-label", "コピーする計算結果");
      document.body.append(area);
      area.select();
      const copied = document.execCommand("copy");
      area.remove();
      previous?.focus();
      if (!copied) throw new Error("copy unavailable");
    }
    announce(latex ? "LaTeXソースをコピーしました。" : "計算結果をテキストでコピーしました。");
    const button = latex ? $("copy-latex") : $("copy-text");
    const original = button.textContent;
    button.textContent = "コピーしました";
    window.setTimeout(() => { button.textContent = original; }, 1800);
  } catch {
    announce("コピーできませんでした。テキスト表示に切り替えて、結果を選択してコピーしてください。");
    setNote("コピーできませんでした。テキスト表示の結果を選択し、コピーしてください。");
  }
}

form.addEventListener("submit", calculate);
field.addEventListener("change", () => { clearError(); updateField(); markPreviousResult(); });
for (const control of [input, prime, digits, scope]) control.addEventListener("input", markPreviousResult);
input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
    event.preventDefault();
    if (ready && !busy) form.requestSubmit();
  }
});
cancelButton.addEventListener("click", () => {
  if (!busy) return;
  stopWorker();
  setNote("計算を中止しました。計算エンジンの再準備後に、再計算できます。");
  announce("計算を中止しました。");
  startWorker();
  input.focus();
});
$("retry-engine").addEventListener("click", () => { clearError(); setNote(); startWorker(); });
document.querySelectorAll('input[name="display"]').forEach((radio) => {
  radio.addEventListener("change", () => { if (radio.checked) setDisplay(radio.value); });
});
document.querySelectorAll("[data-example]").forEach((button) => {
  button.addEventListener("click", () => {
    if (busy) return;
    const sample = examples[button.dataset.example];
    if (!sample) return;
    input.value = sample.polynomial;
    field.value = sample.field;
    prime.value = sample.p;
    updateField();
    scope.value = sample.root_scope;
    markPreviousResult();
    clearError();
    setNote();
    input.focus();
    announce(`${sample.title}を入力しました。計算ボタンで結果を確認できます。`);
  });
});
$("copy-text").addEventListener("click", () => copyReport(false));
$("copy-latex").addEventListener("click", () => copyReport(true));

function mathReady() {
  if (!window.katex) return;
  katexFailed = false;
  $("math-notice").hidden = true;
  document.querySelectorAll(".formula-visual").forEach(renderMath);
}
$("katex-script").addEventListener("load", mathReady);
$("katex-script").addEventListener("error", () => { katexFailed = true; setDisplay(currentDisplay); });
window.addEventListener("load", () => {
  if (window.katex) mathReady();
  else { katexFailed = true; setDisplay(currentDisplay); }
});

try { currentDisplay = window.localStorage.getItem(displayStorageKey) || "both"; } catch { /* Local storage is optional. */ }
setDisplay(currentDisplay);
updateField();
startWorker();
