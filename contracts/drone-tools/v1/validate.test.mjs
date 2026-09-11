import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import { assertRequest, assertResponse, contract, tools, validate } from "./validate.mjs";
import { verifyContract } from "./verify.mjs";

test("v1 contract is immutable and every tool has a strict input schema", () => {
  assert.equal(verifyContract(), "1.0.0");
  assert.equal(tools.length, 7);
  assert.equal(new Set(tools.map(t => t.name)).size, 7);
  for (const tool of tools) {
    assert.equal(tool.strict, true);
    assert.equal(tool.parameters.additionalProperties, false);
  }
});
test("exact route arguments and caller envelope match the fixed contract", () => {
  const args = {profile_id: "contract-mock-v1", site_revision: "v1", destination_ids: ["tag-2","tag-3","tag-1"]};
  const envelope = {arguments: args, caller_id: "frontend", request_id: "one-intent"};
  assert.doesNotThrow(() => assertRequest("drone_execute_route", envelope));
  for (const value of [{...envelope, token: "not-a-field"}, {...envelope, arguments: {...args, speed: 1}},
                       {...envelope, arguments: {...args, destination_ids: [true]}},
                       {...envelope, arguments: {...args, site_revision: "invalid.dot"}}]) {
    assert.throws(() => assertRequest("drone_execute_route", value), TypeError);
  }
});
test("response error and truthful mock mode are required", () => {
  const error = {schema_version: 1, ok: false, execution_mode: "mock", physical_execution: false,
                 error: {code: "MODE_MISMATCH", message: "Not dispatched"}};
  assert.doesNotThrow(() => assertResponse("drone_execute_route", error));
  assert.throws(() => assertResponse("drone_execute_route", {...error, schema_version: 2}), TypeError);
  assert.throws(() => assertResponse("drone_execute_route", {...error, physical_execution: true}), TypeError);
  assert.throws(() => assertResponse("drone_execute_route", {...error, ok: true}), TypeError);
});
test("validator does not silently ignore new keywords or nonfinite numbers", () => {
  assert.throws(() => validate({type: "number"}, Infinity), TypeError);
  assert.throws(() => validate({type: "integer"}, 1.2), TypeError);
  assert.throws(() => validate({format: "unknown"}, "x"), /Unsupported schema keyword/);
  assert.throws(() => validate({$ref: "#/$defs/missing"}, {}), /Unresolved/);
  assert.throws(() => validate(contract.$defs.capture, {capture_id: "one"}), TypeError);
});
test("TypeScript contract stays colocated and declares every tool", () => {
  const types = readFileSync(new URL("./types.d.ts", import.meta.url), "utf8");
  for (const tool of tools) assert.ok(types.includes(tool.name));
});
