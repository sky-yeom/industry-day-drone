"""Exercise results rendering and measured pagination with existing frontend dependencies."""

from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("node") and (ROOT / "node_modules/typescript").exists(),
                     "Results layout contract requires the existing frontend dependencies")
class ResultsLayoutTests(unittest.TestCase):
    def test_reveal_and_measured_content_fit(self):
        result = subprocess.run(["node", "-e", r"""
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const assert = require("node:assert/strict");
const ts = require("typescript");
const React = require("react");
const {renderToStaticMarkup} = require("react-dom/server");

function loader(react = React, browser = {}) {
  const cache = new Map();
  function load(filename) {
    if (cache.has(filename)) return cache.get(filename).exports;
    if (filename.endsWith(".json")) return JSON.parse(fs.readFileSync(filename, "utf8"));
    const module = {exports: {}};
    cache.set(filename, module);
    const code = ts.transpileModule(fs.readFileSync(filename, "utf8"), {
      compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
        jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true},
    }).outputText;
    vm.runInNewContext(code, {module, exports: module.exports, console, ...browser,
      require(name) {
        if (name === "react") return react;
        if (name.startsWith("@/")) {
          const base = path.resolve(name.slice(2));
          return load([base, `${base}.ts`, `${base}.tsx`].find(file => fs.existsSync(file)));
        }
        return require(name);
      },
    }, {filename});
    return module.exports;
  }
  return load;
}

const ResultsPanel = loader()(path.resolve("components/ResultsPanel.tsx")).default;
const state = {
  missionPhase: "complete", score: {total: 3, rescuedCount: 2},
  people: [1, 2, 3].map(id => ({
    id: `person-${id}`, monitorId: `monitor-${id}`, label: `사람 ${id}`, outcome: null,
  })),
  userPromptText: "확정 프롬프트",
};
const renderResults = visible => renderToStaticMarkup(React.createElement(
  ResultsPanel, {state, debrief: "작전 설명", onReset() {}, visible}
));
for (const visible of [undefined, true, false]) {
  const html = renderResults(visible);
  const reveal = html.indexOf('transition-opacity');
  assert.ok(reveal > html.indexOf("처음으로"), "reset remains outside reveal");
  for (const text of ["3명 중 2명 구조", "사람 1", "사람 2", "사람 3",
                      "최종 작전 설명", "확정한 탐지 프롬프트"]) {
    assert.ok(html.indexOf(text) > reveal, `${text} belongs to the shared reveal`);
  }
  assert.match(html, /motion-reduce:transition-none/);
  assert.match(html, /grid-rows-2 sm:grid-cols-2 sm:grid-rows-1/);
  assert.match(html, /grid min-h-0 flex-1 items-start/);
  assert.doesNotMatch(html, /overflow-(auto|scroll)|pixel-bubble/);
  if (visible === false) assert.match(html, /aria-hidden="true" inert=""[^>]*opacity-0/);
  else assert.match(html, /opacity-100/);
}

const panel = {clientWidth: 375, clientHeight: 350};
const panelHooks = [];
let panelCursor = 0, resizePanel;
const panelReact = {...React,
  useRef: () => ({current: panel}),
  useCallback: callback => callback,
  useEffect(effect) { if (!resizePanel) effect(); },
  useState(initial) {
    const index = panelCursor++;
    if (!(index in panelHooks)) panelHooks[index] = initial;
    return [panelHooks[index], update => {
      panelHooks[index] = typeof update === "function" ? update(panelHooks[index]) : update;
    }];
  },
};
const MeasuredResults = loader(panelReact, {ResizeObserver: class {
  constructor(callback) { resizePanel = callback; }
  observe() {}
  disconnect() {}
}})(path.resolve("components/ResultsPanel.tsx")).default;
function renderPanel() {
  panelCursor = 0;
  return MeasuredResults({state, debrief: "설명", onReset() {}});
}
function find(element, predicate) {
  if (!element || typeof element !== "object") return;
  if (predicate(element)) return element;
  for (const child of [element.props?.children].flat(Infinity)) {
    const match = find(child, predicate);
    if (match) return match;
  }
}
renderPanel();
let panelTree = renderPanel();
const grid = element => typeof element.props?.className === "string"
  && element.props.className.startsWith("grid min-h-0 flex-1");
assert.match(find(panelTree, grid).props.className, /grid-cols-2 grid-rows-1/,
  "short narrow containers use two readable columns instead of overflowing stacked rows");
panel.clientHeight = 600; resizePanel(); panelTree = renderPanel();
assert.match(find(panelTree, grid).props.className, /grid-cols-1 grid-rows-2 sm:/);
panel.clientWidth = 1024; panel.clientHeight = 257; resizePanel(); panelTree = renderPanel();
const banner = find(panelTree, element => element.type === "div"
  && element.props.children?.[0]?.props?.children === "구조 작전 종료");
assert.match(banner.props.className, /w-full.*flex-col/,
  "the summary stays a full-width, two-line banner even in short wide regions");
assert.equal(banner.props.children[1].props.children, "3명 중 2명 구조");
assert.ok(find(panelTree, element => element.props?.className === "grid shrink-0 grid-cols-3 items-start gap-2"),
  "the three people stay in their own row below the summary");
assert.doesNotMatch(panelTree.props.className, /sm:p-3/);
assert.match(find(panelTree, element => element.type === "button").props.className, /py-1 leading-5/);
const card = find(panelTree, element => element.props?.title === "최종 작전 설명");
assert.equal(card.props.compact, true, "short-wide text cards trim chrome, never their readable text");
card.props.onSpaceChange(card.props.label, true);
assert.ok(find(renderPanel(), element => element.props?.role === "status"),
  "impossible card budgets report the viewport constraint rather than silently clipping results");
card.props.onSpaceChange(card.props.label, false);
assert.equal(find(renderPanel(), element => element.props?.role === "status"), undefined);
card.props.onHeightChange("최종 작전 설명", 120);
card.props.onHeightChange("결과의 탐지 프롬프트", 80);
for (const title of ["최종 작전 설명", "확정한 탐지 프롬프트"]) {
  assert.equal(find(renderPanel(), element => element.props?.title === title).props.sharedHeight, 120,
    "both text cards use the larger natural height instead of stretching to fill the screen");
}
card.props.onHeightChange("최종 작전 설명", 60);
assert.equal(find(renderPanel(), element => element.props?.title === "최종 작전 설명").props.sharedHeight, 80,
  "paired cards can shrink again after text or viewport changes");

// A tiny hook/DOM harness runs the actual effect and handlers. Layout metrics model
// wrapping at fixed readable line-height, including Korean, emoji and explicit newlines.
function pagination(props, width = 100, fixedHeight = 72) {
  let cursor = 0, dirty = false, tree, lineHeight = 24;
  const hooks = [], pending = [], observers = [], fontReady = [], fontEvents = new Map();
  const body = {clientWidth: width, clientHeight: fixedHeight};
  const measure = {textContent: "", getBoundingClientRect() {
    const capacity = Math.max(1, Math.floor(body.clientWidth / 10));
    const paragraphs = this.textContent.split("\n");
    // CSS pre-wrap does not create an extra line box after a trailing newline.
    if (this.textContent.endsWith("\n")) paragraphs.pop();
    const lines = this.textContent ? paragraphs.reduce(
      (sum, line) => sum + Math.max(1, Math.ceil(Array.from(line).length / capacity)), 0
    ) : 0;
    return {height: lines * lineHeight};
  }};
  const pager = {getBoundingClientRect: () => ({height: 24})};
  const elements = [body, measure, pager];
  let refIndex = 0;
  const react = {...React,
    useRef() {
      const index = cursor++;
      if (!hooks[index]) hooks[index] = {current: elements[refIndex++]};
      return hooks[index];
    },
    useState(initial) {
      const index = cursor++;
      if (!hooks[index]) hooks[index] = {value: typeof initial === "function" ? initial() : initial};
      return [hooks[index].value, update => {
        const value = typeof update === "function" ? update(hooks[index].value) : update;
        if (!Object.is(value, hooks[index].value)) { hooks[index].value = value; dirty = true; }
      }];
    },
    useEffect(effect, deps) {
      const index = cursor++;
      const old = hooks[index];
      if (!old || deps.some((value, i) => !Object.is(value, old.deps[i]))) {
        pending.push(() => {
          old?.cleanup?.();
          hooks[index] = {deps, cleanup: effect()};
        });
      }
    },
  };
  const browser = {
    ResizeObserver: class {
      constructor(callback) { this.callback = callback; observers.push(this); }
      observe() {}
      disconnect() { this.disconnected = true; }
    },
    document: {fonts: {
      ready: {then(callback) { fontReady.push(callback); }},
      addEventListener(name, callback) { fontEvents.set(name, callback); },
      removeEventListener(name, callback) {
        if (fontEvents.get(name) === callback) fontEvents.delete(name);
      },
    }},
  };
  const PagedText = loader(react, browser)(path.resolve("components/PagedText.tsx")).default;
  function flush() {
    let renders = 0;
    do {
      assert.ok(++renders < 20, "pagination must settle without a resize feedback loop");
      dirty = false; cursor = 0;
      tree = PagedText(props);
      pending.splice(0).forEach(effect => effect());
    } while (dirty);
    return tree;
  }
  const pageBody = () => tree.props.children[0];
  const nav = () => tree.props.children[1];
  const visibleText = () => pageBody().props.children[0].props.children;
  const navigate = direction => { nav().props.children[direction === "next" ? 2 : 0].props.onClick(); flush(); };
  const resize = (nextWidth, nextHeight) => {
    body.clientWidth = nextWidth; body.clientHeight = nextHeight;
    observers.filter(observer => !observer.disconnected).forEach(observer => observer.callback());
    flush();
  };
  flush();
  return {
    get tree() { return tree; }, pageBody, nav, visibleText, navigate, resize,
    update(next) { props = {...props, ...next}; flush(); },
    font(height) { lineHeight = height; fontReady.splice(0).forEach(callback => callback());
      fontEvents.get("loadingdone")?.(); flush(); },
    collect() {
      const record = () => {
        measure.textContent = visibleText();
        assert.ok(measure.getBoundingClientRect().height <=
          (pageBody().props.style?.height ?? body.clientHeight), "each page fits without clipping");
        measure.textContent = "";
        return visibleText();
      };
      const chunks = [record()];
      let count = 0;
      while (!nav().props.children[2].props.disabled) {
        assert.ok(++count < 1000);
        navigate("next"); chunks.push(record());
      }
      return chunks;
    },
    cleanup() {
      hooks.forEach(hook => hook?.cleanup?.());
      assert.ok(observers.every(observer => observer.disconnected));
      assert.equal(fontEvents.size, 0);
      fontReady.forEach(callback => callback());
    },
  };
}

const short = pagination({label: "짧은 설명", text: "구조 완료", contentFit: true, maxHeight: 150});
assert.equal(short.pageBody().props.style.height, 24, "short content owns only its natural height");
assert.match(short.nav().props.className, /invisible absolute/, "no reserved pager space for one page");
assert.equal(short.nav().props["aria-hidden"], true);
const text = "긴한국어설명과🚁확인 ".repeat(25) + "\n줄바꿈도 보존합니다.";
// A compact card still needs a readable line plus its pager below the full-width summary.
const shortWindowBudget = 52;
const shortWindow = pagination({label: "짧은 창", text, contentFit: true, maxHeight: shortWindowBudget}, 450);
assert.equal(shortWindow.pageBody().props.style.height, 24, "compact pagination affords one readable line and a pager");
assert.equal(shortWindow.collect().join(""), text);
const long = pagination({label: "긴 설명", text, contentFit: true, maxHeight: 100});
assert.equal(long.pageBody().props.style.height, 72, "pager and gap are deducted from budget");
assert.equal(long.nav().props["aria-hidden"], false);
assert.equal(long.collect().join(""), text, "all Korean/emoji/newline content is reachable");
long.navigate("previous");
const current = long.visibleText();
long.resize(100, 5);
assert.equal(long.visibleText(), current, "content-driven body resize must not reset pagination");
long.resize(50, 5);
assert.equal(long.nav().props.children[0].props.disabled, true, "width change resets to first page");
assert.equal(long.collect().join(""), text);
long.update({maxHeight: 52});
assert.equal(long.pageBody().props.style.height, 24);
assert.equal(long.collect().join(""), text, "one line plus pager works in short containers");
long.update({text: "짧음", maxHeight: 100});
assert.equal(long.visibleText(), "짧음");
assert.equal(long.pageBody().props.style.height, 24);
assert.match(long.nav().props.className, /invisible absolute/);
long.font(32);
assert.equal(long.pageBody().props.style.height, 32, "font readiness triggers a fresh measurement");
long.update({text, maxHeight: 40});
assert.equal(long.pageBody().props.children[0].props.role, "status", "impossible budgets are explicit");
long.update({maxHeight: 100});
assert.equal(long.collect().join(""), text, "pagination recovers after more space is available");
assert.equal(short.pageBody().props.style.height, 24, "independent cards do not share page heights");

const fixed = pagination({label: "기존 호출", text}, 100, 48);
assert.match(fixed.tree.props.className, /h-full/);
assert.match(fixed.pageBody().props.className, /flex-1/);
assert.equal(fixed.collect().join(""), text, "legacy externally-sized callers still paginate");
fixed.update({text: "짧음"});
assert.match(fixed.nav().props.className, /invisible/);
assert.doesNotMatch(fixed.nav().props.className, /absolute/, "legacy mode retains pager reservation");
short.cleanup(); shortWindow.cleanup(); long.cleanup(); fixed.cleanup();
console.log("Results reveal, equal card sizing, top banner, pagination, resize/fonts and legacy mode passed.");
"""], cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
