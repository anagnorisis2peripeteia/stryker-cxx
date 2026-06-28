export const MTE_SCHEMA_VERSION = "2.0";

const CXX_STATUS = new Set([
  "Killed",
  "Survived",
  "CompileError",
  "TimedOut",
  "Pending",
  "RuntimeError",
]);

function _isObject(value) {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

function _extractMte(value) {
  if (!_isObject(value)) {
    throw new Error("expected mutation-testing-elements payload object");
  }

  if (value.schemaVersion === MTE_SCHEMA_VERSION && _isObject(value.files)) {
    return value;
  }

  const nested = value.mutationTestingElements;
  if (_isObject(nested) && _isObject(nested.files)) {
    if (nested.schemaVersion == null) {
      return { ...nested, schemaVersion: MTE_SCHEMA_VERSION };
    }
    if (nested.schemaVersion === MTE_SCHEMA_VERSION) {
      return nested;
    }
  }

  throw new Error(`unexpected schemaVersion; expected '${MTE_SCHEMA_VERSION}'`);
}

export function assertMteReport(value) {
  const payload = _extractMte(value);

  if (typeof payload.schemaVersion !== "string") {
    throw new Error("expected mutation-testing-elements payload object");
  }

  if (payload.schemaVersion !== MTE_SCHEMA_VERSION) {
    throw new Error(`unexpected schemaVersion; expected '${MTE_SCHEMA_VERSION}'`);
  }

  if (typeof payload.files !== "object" || payload.files === null) {
    throw new Error("missing files map");
  }

  if (typeof payload.language !== "string" || payload.language.length === 0) {
    throw new Error("missing language");
  }

  for (const [file, entry] of Object.entries(payload.files)) {
    if (!file || typeof file !== "string") {
      throw new Error("file map key must be a string");
    }
    if (!entry || typeof entry !== "object") {
      throw new Error(`missing mutants entry for file ${file}`);
    }
    if (!Array.isArray(entry.mutants)) {
      throw new Error(`file ${file} missing mutants array`);
    }

    for (const mutant of entry.mutants) {
      if (!mutant || typeof mutant !== "object") {
        throw new Error(`invalid mutant record in ${file}`);
      }
      for (const field of ["id", "mutatorName", "original", "replacement", "status", "location"]) {
        if (!(field in mutant)) {
          throw new Error(`mutant ${file} missing ${field}`);
        }
      }
      if (!CXX_STATUS.has(mutant.status)) {
        throw new Error(`unknown mutant status ${mutant.status} in ${file}`);
      }
      const { location } = mutant;
      if (!location.start || !location.end) {
        throw new Error(`invalid location object for mutant ${mutant.id}`);
      }
    }
  }

  return payload;
}

export function toPlainMutationList(report) {
  const out = [];
  for (const [file, entry] of Object.entries(report.files || {})) {
    for (const mutant of entry.mutants || []) {
      out.push({ file, ...mutant });
    }
  }
  return out;
}

export function summarizeReport(report) {
  const flat = toPlainMutationList(report);
  const counts = flat.reduce(
    (acc, mut) => {
      acc.total += 1;
      if (mut.status === "Killed") acc.killed += 1;
      if (mut.status === "Survived") acc.survived += 1;
      if (mut.status === "CompileError") acc.compileErrors += 1;
      if (mut.status === "TimedOut") acc.timeouts += 1;
      if (mut.status === "Pending") acc.pending += 1;
      if (mut.status === "RuntimeError") acc.runtimeErrors += 1;
      return acc;
    },
    {
      total: 0,
      killed: 0,
      survived: 0,
      compileErrors: 0,
      timeouts: 0,
      pending: 0,
      runtimeErrors: 0,
    },
  );

  const score = counts.total ? counts.killed / counts.total : 1;
  return {
    schemaVersion: report.schemaVersion,
    language: report.language,
    projectRoot: report.projectRoot,
    total: counts.total,
    killed: counts.killed,
    survived: counts.survived,
    compileErrors: counts.compileErrors,
    timedOut: counts.timeouts,
    pending: counts.pending,
    runtimeErrors: counts.runtimeErrors,
    score,
  };
}

export function survivors(report) {
  return toPlainMutationList(report).filter((mut) => mut.status === "Survived");
}
