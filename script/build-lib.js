const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const { spawnSync } = require("child_process");

function fail(message) {
  console.error(message);
  process.exit(1);
}

function ensureFile(filePath, label) {
  if (!fs.existsSync(filePath) || !fs.statSync(filePath).isFile()) {
    fail(`Missing ${label}: ${filePath}`);
  }
}

function ensureDir(dirPath, label) {
  if (!fs.existsSync(dirPath) || !fs.statSync(dirPath).isDirectory()) {
    fail(`Missing ${label}: ${dirPath}`);
  }
}

function cleanDir(targetDir) {
  fs.rmSync(targetDir, { recursive: true, force: true });
}

function ensureEmptyDir(targetDir) {
  cleanDir(targetDir);
  fs.mkdirSync(targetDir, { recursive: true });
}

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    stdio: "inherit",
    shell: false,
    ...options,
  });
  if (result.status !== 0) {
    fail(`Command failed: ${command} ${args.join(" ")}`);
  }
}

function commandSucceeds(command, args, options = {}) {
  const result = spawnSync(command, args, {
    encoding: "utf8",
    shell: false,
    ...options,
  });
  return result.status === 0;
}

function buildOptionalQqNative(qqNativeDir) {
  const sourcePath = path.join(qqNativeDir, "qmc2_fast.c");
  const targetName = process.platform === "win32" ? "qmc2_fast.dll" : process.platform === "darwin" ? "libqmc2_fast.dylib" : "libqmc2_fast.so";
  const targetPath = path.join(qqNativeDir, targetName);
  const signaturePath = `${targetPath}.sha256`;
  const args = ["-O3", "-shared", "-o", targetPath, sourcePath];
  if (process.platform !== "win32") {
    args.splice(2, 0, "-fPIC");
    args.push("-lm");
  }
  const result = spawnSync("gcc", args, {
    encoding: "utf8",
    shell: false,
  });
  if (result.status !== 0) {
    console.warn("Skipping optional QQ qmc2 native build; gcc is unavailable or failed.");
    return null;
  }
  const digest = crypto.createHash("sha256").update(fs.readFileSync(targetPath)).digest("hex");
  fs.writeFileSync(signaturePath, `${digest}  ${targetName}\n`, "utf8");
  return targetPath;
}

function capture(command, args, options = {}) {
  const result = spawnSync(command, args, {
    encoding: "utf8",
    shell: false,
    ...options,
  });
  if (result.status !== 0) {
    fail((result.stderr || result.stdout || `Command failed: ${command}`).trim());
  }
  return (result.stdout || "").trim();
}

function parseArgs(argv) {
  const values = {};
  for (let index = 0; index < argv.length; index += 1) {
    const item = argv[index];
    if (!item.startsWith("--")) {
      continue;
    }
    const key = item.slice(2);
    const next = argv[index + 1];
    if (!next || next.startsWith("--")) {
      values[key] = "true";
      continue;
    }
    values[key] = next;
    index += 1;
  }
  return values;
}

function copyRecursive(sourceDir, targetDir) {
  if (fs.statSync(sourceDir).isFile()) {
    fs.mkdirSync(path.dirname(targetDir), { recursive: true });
    fs.copyFileSync(sourceDir, targetDir);
    return;
  }
  fs.mkdirSync(targetDir, { recursive: true });
  for (const entry of fs.readdirSync(sourceDir, { withFileTypes: true })) {
    const sourcePath = path.join(sourceDir, entry.name);
    const targetPath = path.join(targetDir, entry.name);
    if (entry.isDirectory()) {
      copyRecursive(sourcePath, targetPath);
    } else {
      fs.mkdirSync(path.dirname(targetPath), { recursive: true });
      fs.copyFileSync(sourcePath, targetPath);
    }
  }
}

function resolvePythonExe(rootDir) {
  const fromEnv = process.env.QKK_PYTHON_EXE && process.env.QKK_PYTHON_EXE.trim();
  if (fromEnv) {
    ensureFile(fromEnv, "python from QKK_PYTHON_EXE");
    return fromEnv;
  }
  const candidates = [
    path.join(rootDir, ".venv", "Scripts", "python.exe"),
    path.join(path.dirname(rootDir), "A_QKKd", ".venv", "Scripts", "python.exe"),
  ];
  for (const candidate of candidates) {
    if (fs.existsSync(candidate) && fs.statSync(candidate).isFile()) {
      return candidate;
    }
  }
  ensureFile(candidates[0], "venv python");
  return candidates[0];
}

function locateIscc() {
  const candidates = [
    process.env.ISCC_EXE,
    "C:\\Program Files (x86)\\Inno Setup 6\\ISCC.exe",
    "C:\\Program Files\\Inno Setup 6\\ISCC.exe",
  ].filter(Boolean);
  for (const candidate of candidates) {
    if (fs.existsSync(candidate) && fs.statSync(candidate).isFile()) {
      return candidate;
    }
  }
  try {
    const result = spawnSync("where.exe", ["ISCC.exe"], {
      encoding: "utf8",
      shell: false,
    });
    if (result.status !== 0) {
      return null;
    }
    const resolved = (result.stdout || "").trim();
    const first = resolved.split(/\r?\n/).map((item) => item.trim()).find(Boolean);
    if (first && fs.existsSync(first) && fs.statSync(first).isFile()) {
      return first;
    }
  } catch (error) {
    // Fall through to null when ISCC is not on PATH.
  }
  return null;
}

module.exports = {
  buildOptionalQqNative,
  capture,
  cleanDir,
  commandSucceeds,
  copyRecursive,
  ensureDir,
  ensureEmptyDir,
  ensureFile,
  fail,
  locateIscc,
  parseArgs,
  resolvePythonExe,
  run,
};
