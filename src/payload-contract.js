export const MTE_SCHEMA_VERSION = "2.0";

export const MTE_STATUSES = Object.freeze([
  "Killed",
  "Survived",
  "NoCoverage",
  "Timeout",
  "Ignored",
  "Pending",
  "RuntimeError",
]);

export const SUMMARY_STATUS_FIELDS = Object.freeze({
  Killed: "killed",
  Survived: "survived",
  NoCoverage: "compileErrors",
  Timeout: "timeouts",
  Ignored: "ignored",
  Pending: "pending",
  RuntimeError: "runtimeErrors",
});

const MTE_STATUS_SET = new Set(MTE_STATUSES);

function isObject(value) {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

export function supportedMteStatuses() {
  return [...MTE_STATUSES];
}

export function isMteStatus(status) {
  return MTE_STATUS_SET.has(status);
}

export function normalizeMtePayload(value) {
  if (!isObject(value)) {
    throw new Error("expected mutation-testing-elements payload object");
  }

  if (value.schemaVersion === MTE_SCHEMA_VERSION && isObject(value.files)) {
    return value;
  }

  const nested = value.mutationTestingElements;
  if (isObject(nested) && isObject(nested.files)) {
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
  const payload = normalizeMtePayload(value);

  if (typeof payload.schemaVersion !== "string") {
    throw new Error("expected mutation-testing-elements payload object");
  }

  if (payload.schemaVersion !== MTE_SCHEMA_VERSION) {
    throw new Error(`unexpected schemaVersion; expected '${MTE_SCHEMA_VERSION}'`);
  }

  if (!isObject(payload.files)) {
    throw new Error("missing files map");
  }

  if (typeof payload.language !== "string" || payload.language.length === 0) {
    throw new Error("missing language");
  }

  for (const [file, entry] of Object.entries(payload.files)) {
    if (!file || typeof file !== "string") {
      throw new Error("file map key must be a string");
    }
    if (!isObject(entry)) {
      throw new Error(`missing mutants entry for file ${file}`);
    }
    if (!Array.isArray(entry.mutants)) {
      throw new Error(`file ${file} missing mutants array`);
    }

    for (const mutant of entry.mutants) {
      if (!isObject(mutant)) {
        throw new Error(`invalid mutant record in ${file}`);
      }
      for (const field of ["id", "mutatorName", "original", "replacement", "status", "location"]) {
        if (!(field in mutant)) {
          throw new Error(`mutant ${file} missing ${field}`);
        }
      }
      if (!isMteStatus(mutant.status)) {
        throw new Error(`unknown mutant status ${mutant.status} in ${file}`);
      }
      const { location } = mutant;
      if (!isObject(location) || !location.start || !location.end) {
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
      const field = SUMMARY_STATUS_FIELDS[mut.status];
      if (field) acc[field] += 1;
      return acc;
    },
    {
      total: 0,
      killed: 0,
      survived: 0,
      compileErrors: 0,
      timeouts: 0,
      ignored: 0,
      pending: 0,
      runtimeErrors: 0,
    },
  );

  const denominator = Math.max(0, counts.total - counts.ignored);
  const score = denominator ? counts.killed / denominator : 1;
  return {
    schemaVersion: report.schemaVersion,
    language: report.language,
    projectRoot: report.projectRoot,
    total: counts.total,
    killed: counts.killed,
    survived: counts.survived,
    compileErrors: counts.compileErrors,
    timedOut: counts.timeouts,
    ignored: counts.ignored,
    pending: counts.pending,
    runtimeErrors: counts.runtimeErrors,
    score,
  };
}

export function survivors(report) {
  return toPlainMutationList(report).filter((mut) => mut.status === "Survived");
}
