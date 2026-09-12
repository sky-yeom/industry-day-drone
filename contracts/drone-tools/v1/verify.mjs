import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";

export function verifyContract() {
  const lock = JSON.parse(readFileSync(new URL("./contract.lock.json", import.meta.url)));
  for (const [file, expected] of Object.entries(lock.sha256)) {
    const bytes = readFileSync(new URL(file, import.meta.url));
    if (createHash("sha256").update(bytes).digest("hex") !== expected) {
      throw new Error(`Frozen v${lock.version} artifact changed: ${file}. Publish a new version instead.`);
    }
  }
  return lock.version;
}
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  console.log(`Drone Tools ${verifyContract()} contract matches its frozen artifacts.`);
}
