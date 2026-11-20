import { access, cp, mkdir, rm, writeFile } from "node:fs/promises";
import { constants } from "node:fs";
import { basename, dirname, extname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const source = resolve(root, "web");
const destination = resolve(root, "dist");
const required = ["index.html", "style.css", "app.js", "worker.js", "kernel.py"];

try {
  for (const file of required) {
    await access(resolve(source, file), constants.R_OK);
  }
  await rm(destination, { recursive: true, force: true });
  await mkdir(destination, { recursive: true });
  await cp(source, destination, {
    recursive: true,
    filter(path) {
      return basename(path) !== "__pycache__" && ![".pyc", ".pyo"].includes(extname(path));
    },
  });
  await writeFile(resolve(destination, ".nojekyll"), "", "utf8");
  console.log("静的アプリを dist/ に作成しました。");
} catch (error) {
  console.error(`ビルドできませんでした: ${error.message}`);
  process.exitCode = 1;
}
