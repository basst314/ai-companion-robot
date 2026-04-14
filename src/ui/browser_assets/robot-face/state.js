export const SCHEMA_VERSION = 1;

export const DEFAULT_STATE = {
  baseVisual: {
    faceScale: 0.96,
    eyeSize: 0.126,
    eyeSpacing: 0.29,
    eyeY: -0.055,
    eyeXOffset: 0.0,
    fillMode: "filled",
    outlineThickness: 20,
    innerCutout: 0.0,
    ringShiftIntensity: 0.43,
    perspectiveIntensity: 0.10,
    roundness: 1.0,
    asymmetry: 0.35,
    glowEnabled: true,
    glowIntensity: 0.38,
    eyeColor: "#48f8ff",
    lidColor: "#48f8ff",
    backgroundColor: "#000000",
    eyeGradientEnabled: false,
    lidGradientEnabled: false,
    gradientStrength: 0.18,
    mouthEnabled: true,
    mouthWidth: 0.07,
    mouthThickness: 7,
    mouthY: 0.21,
    mouthStyle: "tiny-dash",
    mouthMotionStyle: "sine-wave",
    mouthCurveBias: 0.0,
    mouthOpenBias: 0.0,
    sparkleEnabled: false,
    sparkleSize: 0.04,
    sparkleOffsetX: 0.17,
    sparkleOffsetY: -0.11,
    eyeReflectionEnabled: false,
    eyeReflectionSize: 0.07,
    eyeReflectionOffsetX: -0.20,
    eyeReflectionOffsetY: -0.24,
    eyeReflectionOpacity: 0.58,
    outerBoxEnabled: true,
    outerBoxColor: "#d6faff",
    outerBoxWidth: 5,
    outerBoxRadius: 29,
    outerBoxPaddingPx: 5,
    outerBoxPadding: 0.02,
    forwardBounceScale: 0.08,
  },
  expressionModifiers: {
    lidsEnabled: true,
    lidAmount: 0.0,
    lidAngleLeft: 0.0,
    lidAngleRight: 0.0,
    lidLift: 1.0,
    lidInset: 0.36,
    lidSoftness: 0.52,
    squishAmount: 0.0,
    stretchAmount: 0.0,
    cuteMode: 0.08,
    boredIntensity: 0.72,
    curiousIntensity: 0.12,
    tearfulIntensity: 0.0,
    lookX: 0.0,
    lookY: 0.0,
  },
  motionModifiers: {
    trappedMode: false,
    idleEnabled: true,
  },
  timing: {
    masterSpeed: 1.29,
    easeInDuration: 0.09,
    mainMoveDuration: 0.575,
    easeOutDuration: 0.12,
    overshootAmount: 0.24,
    settleAmount: 0.28,
    idleFrequency: 0.26,
    idleIntensity: 0.63,
    blinkSpeed: 0.11,
    blinkHoldDuration: 0.04,
    emotionHoldMin: 1.2,
    emotionHoldMax: 1.89,
    bounceIntensity: 0.42,
    pauseRandomness: 0.54,
    randomnessAmount: 0.86,
    motionSmoothing: 0.28,
    movementAmount: 0.91,
    lookTravelAmount: 1.2,
    trapRoamAmount: 1.2,
    mouthAnimationAmount: 1.2,
    secondaryMicroMotion: true,
  },
  meta: {
    currentPresetName: "Neutral",
    currentStateName: "Neutral Baseline v4",
    currentStateNotes: "Two cyan eyes, slight deadpan, subtle glow, mostly still until a quick move lands.",
    showGuides: false,
  },
};

export const PRESETS = {
  "Neutral": {
    meta: {
      currentStateName: "Neutral Baseline v4",
      currentStateNotes: "Two cyan eyes, slight deadpan, subtle glow, mostly still until a quick move lands.",
    },
  },
  "Deadpan": {
    expressionModifiers: {
      lidAmount: 0.0,
      lidAngleLeft: 0.0,
      lidAngleRight: 0.0,
      cuteMode: 0.0,
      boredIntensity: 0.90,
      curiousIntensity: 0.04,
      lookX: 0.0,
      lookY: 0.0,
    },
    baseVisual: {
      mouthEnabled: true,
      mouthStyle: "flat-line",
      mouthWidth: 0.05,
      glowIntensity: 0.42,
    },
    meta: {
      currentStateName: "Deadpan",
      currentStateNotes: "Dry and still. The face should do almost nothing, then hit one precise glance or blink.",
    },
  },
  "Bored": {
    expressionModifiers: {
      lidAmount: 0.0,
      lidAngleLeft: 0.0,
      lidAngleRight: 0.0,
      boredIntensity: 0.88,
      curiousIntensity: 0.05,
      lookX: -0.08,
      lookY: 0.06,
    },
    baseVisual: {
      mouthEnabled: true,
      mouthStyle: "tiny-dash",
      mouthWidth: 0.10,
      glowIntensity: 0.36,
    },
    timing: {
      masterSpeed: 0.86,
      idleFrequency: 0.26,
      motionSmoothing: 0.36,
    },
    meta: {
      currentStateName: "Bored",
      currentStateNotes: "Lower lids, long pauses, quick side-glances instead of constant motion.",
    },
  },
  "Curious": {
    expressionModifiers: {
      lidAmount: 0.0,
      lidAngleLeft: 0.0,
      lidAngleRight: 0.0,
      cuteMode: 0.1,
      boredIntensity: 0.0,
      curiousIntensity: 0.74,
      lookX: 0.12,
      lookY: -0.22,
    },
    timing: {
      masterSpeed: 1.12,
      overshootAmount: 0.32,
      idleFrequency: 0.48,
    },
    meta: {
      currentStateName: "Curious",
      currentStateNotes: "Alert, upward, slightly asymmetrical, and ready to snap into a new look target.",
    },
  },
  "Cute": {
    baseVisual: {
      eyeSize: 0.19,
      eyeSpacing: 0.26,
      glowIntensity: 0.70,
      mouthEnabled: true,
      mouthStyle: "small-curve",
      mouthWidth: 0.11,
    },
    expressionModifiers: {
      lidAmount: 0.0,
      lidAngleLeft: -0.34,
      lidAngleRight: 0.34,
      cuteMode: 0.82,
      boredIntensity: 0.0,
      curiousIntensity: 0.34,
      lookY: -0.08,
    },
    meta: {
      currentStateName: "Cute",
      currentStateNotes: "Bigger eyes, a little closer together, still robotic, with playful quick snaps rather than babyish softness.",
    },
  },
  "Thinking": {
    baseVisual: {
      mouthEnabled: true,
      mouthStyle: "flat-line",
      mouthWidth: 0.10,
    },
    expressionModifiers: {
      lidAmount: 0.08,
      lidAngleLeft: -0.22,
      lidAngleRight: 0.20,
      boredIntensity: 0.22,
      curiousIntensity: 0.44,
      lookX: 0.16,
      lookY: -0.34,
    },
    timing: {
      idleFrequency: 0.28,
      motionSmoothing: 0.34,
    },
    meta: {
      currentStateName: "Thinking",
      currentStateNotes: "Eyes tip upward with asymmetry. It should feel focused, mildly puzzled, and slightly theatrical.",
    },
  },
  "Mischievous": {
    baseVisual: {
      glowIntensity: 0.68,
      mouthEnabled: true,
      mouthStyle: "small-curve",
      mouthWidth: 0.13,
    },
    expressionModifiers: {
      lidAmount: 0.16,
      lidAngleLeft: -0.26,
      lidAngleRight: 0.18,
      cuteMode: 0.18,
      boredIntensity: 0.18,
      curiousIntensity: 0.52,
      lookX: -0.18,
      lookY: -0.08,
    },
    timing: {
      masterSpeed: 1.16,
      overshootAmount: 0.42,
      bounceIntensity: 0.58,
    },
    meta: {
      currentStateName: "Mischievous",
      currentStateNotes: "A sly tilt with crisp movement. The face should feel like it knows something and might be about to say it.",
    },
  },
  "Sleepy": {
    baseVisual: {
      glowIntensity: 0.30,
      mouthEnabled: true,
      mouthStyle: "small-curve",
      mouthWidth: 0.10,
    },
    expressionModifiers: {
      lidAmount: 0.58,
      lidAngleLeft: -0.06,
      lidAngleRight: 0.06,
      boredIntensity: 0.72,
      curiousIntensity: 0.0,
      lookY: 0.08,
    },
    timing: {
      masterSpeed: 0.72,
      idleFrequency: 0.16,
      motionSmoothing: 0.44,
    },
    meta: {
      currentStateName: "Sleepy",
      currentStateNotes: "Heavy half-lids and long holds. Even the quick movements should feel low-energy.",
    },
  },
  "Alert": {
    baseVisual: {
      eyeSize: 0.175,
      glowIntensity: 0.74,
      mouthEnabled: true,
      mouthStyle: "tiny-dash",
    },
    expressionModifiers: {
      lidAmount: 0.0,
      lidAngleLeft: 0.0,
      lidAngleRight: 0.0,
      boredIntensity: 0.0,
      curiousIntensity: 0.76,
      lookY: 0.0,
    },
    timing: {
      masterSpeed: 1.22,
      overshootAmount: 0.38,
      idleFrequency: 0.52,
      bounceIntensity: 0.54,
    },
    meta: {
      currentStateName: "Alert",
      currentStateNotes: "Bright and ready. Quick glances should feel immediate and efficient, not panicked.",
    },
  },
  "Funny tearful": {
    baseVisual: {
      sparkleEnabled: true,
      sparkleSize: 0.065,
      sparkleOffsetX: 0.19,
      sparkleOffsetY: -0.02,
      mouthEnabled: true,
      mouthStyle: "small-curve",
      glowIntensity: 0.76,
    },
    expressionModifiers: {
      lidAmount: 0.18,
      lidAngleLeft: -0.16,
      lidAngleRight: 0.10,
      cuteMode: 0.24,
      boredIntensity: 0.04,
      curiousIntensity: 0.34,
      tearfulIntensity: 0.82,
      lookX: 0.03,
      lookY: -0.04,
    },
    meta: {
      currentStateName: "Funny tearful",
      currentStateNotes: "A deliberately overdone sparkle moment that reads funny rather than emotional realism.",
    },
  },
  "Oreo vibe": {
    baseVisual: {
      fillMode: "filled",
      eyeColor: "#63f1ff",
      eyeSize: 0.175,
      eyeSpacing: 0.285,
      roundness: 1.0,
      glowEnabled: true,
      glowIntensity: 0.60,
      mouthEnabled: true,
      mouthStyle: "tiny-dash",
      mouthWidth: 0.11,
    },
    expressionModifiers: {
      lidAmount: 0.21,
      lidAngleLeft: -0.10,
      lidAngleRight: 0.06,
      cuteMode: 0.18,
      boredIntensity: 0.26,
      curiousIntensity: 0.26,
      tearfulIntensity: 0.0,
      lookX: 0.0,
      lookY: -0.02,
    },
    timing: {
      masterSpeed: 1.02,
      overshootAmount: 0.28,
      settleAmount: 0.24,
      idleFrequency: 0.34,
      idleIntensity: 0.32,
    },
    meta: {
      currentStateName: "Oreo vibe",
      currentStateNotes: "Balanced and readable. Cute without being babyish, with a slightly dry sense of humor built into the lids.",
    },
  },
};

export function deepClone(value) {
  if (typeof structuredClone === "function") {
    return structuredClone(value);
  }
  return JSON.parse(JSON.stringify(value));
}

export function mergePatch(target, patch) {
  Object.entries(patch || {}).forEach(([key, value]) => {
    if (Array.isArray(value)) {
      target[key] = value.slice();
      return;
    }
    if (value && typeof value === "object") {
      const current = target[key];
      if (!current || typeof current !== "object" || Array.isArray(current)) {
        target[key] = {};
      }
      mergePatch(target[key], value);
      return;
    }
    target[key] = value;
  });
  return target;
}

export function buildState(basePatch = null) {
  const next = deepClone(DEFAULT_STATE);
  if (basePatch) {
    mergePatch(next, basePatch);
  }
  return next;
}

export function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

export function lerp(a, b, t) {
  return a + ((b - a) * t);
}

export function easeOutCubic(t) {
  const x = clamp(t, 0, 1);
  return 1 - Math.pow(1 - x, 3);
}

export function easeInOutCubic(t) {
  const x = clamp(t, 0, 1);
  if (x < 0.5) {
    return 4 * x * x * x;
  }
  return 1 - (Math.pow(-2 * x + 2, 3) / 2);
}

export function pickWeighted(items, rng = Math.random) {
  const total = items.reduce((sum, item) => sum + item.weight, 0);
  let cursor = rng() * total;
  for (const item of items) {
    cursor -= item.weight;
    if (cursor <= 0) {
      return item;
    }
  }
  return items[items.length - 1];
}
