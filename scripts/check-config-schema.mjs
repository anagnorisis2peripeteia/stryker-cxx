#!/usr/bin/env node
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const schemaPath = resolve("docs/schemas/stryker-cxx.config.schema.json");
const fixturePaths = [
  resolve("fixtures/config/stryker-cxx.config.json"),
  resolve("fixtures/config/stryker-cxx.config.yml"),
];

function readConfig(path) {
  const text = readFileSync(path, "utf8");
  if (path.endsWith(".json")) return JSON.parse(text);
  return parseSimpleYaml(text);
}

function parseSimpleYaml(text) {
  const root = {};
  const stack = [{ indent: -1, value: root }];
  for (const rawLine of text.split(/\r?\n/)) {
    if (!rawLine.trim() || rawLine.trimStart().startsWith("#")) continue;
    const indent = rawLine.match(/^ */)[0].length;
    const line = rawLine.trim();
    const match = line.match(/^([^:]+):(.*)$/);
    if (!match) throw new Error(`unsupported YAML line: ${rawLine}`);
    const key = match[1].trim();
    const rawValue = match[2].trim();
    while (stack[stack.length - 1].indent >= indent) stack.pop();
    const parent = stack[stack.length - 1].value;
    if (rawValue === "") {
      const child = {};
      parent[key] = child;
      stack.push({ indent, value: child });
    } else {
      parent[key] = parseYamlScalar(rawValue);
    }
  }
  return root;
}

function parseYamlScalar(rawValue) {
  if (rawValue === "true") return true;
  if (rawValue === "false") return false;
  if (rawValue === "null") return null;
  if (/^-?\d+(\.\d+)?$/.test(rawValue)) return Number(rawValue);
  if (rawValue.startsWith("[") || rawValue.startsWith("{")) {
    return JSON.parse(rawValue);
  }
  if (
    (rawValue.startsWith('"') && rawValue.endsWith('"')) ||
    (rawValue.startsWith("'") && rawValue.endsWith("'"))
  ) {
    return rawValue.slice(1, -1);
  }
  return rawValue;
}

function typeOf(value) {
  if (value === null) return "null";
  if (Array.isArray(value)) return "array";
  return typeof value === "object" ? "object" : typeof value;
}

function matchesType(value, type) {
  if (Array.isArray(type)) return type.includes(typeOf(value));
  if (type === "integer") return Number.isInteger(value);
  return typeOf(value) === type;
}

function validate(value, schema, path = "$") {
  const errors = [];
  if (schema.const !== undefined && value !== schema.const) {
    errors.push(`${path}: expected ${JSON.stringify(schema.const)}`);
  }
  if (schema.enum && !schema.enum.includes(value)) {
    errors.push(`${path}: expected one of ${schema.enum.map(String).join(", ")}`);
  }
  if (schema.type && !matchesType(value, schema.type)) {
    errors.push(`${path}: expected type ${JSON.stringify(schema.type)}, got ${typeOf(value)}`);
    return errors;
  }
  if (schema.pattern && typeof value === "string" && !new RegExp(schema.pattern).test(value)) {
    errors.push(`${path}: value does not match ${schema.pattern}`);
  }
  if (typeof value === "number") {
    if (schema.minimum !== undefined && value < schema.minimum) {
      errors.push(`${path}: expected >= ${schema.minimum}`);
    }
    if (schema.maximum !== undefined && value > schema.maximum) {
      errors.push(`${path}: expected <= ${schema.maximum}`);
    }
  }
  if (schema.oneOf) {
    const matches = schema.oneOf.filter(
      (candidate) => validate(value, candidate, path).length === 0,
    );
    if (matches.length !== 1) {
      errors.push(`${path}: expected exactly one schema match, got ${matches.length}`);
    }
  }
  if (schema.type === "object" && value && typeof value === "object" && !Array.isArray(value)) {
    validateObject(value, schema, path, errors);
  }
  if (schema.type === "array" && Array.isArray(value) && schema.items) {
    value.forEach((item, index) => {
      errors.push(...validate(item, schema.items, `${path}[${index}]`));
    });
  }
  return errors;
}

function validateObject(value, schema, path, errors) {
  const properties = schema.properties ?? {};
  if (schema.additionalProperties === false) {
    for (const key of Object.keys(value)) {
      if (!(key in properties)) errors.push(`${path}.${key}: unknown property`);
    }
  }
  for (const key of schema.required ?? []) {
    if (!(key in value)) errors.push(`${path}.${key}: required`);
  }
  for (const [key, child] of Object.entries(value)) {
    const childSchema = properties[key] ?? schema.additionalProperties;
    if (childSchema && typeof childSchema === "object") {
      errors.push(...validate(child, childSchema, `${path}.${key}`));
    }
  }
}

const schema = JSON.parse(readFileSync(schemaPath, "utf8"));
let totalErrors = 0;
for (const fixturePath of fixturePaths) {
  const fixture = readConfig(fixturePath);
  const errors = validate(fixture, schema);
  if (errors.length) {
    totalErrors += errors.length;
    console.error(`[schema:check] ${fixturePath}`);
    for (const error of errors) console.error(`  - ${error}`);
  } else {
    console.log(`[schema:check] ok ${fixturePath}`);
  }
}

if (totalErrors) process.exit(1);
