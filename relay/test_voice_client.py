"""Exercise the existing TypeScript client using installed Node/TypeScript, no browser or cloud."""

from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("node") and (ROOT / "node_modules/typescript").exists(),
                     "Client contract requires the existing frontend dependencies")
class VoiceClientTests(unittest.TestCase):
    def test_departure_and_results_playback_lifecycle(self):
        result = subprocess.run(["node", "-e", r"""
const fs = require("node:fs");
const vm = require("node:vm");
const assert = require("node:assert/strict");
const ts = require("typescript");
const nodes = [], sockets = [], contexts = [];
let stoppedTracks = 0, departures = 0, micRequests = 0;
const connections = [], states = [];
class Socket {
  static OPEN = 1;
  readyState = 1;
  sent = [];
  closed = false;
  constructor() {
    sockets.push(this);
    queueMicrotask(() => {
      this.emit({type:"relay.ready"});
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
    this.port = {messages:[], onmessage:null, postMessage(message){this.messages.push(message);}};
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
});
async function exercise({early=false,fail=false}={}) {
  nodes.length=sockets.length=contexts.length=connections.length=states.length=0;
  stoppedTracks=departures=micRequests=0;
  const noop=()=>{};
  const session=new exportsObject.VoiceSession({
    onStatus:noop,onLevel:noop,onBusy:noop,onTranscript:noop,onTool:noop,
    onTtfa:noop,onDebrief:noop,onError:noop,onRouteState:s=>states.push(s),
    onConnection:c=>connections.push(c),onDeparture:()=>departures++,
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
  await new Promise(setImmediate);
  assert.equal(socket.sent.filter(m=>m.type==="results.ready").length,1);
  assert.equal(socket.closed,false);
  assert.equal(stoppedTracks,1);
  assert.equal(micRequests,1);
  if(fail) {
    socket.emit({type:"mission.debrief.failed",runId:"run",message:"결과 음성 연결 실패"});
    await new Promise(setImmediate);
    assert.equal(socket.closed,true);
    assert.equal(contexts[0].state,"closed");
    assert.equal(stoppedTracks,1);
    return;
  }
  socket.emit({type:"mission.debrief.response",runId:"run",responseId:"results"});
  socket.emit({type:"response.audio.delta",delta:btoa("\0\0")});
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
}
async function exerciseCompleteReplies() {
  nodes.length=sockets.length=contexts.length=0;
  const noop=()=>{}, transcripts=[];
  const session=new exportsObject.VoiceSession({
    onStatus:noop,onLevel:noop,onBusy:noop,onTranscript:(...args)=>transcripts.push(args),
    onTool:noop,onTtfa:noop,onDebrief:noop,onError:noop,onRouteState:noop,
  });
  await session.start();
  const socket=sockets[0], capture=nodes.find(n=>n.name==="capture-processor");
  const playback=nodes.find(n=>n.name==="playback-processor");
  const muted=()=>capture.port.messages.at(-1).value;
  socket.emit({type:"response.created",response:{id:"welcome"}});
  socket.emit({type:"response.audio_transcript.delta",delta:"첫 문장. 마지막 문장."});
  socket.emit({type:"response.audio.delta",delta:btoa("\0\0")});
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
  playback.port.onmessage({data:{type:"drained",id:"welcome"}});
  assert.equal(muted(),false);
  capture.port.onmessage({data:{pcm:new Int16Array([1]),peak:0.5}});
  assert.equal(socket.sent.filter(m=>m.type==="audio").length,1);
  assert.deepEqual(transcripts.at(-1),["agent","첫 문장. 마지막 문장.",true]);

  socket.emit({type:"response.created",response:{id:"tool-turn"}});
  assert.equal(muted(),true);
  socket.emit({type:"response.function_call_arguments.done",name:"confirm_prompt"});
  socket.emit({type:"response.done",response:{id:"tool-turn",status:"completed"}});
  playback.port.onmessage({data:{type:"drained",id:"welcome"}});
  assert.equal(muted(),true,"keep the mic muted across a tool continuation");
  socket.emit({type:"response.created",response:{id:"explanation"}});
  socket.emit({type:"response.output_audio_transcript.delta",delta:"모니터 1 설명. 모니터 2 설명. 모니터 3 설명."});
  socket.emit({type:"response.output_audio.delta",delta:btoa("\0\0")});
  socket.emit({type:"response.done",response:{id:"explanation",status:"completed"}});
  assert.equal(muted(),true);
  assert.equal(session.sendText("interrupt"),false);
  socket.emit({type:"input_audio_buffer.speech_started"});
  assert.equal(playback.port.messages.filter(m=>m.type==="flush").length,0);
  playback.port.onmessage({data:{type:"drained",id:"explanation"}});
  assert.equal(muted(),false);
  assert.equal(session.sendText("next turn"),true);
  await session.stop();
}
(async()=>{
  await exerciseCompleteReplies();
  await exercise();
  await exercise({early:true});
  await exercise({fail:true});
})().catch(error=>{console.error(error);process.exitCode=1;});
"""], cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
