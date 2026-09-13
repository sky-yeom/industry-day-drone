"""Exercise the existing TypeScript client using installed Node/TypeScript, no browser or cloud."""

from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("node") and (ROOT / "node_modules/typescript").exists(),
                     "Client contract requires the existing frontend dependencies")
class VoiceClientTests(unittest.TestCase):
    def test_worklet_preserves_short_capture_and_discards_interrupted_drains(self):
        result = subprocess.run(["node", "-e", r"""
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
const processors = {};
class AudioWorkletProcessor {
  port = {messages:[], postMessage(message){this.messages.push(message);}};
}
vm.runInNewContext(fs.readFileSync("public/audio-worklets.js", "utf8"), {
  AudioWorkletProcessor, Float32Array, Int16Array,
  registerProcessor:(name, processor)=>{processors[name]=processor;},
});
const capture = new processors["capture-processor"]();
const input = (length, value)=>capture.process([[new Float32Array(length).fill(value)]]);
input(100, 0.5);
capture.port.onmessage({data:{type:"mute",value:true}});
input(480, 0.5);
assert.equal(capture.port.messages.length, 0);
capture.port.onmessage({data:{type:"mute",value:false}});
input(240, 0.25);
assert.equal(capture.port.messages.length, 0);
input(240, 0.25);
assert.equal(capture.port.messages.length, 1);
assert.equal(capture.port.messages[0].pcm.length, 480);
assert.ok(capture.port.messages[0].pcm.every(sample=>sample===8191));

const playback = new processors["playback-processor"]();
const send = data=>playback.port.onmessage({data});
send({type:"push",id:"interrupted",pcm:new Int16Array(960).fill(16000)});
send({type:"drain",id:"interrupted"});
playback.process([], [[new Float32Array(128)]]);
send({type:"flush"});
send({type:"push",id:"current",pcm:new Int16Array(480).fill(8000)});
send({type:"drain",id:"current"});
const output = new Float32Array(480);
playback.process([], [[output]]);
assert.ok(output.every(sample=>sample===8000/32768));
assert.deepEqual(playback.port.messages.filter(m=>m.type==="drained").map(m=>m.id), ["current"]);
assert.deepEqual(playback.port.messages.filter(m=>m.type==="started").map(m=>m.id), ["interrupted","current"]);
const silence = new Float32Array(128).fill(1);
playback.process([], [[silence]]);
assert.ok(silence.every(sample=>sample===0));
send({type:"push",id:"single-block",pcm:new Int16Array([8000])});
send({type:"drain",id:"single-block"});
assert.equal(playback.port.messages.filter(m=>m.id==="single-block").length,0,
             "queued PCM must not report playback");
playback.process([], [[new Float32Array(128)]]);
assert.deepEqual(playback.port.messages.filter(m=>m.id==="single-block").map(m=>m.type), ["started","drained"]);

const held = new processors["playback-processor"]();
const hold = data=>held.port.onmessage({data});
const pcm=Int16Array.from({length:960},(_,i)=>i-480);
hold({type:"push",id:"route",pcm:pcm.slice(0,480)});
const first=new Float32Array(128);
held.process([],[[first]]);
hold({type:"pause",value:true});
hold({type:"push",id:"route",pcm:pcm.slice(480)});
hold({type:"drain",id:"route"});
const paused=new Float32Array(128).fill(1);
held.process([],[[paused]]);
assert.ok(paused.every(sample=>sample===0));
assert.equal(held.port.messages.filter(m=>m.type==="drained").length,0,"a pause is not completion");
hold({type:"pause",value:false});
const tail=new Float32Array(832);
held.process([],[[tail]]);
assert.deepEqual([...first,...tail],Array.from(pcm,sample=>sample/32768),"noise must not discard the final sentence");
assert.equal(held.port.messages.filter(m=>m.type==="drained"&&m.id==="route").length,1);

const interrupted = new processors["playback-processor"]();
const interrupt = data=>interrupted.port.onmessage({data});
interrupt({type:"push",id:"old",pcm:new Int16Array(480).fill(4000)});
interrupted.process([],[[new Float32Array(128)]]);
interrupt({type:"pause",value:true});
interrupt({type:"drain",id:"old"});
interrupt({type:"push",id:"new",pcm:new Int16Array(480).fill(9000)});
interrupt({type:"drain",id:"new"});
interrupt({type:"discard",ids:["old"]});
interrupt({type:"pause",value:false});
const replacement=new Float32Array(480);
interrupted.process([],[[replacement]]);
assert.ok(replacement.every(sample=>sample===9000/32768),"a real interruption keeps the newly generated reply");
assert.deepEqual(interrupted.port.messages.filter(m=>m.type==="drained").map(m=>m.id),["new"]);
const prefetched = new processors["playback-processor"]();
const prefetch = data=>prefetched.port.onmessage({data});
prefetch({type:"push",id:"preface",pcm:new Int16Array([7000])});
prefetch({type:"hold",id:"briefing"});
const briefing=Int16Array.from({length:960},(_,i)=>i-400);
prefetch({type:"push",id:"briefing",pcm:briefing});
prefetch({type:"drain",id:"briefing"});
const beforeMap=new Float32Array(128).fill(1);
prefetched.process([],[[beforeMap]]);
assert.equal(beforeMap[0],7000/32768,"unrelated preface still plays ahead of the held briefing");
assert.ok(beforeMap.slice(1).every(sample=>sample===0));
assert.equal(prefetched.port.messages.filter(m=>m.id==="briefing").length,0,
  "prefetched audio reports neither start nor drain before visual readiness");
prefetch({type:"release",id:"briefing"});
const afterMap=new Float32Array(960);
prefetched.process([],[[afterMap]]);
assert.deepEqual([...afterMap],Array.from(briefing,sample=>sample/32768),
  "the first eligible render emits the full retained PCM without another delay");
assert.deepEqual(prefetched.port.messages.filter(m=>m.id==="briefing").map(m=>m.type),["started","drained"]);
prefetch({type:"hold",id:"obsolete"});
prefetch({type:"push",id:"obsolete",pcm:new Int16Array([9000])});
prefetch({type:"discard",ids:["obsolete"]});
prefetch({type:"push",id:"replacement",pcm:new Int16Array([3000])});
const nextSample=new Float32Array(1);
prefetched.process([],[[nextSample]]);
assert.equal(nextSample[0],3000/32768,"retiring a held response does not strand the queue");
send({type:"push",id:"single-block",pcm:new Int16Array([8000])});
playback.process([], [[new Float32Array(128)]]);
assert.equal(playback.port.messages.filter(m=>m.id==="single-block" && m.type==="started").length,1);
send({type:"push",id:"never-rendered",pcm:new Int16Array([8000])});
send({type:"drain",id:"never-rendered"});
send({type:"flush"});
send({type:"push",id:"empty",pcm:new Int16Array()});
playback.process([], [[new Float32Array(128)]]);
assert.equal(playback.port.messages.filter(m=>["never-rendered","empty"].includes(m.id)).length,0);
"""], cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_departure_and_results_playback_lifecycle(self):
        result = subprocess.run(["node", "-e", r"""
const fs = require("node:fs");
const vm = require("node:vm");
const assert = require("node:assert/strict");
const ts = require("typescript");
const nodes = [], sockets = [], contexts = [];
let stoppedTracks = 0, departures = 0, micRequests = 0;
let advertisedProtocol = "after-playback-v1";
const connections = [], states = [];
const timers = new Map();
let timerId = 0;
class Socket {
  static OPEN = 1;
  readyState = 1;
  sent = [];
  closed = false;
  constructor() {
    sockets.push(this);
    queueMicrotask(() => {
      this.emit({type:"relay.ready",turnTaking:advertisedProtocol});
      this.emit({type:"route.state",state:{runId:"run",missionPhase:"briefing"}});
    });
  }
  send(value) { this.sent.push(JSON.parse(value)); }
  close() { this.closed = true; this.readyState = 3; }
  emit(value) { this.onmessage?.({data:JSON.stringify(value)}); }
}
class Context {
  state = "running";
  destination = {};
  audioWorklet = {addModule:async()=>{}};
  constructor() { contexts.push(this); }
  async resume() {}
  async close() { this.state = "closed"; }
  createMediaStreamSource() { return {connect(){}}; }
  createGain() { return {connect(){}}; }
}
class Worklet {
  constructor(context, name) {
    this.name = name;
    this.port = {messages:[], onmessage:null, postMessage(message){
      this.messages.push(message);
      if(message.type==="mute")queueMicrotask(()=>this.onmessage?.({
        data:{type:"input-state",muted:message.value,epoch:message.epoch},
      }));
    }};
    nodes.push(this);
  }
  connect(node) { return node; }
  disconnect() {}
}
const exportsObject = {};
const code = ts.transpileModule(fs.readFileSync("lib/voiceClient.ts","utf8"), {
  compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}
}).outputText;
vm.runInNewContext(code, {
  exports:exportsObject, module:{exports:exportsObject}, process, console,
  URL, WebSocket:Socket, AudioContext:Context, AudioWorkletNode:Worklet,
  navigator:{mediaDevices:{getUserMedia:async()=>{micRequests++;return {getTracks:()=>[{stop(){stoppedTracks++;}}]};}}},
  btoa, atob, crypto, Int16Array, Uint8Array,
  setTimeout:(fn)=>{const id=++timerId;timers.set(id,()=>{timers.delete(id);fn();});return id;},
  clearTimeout:id=>timers.delete(id),
});
async function exercise({early=false,fail=false}={}) {
  nodes.length=sockets.length=contexts.length=connections.length=states.length=0;
  stoppedTracks=departures=micRequests=0;
  const noop=()=>{};
  let reveals=0;
  const statuses=[];
  const session=new exportsObject.VoiceSession({
    onStatus:s=>statuses.push(s),onLevel:noop,onBusy:noop,onTranscript:noop,onTool:noop,
    onTtfa:noop,onDebrief:noop,onError:noop,onRouteState:s=>states.push(s),
    onConnection:c=>connections.push(c),onDeparture:()=>departures++,
    onResultsReveal:()=>reveals++,
  });
  await session.start();
  const socket=sockets[0], playback=nodes.find(n=>n.name==="playback-processor");
  socket.emit({type:"mission.launch",runId:"other-run"});
  assert.equal(session.launchRunId,null);
  socket.emit({type:"mission.launch",runId:"run"});
  socket.emit({type:"mission.launch.response",runId:"run",responseId:"cancelled"});
  socket.emit({type:"response.done",response:{id:"cancelled",status:"cancelled"}});
  assert.equal(playback.port.messages.filter(m=>m.type==="drain").length,0);
  socket.emit({type:"mission.launch.response",runId:"run",responseId:"departure"});
  socket.emit({type:"response.audio.delta",delta:btoa("\0\0")});
  socket.emit({type:"response.done",response:{id:"unrelated",status:"completed"}});
  assert.equal(playback.port.messages.filter(m=>m.type==="drain").length,0);
  socket.emit({type:"response.done",response:{id:"departure",status:"completed"}});
  socket.emit({type:"mission.launch.done",runId:"run",responseId:"departure"});
  assert.equal(playback.port.messages.filter(m=>m.type==="drain").length,1);
  assert.equal(stoppedTracks,0);
  assert.equal(socket.closed,false);
  playback.port.onmessage({data:{type:"drained",id:"unrelated"}});
  assert.equal(departures,0);
  if(early) {
    session.markResultsReady("run");
    socket.emit({type:"mission.debrief",runId:"run",text:"세 사람을 구조했습니다."});
    assert.equal(socket.sent.filter(m=>m.type==="results.ready").length,0);
  }
  playback.port.onmessage({data:{type:"drained",id:"departure"}});
  await new Promise(setImmediate);
  assert.equal(stoppedTracks,1);
  assert.equal(contexts[0].state,"running");
  assert.equal(departures,1);
  assert.equal(socket.closed,false);
  assert.equal(connections.at(-1),true);
  socket.emit({type:"route.state",state:{runId:"run",missionPhase:"capturing"}});
  assert.equal(states.at(-1).missionPhase,"capturing");
  assert.equal(session.sendCommand("retry_mission"),true);
  socket.emit({type:"mission.debrief",runId:"run",text:"세 사람을 구조했습니다."});
  socket.emit({type:"mission.debrief",runId:"run",text:"세 사람을 구조했습니다."});
  if(!early) {
    assert.equal(socket.sent.filter(m=>m.type==="results.ready").length,0);
    session.markResultsReady("stale");
    assert.equal(socket.sent.filter(m=>m.type==="results.ready").length,0);
    session.markResultsReady("run");
  }
  session.markResultsReady("run");
  await new Promise(setImmediate);
  assert.equal(socket.sent.filter(m=>m.type==="results.ready").length,1);
  assert.equal(socket.closed,false);
  assert.equal(stoppedTracks,1);
  assert.equal(reveals,0);
  assert.equal(micRequests,1);
  if(fail) {
    socket.emit({type:"mission.debrief.failed",runId:"run",message:"결과 음성 연결 실패"});
    await new Promise(setImmediate);
    assert.equal(socket.closed,true);
    assert.equal(contexts[0].state,"closed");
    assert.equal(stoppedTracks,1);
    assert.equal(reveals,1);
    assert.equal(statuses.at(-1),"error","failed narration remains visible in Home's error banner");
    return;
  }
  socket.emit({type:"mission.debrief.response",runId:"run",responseId:"results"});
  socket.emit({type:"response.created",response:{id:"results",metadata:{missionDebrief:"true",runId:"run"}}});
  playback.port.onmessage({data:{type:"state",playing:true}});
  assert.equal(reveals,0);
  socket.emit({type:"response.audio.delta",response_id:"results",delta:btoa("\0\0")});
  assert.equal(playback.port.messages.at(-1).id,"results");
  assert.equal(reveals,0,"network arrival is not audible playback");
  playback.port.onmessage({data:{type:"started",id:"departure"}});
  assert.equal(reveals,0);
  playback.port.onmessage({data:{type:"started",id:"results"}});
  playback.port.onmessage({data:{type:"started",id:"results"}});
  assert.equal(reveals,1);
  socket.emit({type:"response.done",response:{id:"results",status:"completed"}});
  socket.emit({type:"mission.debrief.done",runId:"run",responseId:"results"});
  assert.equal(playback.port.messages.filter(m=>m.type==="drain").length,2);
  assert.equal(socket.closed,false);
  playback.port.onmessage({data:{type:"drained",id:"results"}});
  await new Promise(setImmediate);
  assert.equal(contexts[0].state,"closed");
  assert.equal(socket.closed,true);
  assert.equal(connections.at(-1),false);
  assert.equal(stoppedTracks,1);
  assert.equal(departures,1);
  assert.equal(micRequests,1);
  assert.equal(reveals,1);
  assert.equal(timers.size,0);
}
async function exerciseResultsFallbacks() {
  const noop=()=>{};
  for(const mode of ["text","no-audio","timeout","pending-debrief","pending-drain",
                     "disconnect-before","disconnect-after","error","reset","reset-waiting","retry"]) {
    nodes.length=sockets.length=contexts.length=0;
    let reveals=0, debriefs=0, departures=0;
    const errors=[], statuses=[];
    const session=new exportsObject.VoiceSession({
      onStatus:s=>statuses.push(s),onLevel:noop,onBusy:noop,onTranscript:noop,onTool:noop,
      onTtfa:noop,onDebrief:()=>debriefs++,onError:e=>errors.push(e),onRouteState:noop,
      onResultsReveal:()=>reveals++,onDeparture:()=>departures++,
    });
    await session.start({withVoice:mode!=="text"});
    const socket=sockets[0], playback=nodes.find(n=>n.name==="playback-processor");
    socket.emit({type:"route.state",state:{runId:"run",revision:4}});
    socket.emit({type:"route.state",state:{runId:"stale",revision:9}});
    socket.emit({type:"mission.debrief",runId:"stale",text:"stale"});
    assert.equal(debriefs,0);
    assert.equal(session.currentRunId,"run");
    socket.emit({type:"route.state",state:{runId:"run",revision:3}});
    assert.equal(session.routeRevision,4);
    if(mode==="disconnect-before") {
      socket.onclose({code:1006});
      await new Promise(setImmediate);
      assert.equal(reveals,0);
      session.markResultsReady("run");
      assert.equal(reveals,1,"disconnect before debrief must not leave results invisible");
      await session.stop();
      continue;
    }
    if(mode==="pending-debrief" || mode==="pending-drain") {
      if(mode==="pending-drain") {
        socket.emit({type:"mission.launch",runId:"run"});
        socket.emit({type:"mission.launch.response",runId:"run",responseId:"departure"});
        socket.emit({type:"mission.launch.done",runId:"run",responseId:"departure"});
        socket.emit({type:"mission.debrief",runId:"run",text:"early completion"});
      }
      session.markResultsReady("run");
      assert.equal(socket.sent.filter(m=>m.type==="results.ready").length,0);
      [...timers.values()][0]();
      assert.equal(reveals,1,"missing debrief or stalled departure drain must report a bounded fallback");
      assert.equal(errors.length,1);
      await session.stop();
      continue;
    }
    socket.emit({type:"mission.debrief",runId:"run",text:"aborted before boarding"});
    assert.equal(departures,0,"preboarding abort does not announce a departure");
    assert.equal(socket.sent.filter(m=>m.type==="results.ready").length,0);
    assert.equal(session.microphoneMuted,true);
    assert.equal(reveals,0);
    if(mode==="reset") {
      await session.stop();
      session.markResultsReady("run");
      assert.equal(reveals,0);
      assert.equal(timers.size,0);
      continue;
    }
    session.markResultsReady("run");
    if(mode==="text") {
      assert.equal(reveals,1);
      assert.equal(socket.sent.filter(m=>m.type==="results.ready").length,0);
    } else {
      assert.equal(socket.sent.filter(m=>m.type==="results.ready").length,1);
      assert.equal(reveals,0);
      if(mode==="reset-waiting") {
        const oldTimer=[...timers.values()][0], oldPlayback=playback.port.onmessage;
        const oldSocket=socket.onmessage;
        await session.stop();
        await session.start();
        oldTimer();
        oldPlayback({data:{type:"started",id:"old-results"}});
        oldSocket({data:JSON.stringify({type:"mission.debrief",runId:"run",text:"old result"})});
        assert.equal(reveals,0);
        assert.equal(session.debriefRunId,null);
        assert.equal(timers.size,0);
        await session.stop();
        continue;
      } else if(mode==="retry") {
        socket.emit({type:"mission.debrief.response",runId:"run",responseId:"first"});
        socket.emit({type:"response.done",response:{id:"first",status:"failed"}});
        socket.emit({type:"mission.debrief.response",runId:"run",responseId:"retry"});
        socket.emit({type:"mission.debrief.response",runId:"stale",responseId:"stale"});
        playback.port.onmessage({data:{type:"started",id:"stale"}});
        assert.equal(reveals,0);
        playback.port.onmessage({data:{type:"started",id:"first"}});
        assert.equal(reveals,1,"already queued result PCM still belongs to this run during retry");
        socket.emit({type:"mission.debrief.done",runId:"run",responseId:"retry"});
        assert.equal(playback.port.messages.at(-1).id,"retry");
        playback.port.onmessage({data:{type:"drained",id:"retry"}});
      } else if(mode==="no-audio") {
        socket.emit({type:"mission.debrief.response",runId:"run",responseId:"empty"});
        socket.emit({type:"mission.debrief.done",runId:"run",responseId:"empty"});
        assert.equal(reveals,0);
        playback.port.onmessage({data:{type:"drained",id:"empty"}});
        assert.equal(errors.length,1,"a silent completed response is an explicit audio failure");
        assert.equal(statuses.at(-1),"error");
      } else if(mode==="timeout") {
        const callback=[...timers.values()][0];
        callback();
        assert.equal(errors.length,1);
        callback();
        assert.equal(errors.length,1,"stale timeout is inert");
      } else if(mode==="disconnect-after") {
        socket.onclose({code:1006});
      } else if(mode==="error") {
        socket.emit({type:"error",error:{message:"failed"}});
      }
      assert.equal(reveals,1);
    }
    await new Promise(setImmediate);
    session.markResultsReady("run");
    assert.equal(reveals,1);
    await session.stop();
    assert.equal(timers.size,0);
  }
}
async function exerciseCompleteReplies() {
  nodes.length=sockets.length=contexts.length=0;
  const noop=()=>{}, transcripts=[], captions=[], statuses=[], errors=[];
  const session=new exportsObject.VoiceSession({
    onStatus:s=>statuses.push(s),onLevel:noop,onBusy:noop,onTranscript:(...args)=>transcripts.push(args),
    onTool:noop,onTtfa:noop,onDebrief:noop,onError:e=>errors.push(e),onRouteState:noop,
    onSpeechText:text=>captions.push(text),
  });
  await session.start();
  const socket=sockets[0], capture=nodes.find(n=>n.name==="capture-processor");
  const playback=nodes.find(n=>n.name==="playback-processor");
  const muted=()=>capture.port.messages.at(-1).value;
  const ready=(windowId,responseIds)=>socket.emit({type:"voice.input.ready",runId:"run",windowId,responseIds});
  socket.emit({type:"response.created",response:{id:"welcome"}});
  socket.emit({type:"response.audio_transcript.delta",delta:"첫 문장. 마지막 문장."});
  socket.emit({type:"response.audio.delta",delta:btoa("\0\0")});
  assert.equal(captions.at(-1),"","generated text must not get ahead of playback");
  playback.port.onmessage({data:{type:"started",id:"welcome"}});
  assert.equal(captions.at(-1),"첫 문장. 마지막 문장.");
  socket.emit({type:"response.done",response:{id:"welcome",status:"completed"}});
  assert.equal(muted(),true,"generation finishing must not reopen the mic before playback");
  playback.port.onmessage({data:{type:"state",playing:false}});
  assert.equal(muted(),true,"idle playback is not a response drain acknowledgement");
  capture.port.onmessage({data:{pcm:new Int16Array([1]),peak:0.5}});
  assert.equal(socket.sent.filter(m=>m.type==="audio").length,0,"ignore already-posted microphone frames");
  socket.emit({type:"input_audio_buffer.speech_started"});
  assert.equal(playback.port.messages.filter(m=>m.type==="flush").length,0);
  assert.equal(transcripts.filter(t=>t[0]==="user").length,0);
  playback.port.onmessage({data:{type:"drained",id:"stale"}});
  assert.equal(muted(),true);
  assert.equal(socket.sent.filter(m=>m.type==="voice.reply_drained").length,0);
  ready("first",["welcome"]);
  assert.equal(muted(),true,"relay readiness alone cannot reopen capture");
  playback.port.onmessage({data:{type:"drained",id:"welcome"}});
  assert.deepEqual(socket.sent.filter(m=>m.type==="voice.reply_drained"),[
    {type:"voice.reply_drained",responseId:"welcome",runId:"run"},
  ]);
  assert.equal(muted(),false);
  await Promise.resolve();
  assert.equal(statuses.at(-1),"listening","cue follows the capture worklet acknowledgement");
  const firstEpoch=session.inputEpoch;
  capture.port.onmessage({data:{pcm:new Int16Array([1]),peak:0.5,epoch:firstEpoch-1}});
  assert.equal(socket.sent.filter(m=>m.type==="audio").length,0,"stale capture frames cannot leak into the window");
  capture.port.onmessage({data:{pcm:new Int16Array([1]),peak:0.5,epoch:firstEpoch}});
  assert.equal(socket.sent.filter(m=>m.type==="audio").length,1);
  assert.equal(socket.sent.find(m=>m.type==="audio").windowId,"first");
  assert.deepEqual(transcripts.at(-1),["agent","첫 문장. 마지막 문장.",true]);

  socket.emit({type:"input_audio_buffer.speech_stopped",item_id:"answer"});
  assert.equal(muted(),true);
  socket.emit({type:"voice.input.failed",runId:"old-run",itemId:"old-answer",message:"stale"});
  assert.equal(errors.length,0);
  socket.emit({type:"voice.input.failed",runId:"run",itemId:"answer",message:"다시 말해 줘."});
  assert.deepEqual(errors,["다시 말해 줘."]);
  assert.equal(muted(),true,"a transcription failure cannot authorize new capture");
  assert.notEqual(statuses.at(-1),"error","recoverable transcription failures keep the session alive");
  socket.emit({type:"conversation.item.input_audio_transcription.completed",item_id:"answer",transcript:"응"});
  assert.deepEqual(transcripts.at(-1),["user","응",true],"late admitted transcription survives closure");
  socket.emit({type:"response.created",response:{id:"tool-turn"}});
  assert.equal(muted(),true);
  socket.emit({type:"response.function_call_arguments.done",name:"confirm_prompt"});
  socket.emit({type:"response.done",response:{id:"tool-turn",status:"completed"}});
  playback.port.onmessage({data:{type:"drained",id:"welcome"}});
  assert.equal(muted(),true,"a tool-only completion cannot reopen between continuations");
  socket.emit({type:"route_intro.pending",runId:"run",introId:"intro"});
  socket.emit({type:"route_intro.response",runId:"run",introId:"intro",responseId:"explanation"});
  socket.emit({type:"response.created",response:{id:"explanation"}});
  socket.emit({type:"response.output_audio_transcript.delta",delta:"모니터 1 설명. 모니터 2 설명. 모니터 3 설명."});
  socket.emit({type:"response.output_audio.delta",delta:btoa("\0\0")});
  assert.equal(playback.port.messages.find(m=>m.type==="hold").id,"explanation");
  assert.equal(captions.at(-1),"첫 문장. 마지막 문장.","prefetched captions cannot replace current speech");
  socket.emit({type:"response.done",response:{id:"explanation",status:"completed"}});
  ready("second",["tool-turn","explanation"]);
  assert.equal(muted(),true);
  assert.equal(session.sendText("interrupt"),false);
  socket.emit({type:"input_audio_buffer.speech_started",item_id:"noise"});
  socket.emit({type:"input_audio_buffer.speech_stopped",item_id:"noise"});
  assert.equal(playback.port.messages.filter(m=>m.type==="pause").length,0,"noise cannot pause strict-mode output");
  assert.equal(playback.port.messages.filter(m=>m.type==="flush"||m.type==="discard").length,0);
  assert.equal(socket.sent.filter(m=>m.type==="voice.interrupt").length,0);
  assert.equal(timers.size,0,"strict mode has no ASR-dependent playback timeout");
  session.sendRouteIntroReady();
  session.sendRouteIntroReady();
  assert.equal(socket.sent.filter(m=>m.type==="route_intro.ready").length,1);
  assert.equal(socket.sent.find(m=>m.type==="route_intro.ready").introId,"intro");
  assert.equal(playback.port.messages.find(m=>m.type==="release").id,"explanation");
  playback.port.onmessage({data:{type:"started",id:"explanation"}});
  assert.equal(captions.at(-1),"모니터 1 설명. 모니터 2 설명. 모니터 3 설명.");
  assert.equal(session.inputReady.windowId,"second","ignored VAD cannot erase pending playback readiness");
  contexts[0].currentTime=10;contexts[0].baseLatency=0.08;
  playback.port.onmessage({data:{type:"drained",id:"explanation",contextTime:10.02}});
  assert.equal(muted(),true,"render completion still waits for estimated speaker output");
  assert.equal(timers.size,1);
  contexts[0].currentTime=10.11;
  [...timers.values()][0]();
  await Promise.resolve();
  assert.equal(muted(),false);
  assert.equal(statuses.at(-1),"listening");
  socket.emit({type:"voice.input.closed",runId:"run",windowId:"first"});
  assert.equal(muted(),false,"old closure cannot close the next listening window");
  const sentBefore=socket.sent.length;
  ready("first",["welcome"]);
  assert.equal(socket.sent.length,sentBefore,"used readiness tokens cannot reopen an old turn");

  socket.emit({type:"response.created",response:{id:"confirmation"}});
  socket.emit({type:"response.audio_transcript.delta",response_id:"confirmation",delta:"이 경로로"});
  socket.emit({type:"response.audio_transcript.done",response_id:"confirmation",transcript:"이 경로로 출발할까?"});
  socket.emit({type:"response.output_audio.delta",response_id:"confirmation",delta:btoa("\0\0")});
  assert.equal(muted(),true);
  playback.port.onmessage({data:{type:"started",id:"confirmation"}});
  assert.equal(captions.at(-1),"이 경로로 출발할까?","the bubble follows the actual new audio, including the final question");
  socket.emit({type:"response.done",response:{id:"confirmation",status:"completed"}});
  playback.port.onmessage({data:{type:"drained",id:"confirmation"}});
  assert.equal(muted(),true,"drain-before-ready waits for the relay's continuation barrier");
  ready("third",["confirmation"]);
  assert.equal(muted(),false,"ready-after-drain opens without another timeout");
  assert.equal(session.sendText("next turn"),true);
  socket.emit({type:"response.created",response:{id:"reset-during-speech"}});
  socket.emit({type:"response.audio.delta",response_id:"reset-during-speech",delta:btoa("\0\0")});
  socket.emit({type:"response.done",response:{id:"reset-during-speech",status:"completed"}});
  playback.port.onmessage({data:{type:"drained",id:"reset-during-speech",contextTime:10.2}});
  const oldTimeout=[...timers.values()][0];
  await session.stop();
  assert.equal(timers.size,0);
  oldTimeout();
  assert.equal(session.inputWindowId,null,"stale output callback cannot reopen a stopped session");
}
async function exercisePrefetchEdges() {
  for(const scenario of ["ready-first","failure","overflow","text-only"]) {
    nodes.length=sockets.length=contexts.length=0;
    const noop=()=>{},errors=[];
    const session=new exportsObject.VoiceSession({
      onStatus:noop,onLevel:noop,onBusy:noop,onTranscript:noop,onTool:noop,
      onTtfa:noop,onDebrief:noop,onError:e=>errors.push(e),onRouteState:noop,
    });
    await session.start();
    const socket=sockets[0],playback=nodes.find(n=>n.name==="playback-processor");
    socket.emit({type:"route_intro.pending",runId:"wrong",introId:"wrong"});
    assert.equal(session.routeIntro,null);
    socket.emit({type:"route_intro.pending",runId:"run",introId:"intro"});
    if(scenario==="ready-first")session.sendRouteIntroReady();
    socket.emit({type:"route_intro.response",runId:"run",introId:"intro",responseId:"intro-reply"});
    socket.emit({type:"response.created",response:{id:"intro-reply"}});
    if(scenario==="ready-first") {
      assert.equal(playback.port.messages.filter(m=>m.type==="hold").length,0);
      socket.emit({type:"response.audio.delta",response_id:"intro-reply",delta:btoa("\0\0")});
      assert.equal(playback.port.messages.at(-1).type,"push","late audio streams immediately after map readiness");
    } else if(scenario==="overflow") {
      session.routeIntro.bytes=4*1024*1024;
      socket.emit({type:"response.audio.delta",response_id:"intro-reply",delta:btoa("\0\0")});
      assert.equal(errors.length,1);
      assert.equal(socket.sent.filter(m=>m.type==="route_intro.retry").length,1);
      const before=playback.port.messages.length;
      socket.emit({type:"response.audio.delta",response_id:"intro-reply",delta:btoa("\0\0")});
      assert.equal(playback.port.messages.length,before,"overflow never retains or plays a truncated response");
      session.sendRouteIntroReady();
    } else if(scenario==="failure") {
      socket.emit({type:"route_intro.failed",runId:"run",introId:"intro",responseId:"intro-reply",retrying:true,message:"retry"});
      assert.equal(errors.length,1);
      session.sendRouteIntroReady();
      socket.emit({type:"route_intro.response",runId:"run",introId:"intro",responseId:"retry"});
      socket.emit({type:"response.created",response:{id:"retry"}});
      socket.emit({type:"response.audio.delta",response_id:"retry",delta:btoa("\0\0")});
      assert.equal(playback.port.messages.at(-1).id,"retry");
    } else {
      socket.emit({type:"response.done",response:{id:"intro-reply",status:"completed"}});
      assert.equal(session.speech.has("intro-reply"),false,"no-audio completion must not recreate an unfinished speech record");
      socket.emit({type:"voice.input.ready",runId:"run",windowId:"text-window",responseIds:["intro-reply"]});
      assert.equal(session.microphoneMuted,true);
      session.sendRouteIntroReady();
      assert.equal(session.microphoneMuted,false,"no-audio recovery still honors the visual gate");
    }
    await session.stop();
    assert.equal(timers.size,0);
  }
}
async function exerciseProtocolMismatch() {
  nodes.length=sockets.length=contexts.length=0;
  const noop=()=>{}, errors=[];
  advertisedProtocol=undefined;
  const session=new exportsObject.VoiceSession({
    onStatus:noop,onLevel:noop,onBusy:noop,onTranscript:noop,onTool:noop,
    onTtfa:noop,onDebrief:noop,onError:e=>errors.push(e),onRouteState:noop,
  });
  await assert.rejects(session.start(),/버전이 달라/);
  assert.ok(errors.some(message=>message.includes("버전이 달라")));
  assert.equal(sockets[0].closed,true);
  assert.equal(sockets[0].sent.some(message=>message.type==="audio"),false,
    "a protocol mismatch never silently enables permissive microphone behavior");
  advertisedProtocol="after-playback-v1";
}
(async()=>{
  await exerciseCompleteReplies();
  await exercisePrefetchEdges();
  await exercise();
  await exercise({early:true});
  await exercise({fail:true});
  await exerciseResultsFallbacks();
  await exerciseProtocolMismatch();
})().catch(error=>{console.error(error);process.exitCode=1;});
"""], cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
