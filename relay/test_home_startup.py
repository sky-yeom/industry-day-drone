"""Render the real landing screen with the existing React and TypeScript dependencies."""

from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("node") and (ROOT / "node_modules/typescript").exists(),
                     "Startup contract requires the existing frontend dependencies")
class HomeStartupTests(unittest.TestCase):
    def test_initial_render_ignores_legacy_debug_overrides(self):
        result = subprocess.run(["node", "-e", r"""
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const assert = require("node:assert/strict");
const ts = require("typescript");
const React = require("react");
const {renderToStaticMarkup} = require("react-dom/server");

for (const step of [undefined, "route", "map-intro", "images", "results", "invalid"]) {
  const env = {...process.env};
  delete env.NEXT_PUBLIC_DEBUG_STEP;
  delete env.NEXT_PUBLIC_DEBUG_ROUTE;
  if (step !== undefined) {
    env.NEXT_PUBLIC_DEBUG_STEP = step;
    env.NEXT_PUBLIC_DEBUG_ROUTE = "1";
  }
  const initialized = [];
  const react = {...React, useState(initial) {
    const state = React.useState(initial);
    initialized.push(state[0]);
    return state;
  }};
  const cache = new Map();
  const realComponents = new Set([
    "@/components/GibbyIntroSequence",
    "@/components/PixelGround",
  ]);
  const unusedComponent = Object.assign(() => null, {MissionCountdownSummary: () => null});
  const voice = {
    fetchRelayConfig: async () => null,
    VoiceSession: class {
      constructor() { throw new Error("Rendering the landing card must not start a session"); }
    },
  };
  function load(filename) {
    if (cache.has(filename)) return cache.get(filename).exports;
    if (filename.endsWith(".json")) return JSON.parse(fs.readFileSync(filename, "utf8"));
    const module = {exports: {}};
    cache.set(filename, module);
    const code = ts.transpileModule(fs.readFileSync(filename, "utf8"), {
      compilerOptions: {
        module: ts.ModuleKind.CommonJS,
        target: ts.ScriptTarget.ES2022,
        jsx: ts.JsxEmit.ReactJSX,
        esModuleInterop: true,
      },
    }).outputText;
    vm.runInNewContext(code, {
      module, exports: module.exports, process: {env}, console,
      require(name) {
        if (name === "react") return react;
        if (name === "next/font/google") return {Press_Start_2P: () => ({className: "test-font"})};
        if (name === "@/lib/voiceClient") return voice;
        if (name.startsWith("@/components/") && !realComponents.has(name)) return unusedComponent;
        if (name.startsWith("@/")) {
          const base = path.resolve(name.slice(2));
          const resolved = [base, `${base}.ts`, `${base}.tsx`].find(file => fs.existsSync(file));
          assert.ok(resolved, `Cannot resolve ${name}`);
          return load(resolved);
        }
        return require(name);
      },
    }, {filename});
    return module.exports;
  }
  const Home = load(path.resolve("app/page.tsx")).default;
  for (let reload = 0; reload < 2; reload++) {
    initialized.length = 0;
    const html = renderToStaticMarkup(React.createElement(Home));
    assert.equal(initialized[0], "opening", `startup step with debug=${step}`);
    assert.match(html, /Let(?:'|&#x27;|&#39;)s Go!/);
    const floor = html.match(/<div[^>]+class="pixel-scene-ground[^"]*"[^>]*>/)?.[0];
    assert.ok(floor);
    assert.match(floor, /style="[^"]*height:calc\(16% \+ 35px\)/);
    assert.match(floor, /z-index:50/);
    assert.match(floor, /animation-play-state:paused/);
    assert.match(floor, /pixel-scene-ground--scrolling/);
    const snapshot = initialized.find(value => value && typeof value === "object" && "receivedAt" in value);
    assert.ok(snapshot);
    assert.equal(snapshot.receivedAt, 0);
    assert.equal(snapshot.state.phase, "selecting-destinations");
    assert.equal(snapshot.state.promptPhase, "briefing");
    assert.equal(snapshot.state.missionPhase, "briefing");
    assert.equal(snapshot.state.draftRoute.length, 0);
    assert.equal(snapshot.state.confirmedRoute.length, 0);
    assert.equal(snapshot.state.captures.length, 0);
    assert.equal(snapshot.state.runId, "");
    assert.equal(snapshot.state.elapsedMs, 0);
    assert.equal(snapshot.state.clockRunning, false);
    assert.equal(snapshot.state.score, null);
  }
  const PixelGround = load(path.resolve("components/PixelGround.tsx")).default;
  const movingFloor = renderToStaticMarkup(React.createElement(PixelGround, {scrolling: true}));
  assert.match(movingFloor, /animation-play-state:running/);
  assert.match(movingFloor, /pixel-scene-ground--scrolling/);
}
console.log("Landing-card startup and fresh defaults survive legacy debug flags and repeated renders.");
"""], cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
