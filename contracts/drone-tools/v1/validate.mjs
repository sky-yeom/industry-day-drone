import { readFileSync } from "node:fs";

export const tools = JSON.parse(readFileSync(new URL("./tools.json", import.meta.url), "utf8"));
export const contract = JSON.parse(readFileSync(new URL("./contract.schema.json", import.meta.url), "utf8"));
export const responseSchemas = Object.freeze({
  drone_get_capabilities: "capabilitiesResponse",
  drone_get_status: "statusResponse",
  drone_execute_route: "missionResponse",
  drone_get_mission: "missionResponse",
  drone_stop_mission: "stopResponse",
  drone_get_sensor_snapshot: "sensorResponse",
  drone_get_captures: "capturesResponse",
});
const argumentsByName = Object.fromEntries(tools.map(tool => [tool.name, tool.parameters]));
const object = value => value !== null && typeof value === "object" && !Array.isArray(value);
const equal = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const fail = path => { throw new TypeError(`Contract mismatch at ${path}`); };

// Implements only the JSON Schema keywords used by this frozen contract.
// Unsupported keywords fail explicitly so future versions cannot silently bypass validation.
export function validate(schema, value, root = contract, path = "$") {
  const supported = new Set(["$schema", "$id", "$defs", "$ref", "title", "description", "type", "const",
    "enum", "allOf", "anyOf", "required", "properties", "additionalProperties", "items", "minItems",
    "maxItems", "uniqueItems", "minLength", "maxLength", "pattern", "minimum", "maximum", "exclusiveMinimum"]);
  for (const key of Object.keys(schema)) {
    if (!supported.has(key)) throw new TypeError(`Unsupported schema keyword: ${key}`);
  }
  if (schema.$ref) {
    if (!schema.$ref.startsWith("#/$defs/")) throw new TypeError("Only local contract references are supported");
    const target = root.$defs?.[schema.$ref.slice(8)];
    if (!target) throw new TypeError("Unresolved contract reference");
    validate(target, value, root, path);
  }
  if (schema.allOf) for (const branch of schema.allOf) validate(branch, value, root, path);
  if (schema.anyOf && !schema.anyOf.some(branch => {
    try { validate(branch, value, root, path); return true; }
    catch (error) {
      if (error instanceof TypeError && error.message.startsWith("Contract mismatch")) return false;
      throw error;
    }
  })) fail(path);
  if ("const" in schema && !equal(schema.const, value)) fail(path);
  if (schema.enum && !schema.enum.some(item => equal(item, value))) fail(path);
  if (schema.type) {
    const matches = type => type === "object" ? object(value)
      : type === "array" ? Array.isArray(value)
        : type === "null" ? value === null
          : type === "integer" ? Number.isInteger(value)
            : type === "number" ? typeof value === "number" && Number.isFinite(value)
              : typeof value === type;
    if (!(Array.isArray(schema.type) ? schema.type : [schema.type]).some(matches)) fail(path);
  }
  if (object(value)) {
    for (const key of schema.required || []) if (!Object.hasOwn(value, key)) fail(`${path}.${key}`);
    for (const [key, item] of Object.entries(value)) {
      if (Object.hasOwn(schema.properties || {}, key)) validate(schema.properties[key], item, root, `${path}.${key}`);
      else if (schema.additionalProperties === false) fail(`${path}.${key}`);
    }
  }
  if (Array.isArray(value)) {
    if (value.length < (schema.minItems ?? 0) || value.length > (schema.maxItems ?? Infinity)) fail(path);
    if (schema.uniqueItems && new Set(value.map(item => JSON.stringify(item))).size !== value.length) fail(path);
    if (schema.items) value.forEach((item, index) => validate(schema.items, item, root, `${path}[${index}]`));
  }
  if (typeof value === "string") {
    const size = [...value].length;
    if (size < (schema.minLength ?? 0) || size > (schema.maxLength ?? Infinity)) fail(path);
    if (schema.pattern && !(new RegExp(schema.pattern, "u")).test(value)) fail(path);
  }
  if (typeof value === "number") {
    if (!Number.isFinite(value) || value < (schema.minimum ?? -Infinity) || value > (schema.maximum ?? Infinity)) fail(path);
    if ("exclusiveMinimum" in schema && value <= schema.exclusiveMinimum) fail(path);
  }
  return value;
}

export function assertRequest(name, envelope) {
  if (!Object.hasOwn(argumentsByName, name)) throw new TypeError("Unknown tool");
  validate(contract.$defs.request, envelope);
  validate(argumentsByName[name], envelope.arguments);
  return envelope;
}

export function assertResponse(name, response) {
  if (!Object.hasOwn(responseSchemas, name)) throw new TypeError("Unknown tool");
  validate(response?.ok === false ? contract.$defs.response : contract.$defs[responseSchemas[name]], response);
  if (response.execution_mode === "mock" && response.physical_execution !== false) fail("$.physical_execution");
  return response;
}
