// Keep these versions aligned with requirements.txt and the verified Pyodide lock.
const PYODIDE_VERSION = "0.27.7";
const SYMPY_VERSION = "1.13.3";
const MPMATH_VERSION = "1.3.0";
const PYODIDE_INDEX_URL = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`;

let runtime = null;
let initializationError = null;
let calculating = false;

function status(message) {
  self.postMessage({ type: "status", message });
}

function errorText(error) {
  return String(error?.message || error || "原因不明のエラー")
    .replace(/\s+/g, " ")
    .slice(0, 700);
}

async function initialize() {
  status("計算エンジンを読み込んでいます。初回は時間がかかることがあります。");
  const { loadPyodide } = await import(`${PYODIDE_INDEX_URL}pyodide.mjs`);
  const pyodide = await loadPyodide({ indexURL: PYODIDE_INDEX_URL });
  if (pyodide.version !== PYODIDE_VERSION) {
    throw new Error("読み込まれた計算エンジンのバージョンが一致しません。ページを再読み込みしてください。");
  }

  status("数式計算の準備をしています。");
  await pyodide.loadPackage("sympy");
  const versions = JSON.parse(await pyodide.runPythonAsync(
    "import sys, json, sympy, mpmath\n" +
    "json.dumps({'python': sys.version.split()[0], 'sympy': sympy.__version__, 'mpmath': mpmath.__version__})"
  ));
  if (versions.sympy !== SYMPY_VERSION || versions.mpmath !== MPMATH_VERSION) {
    throw new Error("数式計算ライブラリのバージョンが一致しません。ページを再読み込みしてください。");
  }

  const kernelURL = new URL("./kernel.py", import.meta.url);
  const response = await fetch(kernelURL, { credentials: "same-origin" });
  if (!response.ok) {
    throw new Error(`計算プログラムを取得できませんでした（HTTP ${response.status}）。`);
  }
  // This is the application's own Python source, never a user's expression.
  await pyodide.runPythonAsync(await response.text(), { filename: "kernel.py" });
  runtime = pyodide;
  self.postMessage({
    type: "ready",
    versions: { pyodide: pyodide.version, ...versions },
  });
}

const initialized = initialize().catch((error) => {
  initializationError = errorText(error);
  self.postMessage({
    type: "error",
    message: `計算エンジンを準備できませんでした。通信環境を確認して再試行してください。 ${initializationError}`,
  });
});

async function handleMessage(message) {
  if (!message || message.type !== "calculate") return;
  const { id, request } = message;
  if (calculating) {
    self.postMessage({ type: "error", id, message: "別の計算を実行中です。終了または中止してから再実行してください。" });
    return;
  }
  calculating = true;
  try {
    await initialized;
    if (!runtime) throw new Error(initializationError || "計算エンジンの準備ができていません。");
    if (!request || typeof request !== "object" || Array.isArray(request)) {
      throw new Error("計算要求の形式が正しくありません。");
    }
    status("多項式を計算しています。");
    // JSON crosses the Python boundary as data. Do not interpolate it into code.
    runtime.globals.set("_polynomial_lab_request_json", JSON.stringify(request));
    const resultJSON = await runtime.runPythonAsync("calculate_json(_polynomial_lab_request_json)");
    const result = JSON.parse(resultJSON);
    self.postMessage({ type: "result", id, result });
  } catch (error) {
    self.postMessage({ type: "error", id, message: `計算を完了できませんでした。 ${errorText(error)}` });
  } finally {
    if (runtime) runtime.globals.delete("_polynomial_lab_request_json");
    calculating = false;
  }
}

self.addEventListener("message", (event) => {
  void handleMessage(event.data);
});
