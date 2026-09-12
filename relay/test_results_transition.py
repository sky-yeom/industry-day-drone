"""Exercise the real Home orchestration and sprite timeline with existing Node tools."""

from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("node") and (ROOT / "node_modules/typescript").exists(),
                     "Results transition requires the existing frontend dependencies")
class ResultsTransitionTests(unittest.TestCase):
    def test_terminal_handoff_readiness_and_reset(self):
        result = subprocess.run(["node", "-e", r"""
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const assert = require("node:assert/strict"), ts = require("typescript");
const React = require("react");
const values = [], deps = [], cleanups = [], pendingEffects = [];
let cursor = 0, frames = new Map(), nextFrame = 0, handlers, session, tree;
let reducedMotion = false, imageFailure = false;
const timers = new Map();
const timerDelays = new Map();
const browser = {
  setInterval:()=>1, clearInterval:()=>{},
  setTimeout:(fn,delay)=>{timers.set(++nextFrame,fn);timerDelays.set(nextFrame,delay);return nextFrame;},
  clearTimeout:id=>{timers.delete(id);timerDelays.delete(id);},
  matchMedia:()=>({get matches(){return reducedMotion;}}),
  Image:class {decode(){return imageFailure ? Promise.reject(new Error("missing sprite")) : Promise.resolve();}},
};
const same = (a,b) => a && b && a.length === b.length && a.every((v,i)=>Object.is(v,b[i]));
const react = {...React,
  useState(initial) {
    const index = cursor++;
    if (!(index in values)) values[index] = typeof initial === "function" ? initial() : initial;
    return [values[index], value => { values[index] = typeof value === "function" ? value(values[index]) : value; }];
  },
  useRef(initial) {
    const index = cursor++;
    return values[index] ??= {current: initial};
  },
  useCallback(fn, dependencies) {
    const index = cursor++;
    if (!same(deps[index], dependencies)) { values[index] = fn; deps[index] = dependencies; }
    return values[index];
  },
  useEffect(fn, dependencies) {
    const index = cursor++;
    if (!same(deps[index], dependencies)) {
      deps[index] = dependencies;
      pendingEffects.push(() => { cleanups[index]?.(); cleanups[index] = fn(); });
    }
  },
};
const components = new Map(), modules = new Map();
class VoiceSession {
  ready = [];
  constructor(h) { handlers = h; session = this; }
  async start() {}
  async stop() {}
  sendCommand() {}
  sendRouteIntroReady() {}
  markResultsReady(runId) { this.ready.push(runId); }
}
function load(filename) {
  if (modules.has(filename)) return modules.get(filename).exports;
  if (filename.endsWith(".json")) return JSON.parse(fs.readFileSync(filename,"utf8"));
  const module = {exports:{}};
  modules.set(filename,module);
  const code = ts.transpileModule(fs.readFileSync(filename,"utf8"), {
    compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX,esModuleInterop:true},
  }).outputText;
  vm.runInNewContext(code, {
    module,exports:module.exports,console,crypto,performance:{now:()=>100},
    requestAnimationFrame:fn=>{frames.set(++nextFrame,fn);return nextFrame;},
    cancelAnimationFrame:id=>frames.delete(id),
    window:browser,
    require(name) {
      if(name==="react")return react;
      if(name==="@/lib/voiceClient")return {VoiceSession,fetchRelayConfig:async()=>({visionReady:true})};
      if(name.startsWith("@/components/")) {
        if(!components.has(name)) {
          const Component = ()=>null;
          Component.displayName = name.split("/").at(-1);
          components.set(name,Component);
        }
        return components.get(name);
      }
      if(name.startsWith("@/")) {
        const base=path.resolve(name.slice(2));
        return load([base,base+".ts",base+".tsx"].find(p=>fs.existsSync(p)));
      }
      return require(name);
    },
  },{filename});
  return module.exports;
}
const Home=load(path.resolve("app/page.tsx")).default;
const initial={
  ...load(path.resolve("data/monitors.ts")).INITIAL_ROUTE_STATE,
  ...load(path.resolve("data/scenario.ts")).INITIAL_MISSION_STATE,
};
function find(name,node=tree) {
  if(!node)return null;
  if(Array.isArray(node))return node.map(n=>find(name,n)).find(Boolean);
  if(node.type?.displayName===name || node.type===name)return node;
  return find(name,node.props?.children ?? null);
}
function render() {
  cursor=0;
  tree=Home();
  const map=find("FlightPathMap");
  if(map)map.props.markerRef.current=map.props.boarded&&!map.props.departing
    ? {getBoundingClientRect:()=>({left:173,top:241,width:36,height:44})}:null;
  while(pendingEffects.length)pendingEffects.shift()();
}
function frame() { const work=[...frames.values()];frames.clear();work.forEach(fn=>fn(100));render(); }
function emit(patch) { handlers.onRouteState({...initial,runId:"run",revision:2,...patch});render(); }
function start() { render();find("GibbyIntroSequence").props.onReady(); }
function route() {
  emit({promptPhase:"confirmed",revision:2});
  find("GibbyMapTransition").props.onDone();render();
  handlers.onTranscript("agent","Generated but not yet played",false);render();
  assert.equal(find("GibbyRouteDock").props.agentText,"");
  handlers.onSpeechText("Actually playing");render();
  assert.equal(find("GibbyRouteDock").props.agentText,"Actually playing");
  emit({promptPhase:"confirmed",missionPhase:"flying",clockRunning:true,revision:3});
}
function reset() { find("ResultsPanel").props.onReset();render(); }
start();route();
const oldBoarded=find("GibbyDroneBoarding").props.onBoarded;
emit({promptPhase:"confirmed",missionPhase:"complete",revision:4});
assert.ok(find("GibbyDroneBoarding"),"early completion waits for outgoing boarding");
assert.equal(find("GibbyResultsTransition"),undefined);
oldBoarded();render();frame();
let transition=find("GibbyResultsTransition");
assert.ok(transition);
assert.equal(transition.props.origin.left,173);
assert.equal(transition.props.origin.top,241);
assert.equal(transition.props.celebrate,true);
assert.equal(find("FlightPathMap").props.departing,true);
assert.equal(find("GibbyDroneBoarding"),undefined);
assert.deepEqual(session.ready,[]);
const oldReady=transition.props.onReady;
oldReady();render();
assert.deepEqual(session.ready,["run"]);
oldReady();render();
assert.deepEqual(session.ready,["run"],"readiness is idempotent");
assert.equal(find("ResultsPanel").props.visible,false);
handlers.onResultsReveal();render();
assert.equal(find("ResultsPanel").props.visible,true);
emit({missionPhase:"complete",revision:5});
assert.deepEqual(session.ready,["run"],"duplicate terminal state does not restart");
reset();
oldReady();oldBoarded();render();
assert.ok(find("GibbyIntroSequence"),"callbacks from the previous scene cannot advance a reset");

start();route();
emit({missionPhase:"aborted",revision:1});
assert.ok(find("GibbyDroneBoarding"),"stale terminal revision is ignored");
emit({missionPhase:"aborted",revision:4});
transition=find("GibbyResultsTransition");
assert.equal(transition.props.origin,null);
assert.equal(transition.props.celebrate,false);
assert.equal(find("GibbyDroneBoarding"),undefined);
transition.props.onReady();render();reset();

start();route();
find("GibbyDroneBoarding").props.onBoarded();render();
emit({missionPhase:"aborted",revision:4});
transition=find("GibbyResultsTransition");
assert.ok(transition.props.origin);
assert.equal(transition.props.celebrate,false);
transition.props.onReady();render();
assert.deepEqual(session.ready,["run"]);
reset();

start();route();
find("GibbyDroneBoarding").props.onBoarded();render();
emit({missionPhase:"complete",revision:4});
const interruptedReady=find("GibbyResultsTransition").props.onReady;
find("button").props.onClick();render();
interruptedReady();render();
assert.deepEqual(session.ready,[],"reset during return cannot release old results audio");
assert.ok(find("GibbyIntroSequence"));

start();
emit({missionPhase:"complete",revision:4});
assert.ok(find("GibbyDroneBoarding"),"a terminal first snapshot cannot strand the opening screen");
find("GibbyDroneBoarding").props.onBoarded();render();frame();
assert.ok(find("GibbyResultsTransition"));

const sprites=load(path.resolve("lib/gibbyResultsSprite.ts"));
assert.equal(sprites.returnFrame(0,true).phase,"handoff");
assert.equal(sprites.returnFrame(100,true).phase,"flying");
const reversed=[1680,1980,2280,2580].map(t=>sprites.returnFrame(t,true).src);
assert.equal(JSON.stringify(reversed),JSON.stringify([...sprites.RETURN_BOARDING_FRAMES].reverse()));
assert.equal(sprites.returnFrame(2900,true).phase,"idle");
assert.equal(sprites.returnFrame(3130,true).phase,"celebrating");
assert.equal(sprites.returnFrame(3130,false).phase,"resting");
assert.equal(sprites.returnFrame(Infinity,true).phase,"resting");
for(const src of sprites.RESULTS_ASSETS)assert.ok(fs.existsSync(path.join("public",src)));
console.log("Return handoff, terminal ordering, readiness, aborts, reset and frame sequence passed.");

(async()=>{
  cleanups.forEach(fn=>fn?.());
  const Transition=load(path.resolve("components/GibbyResultsTransition.tsx")).default;
  function fresh(){
    cleanups.forEach(fn=>fn?.());
    values.length=deps.length=cleanups.length=pendingEffects.length=0;
    frames.clear();timers.clear();timerDelays.clear();
  }
  let readies=0, failures=0;
  let props={origin:{left:173,top:241,width:36,height:44},celebrate:true,
    onReady:()=>readies++,onError:()=>failures++};
  function renderTransition(){
    cursor=0;tree=Transition(props);
    while(pendingEffects.length)pendingEffects.shift()();
  }
  function advance(now){
    const work=[...frames.values()];frames.clear();work.forEach(fn=>fn(now));renderTransition();
  }
  fresh();renderTransition();
  await new Promise(resolve=>setImmediate(resolve));
  advance(100);
  assert.equal(tree.props["data-return-phase"],"handoff");
  advance(1000);
  assert.equal(tree.props["data-return-phase"],"flying");
  assert.match(tree.props.children.props.className,/gibby-results-sprite--flying/);
  advance(3400);
  assert.equal(tree.props["data-return-phase"],"celebrating");
  assert.equal(readies,0);
  advance(4400);
  assert.equal(tree.props["data-return-phase"],"resting");
  assert.doesNotMatch(tree.props.children.props.className,/gibby-results-sprite--flying/);
  assert.equal(readies,1);
  assert.equal(frames.size,0);

  fresh();readies=0;reducedMotion=true;renderTransition();
  await new Promise(resolve=>setImmediate(resolve));
  advance(100);
  assert.equal(tree.props["data-return-phase"],"resting");
  assert.equal(readies,1,"reduced motion advances without animation events");

  fresh();readies=0;reducedMotion=false;renderTransition();
  await new Promise(resolve=>setImmediate(resolve));
  advance(100);
  cleanups.forEach(fn=>fn?.());
  assert.equal(frames.size,0,"unmount cancels the live frame");
  assert.equal(timers.size,0,"unmount clears the asset deadline");
  assert.equal(readies,0);

  fresh();readies=0;imageFailure=true;renderTransition();
  await new Promise(resolve=>setImmediate(resolve));
  renderTransition();
  assert.equal(failures,1);
  assert.equal(readies,1);
  assert.equal(tree.props["data-return-phase"],"resting");
  console.log("Animated, reduced-motion, unmounted and failed-asset component lifecycles passed.");

  const Boarding=load(path.resolve("components/GibbyDroneBoarding.tsx")).default;
  let boarded=0;
  const onBoarded=()=>boarded++;
  function renderBoarding(){
    cursor=0;tree=Boarding({onBoarded:()=>onBoarded()});
    while(pendingEffects.length)pendingEffects.shift()();
  }
  function reachSeated(){
    fresh();renderBoarding();
    for(let step=0;tree.props["data-boarding-phase"]!=="seated";step++){
      assert.ok(step<20,"boarding reaches its final pose");
      const [id,callback]=timers.entries().next().value;
      timers.delete(id);callback();renderBoarding();
    }
    assert.equal(tree.props.children.props.children.props.src,"/gibby/drone-board-4.png");
  }
  reachSeated();
  assert.equal(boarded,0,"the final boarding sprite is visible before map handoff");
  const [holdId,finishHold]=timers.entries().next().value;
  assert.equal(timerDelays.get(holdId),800,"the corner pose holds for a visible beat");
  for(let tick=0;tick<10;tick++)renderBoarding();
  assert.ok(timers.has(holdId),"mission clock rerenders do not restart the seated hold");
  timers.delete(holdId);finishHold();renderBoarding();finishHold();
  assert.equal(boarded,1,"the handoff fires only once after the final pose");
  reachSeated();
  cleanups.forEach(fn=>fn?.());
  assert.equal(timers.size,0,"reset or abort cancels the seated hold");
  assert.equal(boarded,1,"unmount during the hold cannot advance the old mission");
  console.log("Final boarding pose, timed handoff and cancelled hold passed.");
})().catch(error=>{console.error(error);process.exitCode=1;});
"""], cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
