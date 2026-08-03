/**
 * UI-only replay fixture.
 *
 * Nothing in this file is a KIT model result. The 76-condition x 3-replicate
 * roster mirrors the study shape, while values and flags are deterministic
 * synthetic examples used only to exercise the interface.
 */

export const MODE = Object.freeze({
  all: "all",
  calendar: "calendar",
  cyclic: "cyclic",
  profile: "profile",
});

export const MAX_CHECKUP = 11;
export const PREDICTION_CHECKUP = 3;
export const H3_CHECKUP = PREDICTION_CHECKUP + 3;
export const CELL_COUNT = 228;

const modeForCondition = (condition) => {
  if (condition <= 16) return MODE.calendar;
  if (condition <= 64) return MODE.cyclic;
  return MODE.profile;
};

const exposureFor = (mode, checkup) => {
  if (mode === MODE.calendar) {
    return { primary: `${checkup * 30} illustrative days`, secondary: "EFC not applicable" };
  }
  if (mode === MODE.cyclic) {
    return {
      primary: `${checkup * 95} illustrative EFC`,
      secondary: `${checkup * 21} illustrative days`,
    };
  }
  return {
    primary: `${checkup * 2200} illustrative km`,
    secondary: `${checkup * 68} EFC · ${checkup * 18} days`,
  };
};

const sohFor = (condition, replicate, checkup) => {
  const baseRate = 0.28 + (condition % 9) * 0.035 + replicate * 0.02;
  const lateAcceleration = Math.max(0, checkup - 6) ** 1.45 * (condition % 13 === 0 ? 0.3 : 0.07);
  const ripple = Math.sin(condition * 0.7 + replicate + checkup) * 0.08;
  return Math.max(72, 100 - baseRate * checkup - lateAcceleration + ripple);
};

const statusesFor = (condition, replicate, checkup) => {
  const statuses = [];
  if (checkup < 2) statuses.push("insufficient_history");
  if ((condition + replicate + checkup) % 19 === 0) statuses.push("missing_eis");
  if ((condition * replicate + checkup) % 43 === 0) statuses.push("abstained");
  if ((condition * 5 + replicate + checkup) % 97 === 0) statuses.push("data_unavailable");
  if (checkup > MAX_CHECKUP - 3) statuses.push("no_h3_followup");
  if (statuses.length === 0) statuses.push("eligible");
  return statuses;
};

const attentionFlagFor = (condition, replicate, checkup) =>
  checkup >= 5 && (condition * 7 + replicate * 11) % 37 === 0;

export const cells = Object.freeze(
  Array.from({ length: 76 }, (_, conditionIndex) => {
    const condition = conditionIndex + 1;
    return Array.from({ length: 3 }, (_, replicateIndex) => {
      const replicate = replicateIndex + 1;
      const mode = modeForCondition(condition);
      return Object.freeze({
        id: `P${String(condition).padStart(3, "0")}-R${replicate}`,
        condition,
        replicate,
        mode,
      });
    });
  }).flat(),
);

export function snapshotFor(cell, checkup) {
  const bounded = Math.max(0, Math.min(MAX_CHECKUP, Number(checkup)));
  const history = Array.from({ length: bounded + 1 }, (_, index) => ({
    checkup: index,
    soh: sohFor(cell.condition, cell.replicate, index),
  }));
  const predictionOriginSoh = sohFor(cell.condition, cell.replicate, PREDICTION_CHECKUP);
  const maturedSoh = sohFor(cell.condition, cell.replicate, H3_CHECKUP);
  return Object.freeze({
    ...cell,
    checkup: bounded,
    exposure: exposureFor(cell.mode, bounded),
    currentSoh: history.at(-1).soh,
    history,
    statuses: statusesFor(cell.condition, cell.replicate, bounded),
    illustrativeAttention: attentionFlagFor(cell.condition, cell.replicate, bounded),
    prediction: null,
    predictionReason: "Unavailable — awaiting scientific and software QA.",
    h3: Object.freeze({
      originCheckup: PREDICTION_CHECKUP,
      outcomeCheckup: H3_CHECKUP,
      matured: bounded >= H3_CHECKUP,
      measuredFutureSoh: bounded >= H3_CHECKUP ? maturedSoh : null,
      measuredLoss: bounded >= H3_CHECKUP ? predictionOriginSoh - maturedSoh : null,
    }),
  });
}

export function cellsForMode(mode) {
  return mode === MODE.all ? cells : cells.filter((cell) => cell.mode === mode);
}

export const fixtureMetadata = Object.freeze({
  evidence: "synthetic_ui_fixture",
  rosterCount: CELL_COUNT,
  acceptedOofAvailable: false,
  disclosure:
    "Historical KIT data replay. Predictions are condition-held-out OOF development estimates, not a physics simulation, live monitoring result or protected external test.",
});
