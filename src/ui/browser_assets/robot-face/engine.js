import {
  PRESETS,
  buildState,
  clamp,
  deepClone,
  easeInOutCubic,
  easeOutCubic,
  lerp,
  mergePatch,
  pickWeighted,
} from "./state.js";

function createSeededRandom(seed) {
  if (seed == null) {
    return Math.random;
  }
  let state = Math.floor(seed) || 1;
  return () => {
    state = (state * 1664525 + 1013904223) >>> 0;
    return state / 4294967296;
  };
}

function hexToRgb(value) {
  const normalized = String(value || "#000000").replace("#", "").trim();
  const hex = normalized.length === 3
    ? normalized.split("").map((part) => part + part).join("")
    : normalized;
  const int = Number.parseInt(hex, 16);
  return {
    r: (int >> 16) & 255,
    g: (int >> 8) & 255,
    b: int & 255,
  };
}

const RGBA_CACHE = new Map();

function rgba(hex, alpha) {
  const safeAlpha = Math.round(clamp(alpha, 0, 1) * 1000) / 1000;
  const key = `${hex}|${safeAlpha}`;
  const cached = RGBA_CACHE.get(key);
  if (cached) {
    return cached;
  }
  const { r, g, b } = hexToRgb(hex);
  const value = `rgba(${r}, ${g}, ${b}, ${safeAlpha})`;
  RGBA_CACHE.set(key, value);
  return value;
}

function ellipsePath(ctx, x, y, rx, ry) {
  ctx.beginPath();
  ctx.ellipse(x, y, rx, ry, 0, 0, Math.PI * 2);
}

function roundedRectPath(ctx, x, y, width, height, radius) {
  const r = clamp(radius, 0, Math.min(width, height) / 2);
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + width - r, y);
  ctx.quadraticCurveTo(x + width, y, x + width, y + r);
  ctx.lineTo(x + width, y + height - r);
  ctx.quadraticCurveTo(x + width, y + height, x + width - r, y + height);
  ctx.lineTo(x + r, y + height);
  ctx.quadraticCurveTo(x, y + height, x, y + height - r);
  ctx.lineTo(x, y + r);
  ctx.quadraticCurveTo(x, y, x + r, y);
  ctx.closePath();
}

export class RobotFaceEngine {
  constructor({ canvas, initialState = null, seed = null } = {}) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d", { alpha: false, desynchronized: true });
    this.random = createSeededRandom(seed);
    this.baseState = buildState(initialState);
    this.runtimeOverrides = {};
    this.externalState = {
      scene: "face",
      lifecycle: "idle",
      emotion: "neutral",
      previewText: null,
    };
    this.idlePolicy = {
      enabled: this.baseState.motionModifiers.idleEnabled,
      frequency: this.baseState.timing.idleFrequency,
      intensity: this.baseState.timing.idleIntensity,
      pauseRandomness: this.baseState.timing.pauseRandomness,
      secondaryMicroMotion: this.baseState.timing.secondaryMicroMotion,
      allowedBehaviors: [
        "blink",
        "quick_glance",
        "bored",
        "cute",
        "thinking",
        "scoot",
      ],
    };
    this.runtime = {
      width: 0,
      height: 0,
      dpr: 1,
      lastTime: 0,
      nextIdleAt: 0,
      nextSpeakingBlinkAt: 0,
      recentInteractionAt: 0,
      recentExternalInteractionAt: 0,
      suppressMicroUntil: 0,
      expressionOverride: null,
      renderPose: null,
      activeClips: [],
      micro: {
        x: 0,
        y: 0,
        lookX: 0,
        lookY: 0,
        targetX: 0,
        targetY: 0,
        targetLookX: 0,
        targetLookY: 0,
        nextTargetAt: 0,
      },
      trap: {
        x: 0,
        y: 0,
        vx: 0,
        vy: 0,
        scale: 0,
        scaleVelocity: 0,
        impact: 0,
        impactAxis: "x",
        activeUntil: 0,
        lastImpactAt: 0,
      },
      border: {
        targetLevel: 0,
        currentLevel: 0,
        lastLevelAt: 0,
      },
      sleepSparkles: [],
      nextSleepSparkleAt: 0,
      composedState: null,
      composedStateKey: "",
      geometry: {
        boxRect: null,
        boxRectKey: "",
      },
    };
    this._glowSpriteCache = new Map();
    this._maxGlowSpriteCacheEntries = 48;
    this._rafId = 0;
    this._tick = this._tick.bind(this);
    this.resize();
    const now = performance.now() / 1000;
    this.runtime.lastTime = now;
    this.runtime.recentInteractionAt = now;
    this.runtime.recentExternalInteractionAt = now;
    this.runtime.nextIdleAt = now + 2.0;
    this.runtime.nextSpeakingBlinkAt = now + 1.4;
    this.runtime.micro.nextTargetAt = now + 1.2;
  }

  start() {
    if (this._rafId) {
      return;
    }
    this.resize();
    this._rafId = window.requestAnimationFrame(this._tick);
  }

  stop() {
    if (!this._rafId) {
      return;
    }
    window.cancelAnimationFrame(this._rafId);
    this._rafId = 0;
  }

  resize() {
    const nextDpr = window.devicePixelRatio || 1;
    const nextWidth = Math.max(320, window.innerWidth || this.canvas.clientWidth || 320);
    const nextHeight = Math.max(220, window.innerHeight || this.canvas.clientHeight || 220);
    const nextCanvasWidth = Math.floor(nextWidth * nextDpr);
    const nextCanvasHeight = Math.floor(nextHeight * nextDpr);
    if (
      this.runtime.dpr === nextDpr &&
      this.runtime.width === nextWidth &&
      this.runtime.height === nextHeight &&
      this.canvas.width === nextCanvasWidth &&
      this.canvas.height === nextCanvasHeight
    ) {
      return;
    }
    this.runtime.dpr = nextDpr;
    this.runtime.width = nextWidth;
    this.runtime.height = nextHeight;
    this.canvas.width = nextCanvasWidth;
    this.canvas.height = nextCanvasHeight;
    this.ctx.setTransform(this.runtime.dpr, 0, 0, this.runtime.dpr, 0, 0);
    this.runtime.geometry.boxRect = null;
    this.runtime.geometry.boxRectKey = "";
    this._glowSpriteCache.clear();
  }

  setRendererConfig(config = {}) {
    if (config.stateOverride && typeof config.stateOverride === "object") {
      this.applyStateOverride(config.stateOverride);
    }
    if (config.idlePolicy && typeof config.idlePolicy === "object") {
      this.setIdlePolicy(config.idlePolicy);
    }
  }

  applyStateOverride(override) {
    const next = buildState();
    mergePatch(next, override);
    this.baseState = next;
    this.invalidateComposedState();
  }

  setIdlePolicy(policy = {}) {
    this.idlePolicy = {
      ...this.idlePolicy,
      ...policy,
      allowedBehaviors: Array.isArray(policy.allowedBehaviors)
        ? policy.allowedBehaviors.slice()
        : this.idlePolicy.allowedBehaviors.slice(),
    };
    this.runtimeOverrides = {
      motionModifiers: {
        idleEnabled: Boolean(this.idlePolicy.enabled),
      },
      timing: {
        idleFrequency: this.idlePolicy.frequency,
        idleIntensity: this.idlePolicy.intensity,
        pauseRandomness: this.idlePolicy.pauseRandomness,
        secondaryMicroMotion: Boolean(this.idlePolicy.secondaryMicroMotion),
      },
    };
    this.invalidateComposedState();
    this.markInteraction({ external: false });
  }

  setExternalState(next = {}) {
    const prevLifecycle = this.externalState.lifecycle;
    const prevScene = this.externalState.scene;
    this.externalState = {
      ...this.externalState,
      ...next,
    };
    this.invalidateComposedState();
    if (prevLifecycle !== "idle" && this.externalState.lifecycle === "idle") {
      this.runtime.border.targetLevel = 0;
      this.runtime.border.lastLevelAt = 0;
    }
    if (
      this.externalState.lifecycle !== prevLifecycle ||
      this.externalState.scene !== prevScene ||
      this.externalState.lifecycle === "speaking"
    ) {
      this.markInteraction();
    }
  }

  onMicLevel(level) {
    const parsed = Number(level);
    this.runtime.border.targetLevel = Number.isFinite(parsed) ? clamp(parsed, 0, 1) : 0;
    this.runtime.border.lastLevelAt = performance.now() / 1000;
  }

  buildCurrentState() {
    return deepClone(this.baseState);
  }

  invalidateComposedState() {
    this.runtime.composedState = null;
    this.runtime.composedStateKey = "";
  }

  clearActiveMotion() {
    this.runtime.activeClips = [];
    this.runtime.trap.vx = 0;
    this.runtime.trap.vy = 0;
    this.runtime.trap.x = 0;
    this.runtime.trap.y = 0;
    this.runtime.trap.scale = 0;
    this.runtime.trap.scaleVelocity = 0;
    this.runtime.trap.impact = 0;
    this.runtime.trap.activeUntil = 0;
    this.runtime.trap.lastImpactAt = 0;
    this.markInteraction();
  }

  getActiveBehaviorLabel() {
    const override = this.getActiveExpressionOverride();
    if (override) {
      return override.label || override.name || "";
    }
    const now = performance.now() / 1000;
    const active = this.runtime.activeClips
      .filter((clip) => now >= clip.startAt && now <= clip.startAt + clip.duration)
      .map((clip) => clip.name)
      .filter(Boolean);
    if (!active.length) {
      return "";
    }
    return active[active.length - 1];
  }

  triggerNamedBehavior(name, payload = {}) {
    switch (String(name || "").toLowerCase()) {
      case "blink":
        this.triggerBlink(0, payload.depth ?? 1, payload.label || "Blink");
        return;
      case "double_blink":
        this.triggerDoubleBlink();
        return;
      case "look_left":
        this.triggerLook(-0.92, 0, "Look left");
        return;
      case "look_right":
        this.triggerLook(0.92, 0, "Look right");
        return;
      case "look_up":
        this.triggerLook(0, -0.92, "Look up");
        return;
      case "look_down":
        this.triggerLook(0, 0.92, "Look down");
        return;
      case "quick_glance":
        this.triggerQuickGlance();
        return;
      case "bored":
      case "bored_half_lid":
        this.triggerBoredHalfLid();
        return;
      case "curious":
      case "curious_look":
        this.triggerCuriousLook();
        return;
      case "cute":
      case "cute_mode":
        this.triggerCuteMode();
        return;
      case "thinking":
        this.triggerThinking();
        return;
      case "attention_mode":
      case "wake_attention":
        this.triggerAttentionMode();
        return;
      case "surprise":
        this.triggerSurprise();
        return;
      case "deadpan":
      case "deadpan_stare":
        this.triggerDeadpanStare();
        return;
      case "sleep":
      case "sleeping":
      case "sleepy_close":
        this.triggerSleepyClose();
        return;
      case "scoot":
        this.triggerScoot();
        return;
      case "boundary_press":
        this.triggerBoundaryPress();
        return;
      default:
        return;
    }
  }

  markInteraction({ external = true } = {}) {
    const now = performance.now() / 1000;
    this.runtime.recentInteractionAt = now;
    if (external) {
      this.runtime.recentExternalInteractionAt = now;
    }
    this.runtime.nextIdleAt = now + 1.8 + (this.random() * 0.9);
  }

  getActiveExpressionOverride(now = performance.now() / 1000) {
    const override = this.runtime.expressionOverride;
    if (!override) {
      return null;
    }
    if (override.until != null && now >= override.until) {
      this.runtime.expressionOverride = null;
      this.invalidateComposedState();
      return null;
    }
    return override;
  }

  setExpressionOverride(payload = {}) {
    const rawName = String(payload.name || payload.animation || "").toLowerCase();
    const now = performance.now() / 1000;
    if (!rawName || rawName === "speaking" || rawName === "reset" || rawName === "normal") {
      this.runtime.expressionOverride = null;
      this.invalidateComposedState();
      this.markInteraction();
      return;
    }
    const known = new Set(["curious", "cute", "thinking", "deadpan", "sleeping"]);
    if (!known.has(rawName)) {
      return;
    }
    const duration = Number(payload.durationSeconds);
    const safeDuration = Number.isFinite(duration) ? clamp(duration, 0.5, 10) : 4;
    this.runtime.expressionOverride = {
      name: rawName,
      label: payload.label || this.labelForExpressionOverride(rawName),
      until: now + safeDuration,
      reason: payload.reason || "expression_override",
      direction: this.random() > 0.5 ? 1 : -1,
    };
    this.runtime.activeClips = [];
    this.invalidateComposedState();
    this.markInteraction();
  }

  labelForExpressionOverride(name) {
    switch (name) {
      case "curious":
        return "Curious";
      case "cute":
        return "Cute";
      case "thinking":
        return "Thinking";
      case "deadpan":
        return "Deadpan";
      case "sleeping":
        return "Sleeping";
      default:
        return "";
    }
  }

  resolveBurstTiming(state, extraHold = 0, holdScale = 1) {
    const speed = clamp(state.timing.masterSpeed, 0.2, 3);
    const attack = state.timing.easeInDuration / speed;
    const move = state.timing.mainMoveDuration / speed;
    const holdRandom = lerp(state.timing.emotionHoldMin, state.timing.emotionHoldMax, this.random());
    const hold = (
      extraHold +
      (holdRandom * holdScale * lerp(0.2, 1, state.timing.pauseRandomness)) +
      (state.timing.pauseRandomness * 0.06 * this.random())
    ) / speed;
    const release = state.timing.easeOutDuration / speed;
    const settle = (0.04 + (state.timing.settleAmount * 0.18)) / speed;
    const total = attack + move + hold + release + settle;
    return { attack, move, hold, release, settle, total };
  }

  sampleBurst(elapsed, timing, state) {
    const overshoot = state.timing.overshootAmount;
    if (elapsed < timing.attack) {
      return easeOutCubic(elapsed / timing.attack);
    }
    if (elapsed < timing.attack + timing.move) {
      const local = (elapsed - timing.attack) / timing.move;
      return 1 + (Math.sin(local * Math.PI) * overshoot * 0.34);
    }
    if (elapsed < timing.attack + timing.move + timing.hold) {
      return 1 - (overshoot * 0.06);
    }
    if (elapsed < timing.attack + timing.move + timing.hold + timing.release) {
      const local = (elapsed - timing.attack - timing.move - timing.hold) / timing.release;
      return 1 - easeInOutCubic(local);
    }
    const settleElapsed = elapsed - timing.attack - timing.move - timing.hold - timing.release;
    const settleLocal = clamp(settleElapsed / timing.settle, 0, 1);
    const wiggle = Math.sin((1 - settleLocal) * Math.PI * 1.2) * state.timing.settleAmount * 0.12;
    return wiggle * (1 - settleLocal);
  }

  sampleCleanHold(elapsed, timing) {
    if (elapsed < timing.attack) {
      return easeOutCubic(elapsed / timing.attack);
    }
    if (elapsed < timing.attack + timing.move + timing.hold) {
      return 1;
    }
    if (elapsed < timing.attack + timing.move + timing.hold + timing.release) {
      const local = (elapsed - timing.attack - timing.move - timing.hold) / timing.release;
      return 1 - easeInOutCubic(local);
    }
    return 0;
  }

  queueClip(clip) {
    this.runtime.activeClips.push({
      ...clip,
      startAt: clip.startAt || (performance.now() / 1000),
    });
  }

  pushTowardBoundary(dx, dy, power = 1, options = {}) {
    const state = this._composedState();
    const strength = state.timing.movementAmount * state.timing.lookTravelAmount;
    this.runtime.trap.activeUntil = Math.max(
      this.runtime.trap.activeUntil,
      (performance.now() / 1000) + (options.duration || 1.8),
    );
    this.runtime.trap.vx += dx * power * strength * 0.38;
    this.runtime.trap.vy += dy * power * strength * 0.28;
    if (options.forward) {
      this.runtime.trap.scaleVelocity += options.forward * state.baseVisual.forwardBounceScale * 3.8;
    }
  }

  triggerBlink(delay = 0, depth = 1, label = "Blink", options = {}) {
    const state = this._composedState();
    this.markInteraction({ external: !options.idle });
    const speed = clamp(state.timing.masterSpeed, 0.2, 3);
    const closeTime = clamp(state.timing.blinkSpeed / speed, 0.03, 0.22);
    const hold = clamp(state.timing.blinkHoldDuration / speed, 0, 0.24);
    const openTime = closeTime * 0.92;
    const duration = closeTime + hold + openTime;
    this.queueClip({
      name: label,
      startAt: (performance.now() / 1000) + delay,
      duration,
      sample(elapsed) {
        let closedness = 0;
        if (elapsed < closeTime) {
          closedness = easeOutCubic(elapsed / closeTime) * depth;
        } else if (elapsed < closeTime + hold) {
          closedness = depth;
        } else {
          const local = (elapsed - closeTime - hold) / openTime;
          closedness = (1 - easeInOutCubic(local)) * depth;
        }
        return {
          blinkClosedness: closedness,
          lidAmount: closedness * depth,
          mouthOpen: Math.max(0, (closedness - 0.2) * 0.12),
        };
      },
    });
  }

  triggerDoubleBlink(options = {}) {
    const state = this._composedState();
    const speed = clamp(state.timing.masterSpeed, 0.2, 3);
    const gap = 0.26 / speed;
    this.triggerBlink(0, 1, "Double blink", options);
    this.triggerBlink(gap, 0.94, "Double blink", options);
  }

  triggerLook(dx, dy, label, options = {}) {
    const state = this._composedState();
    this.markInteraction({ external: !options.idle });
    const randomness = state.timing.randomnessAmount;
    const driftX = dx + ((this.random() - 0.5) * 0.18 * randomness);
    const driftY = dy + ((this.random() - 0.5) * 0.14 * randomness);
    const holdScale = options.holdScale == null ? 1 : options.holdScale;
    const timing = this.resolveBurstTiming(state, options.hold || 0.04, holdScale);
    this.queueClip({
      name: label,
      duration: timing.total,
      sample: (elapsed) => {
        const amount = this.sampleCleanHold(elapsed, timing);
        return {
          lookX: driftX * amount * (options.strength || 1),
          lookY: driftY * amount * (options.strength || 1),
          motionX: driftX * amount * 0.24,
          motionY: driftY * amount * 0.16,
          eyeOpenBoost: amount * 0.68,
          lidOverride: 0,
          mouthOpen: Math.abs(driftX) * amount * 0.05,
        };
      },
    });
    this.pushTowardBoundary(driftX, driftY, options.wallHit ? 1.65 : 0.68, {
      duration: timing.total + 0.55,
      forward: options.forward || 0,
    });
    if ((timing.hold > 0.55) && this.random() < 0.42) {
      this.triggerBlink(timing.attack + (timing.move * 0.65), 0.86, `${label} blink`);
    }
  }

  triggerQuickGlance(options = {}) {
    const state = this._composedState();
    this.markInteraction({ external: !options.idle });
    const first = this.random() > 0.5 ? 1 : -1;
    const now = performance.now() / 1000;
    const timing = this.resolveBurstTiming(state, 0.04, 1);
    const sideOneIn = Math.max(0.05, timing.attack * 0.65);
    const sideOneHold = timing.hold;
    const crossTime = Math.max(0.08, timing.move * 0.7);
    const sideTwoHold = timing.hold;
    const centerReturn = Math.max(0.06, timing.release * 0.72);
    const total = sideOneIn + sideOneHold + crossTime + sideTwoHold + centerReturn;
    this.runtime.suppressMicroUntil = now + total + 0.24;
    this.runtime.micro.x = 0;
    this.runtime.micro.y = 0;
    this.runtime.micro.lookX = 0;
    this.runtime.micro.lookY = 0;
    this.runtime.micro.targetX = 0;
    this.runtime.micro.targetY = 0;
    this.runtime.micro.targetLookX = 0;
    this.runtime.micro.targetLookY = 0;
    this.queueClip({
      name: "Quick glance left/right",
      startAt: now,
      duration: total,
      sample(elapsed) {
        let lookX = 0;
        let motionX = 0;
        let eyeOpenBoost = 0.72;
        if (elapsed < sideOneIn) {
          const t = easeOutCubic(elapsed / sideOneIn);
          lookX = first * 0.98 * t;
          motionX = first * 0.16 * t;
        } else if (elapsed < sideOneIn + sideOneHold) {
          lookX = first * 0.98;
          motionX = first * 0.16;
        } else if (elapsed < sideOneIn + sideOneHold + crossTime) {
          const local = (elapsed - sideOneIn - sideOneHold) / crossTime;
          const t = easeInOutCubic(local);
          lookX = lerp(first * 0.98, -first * 0.98, t);
          motionX = lerp(first * 0.16, -first * 0.16, t);
        } else if (elapsed < sideOneIn + sideOneHold + crossTime + sideTwoHold) {
          lookX = -first * 0.98;
          motionX = -first * 0.16;
        } else {
          const local = (elapsed - sideOneIn - sideOneHold - crossTime - sideTwoHold) / centerReturn;
          const t = easeInOutCubic(clamp(local, 0, 1));
          lookX = lerp(-first * 0.98, 0, t);
          motionX = lerp(-first * 0.16, 0, t);
          eyeOpenBoost = lerp(0.72, 0.56, t);
        }
        return {
          lookX,
          motionX,
          eyeOpenBoost,
          lidOverride: 0,
        };
      },
    });
  }

  triggerBoredHalfLid(options = {}) {
    const state = this._composedState();
    this.markInteraction({ external: !options.idle });
    const holdSeconds = Number(options.holdSeconds);
    const timing = Number.isFinite(holdSeconds)
      ? this.resolveBurstTiming(state, Math.max(0.2, holdSeconds), 0)
      : this.resolveBurstTiming(state, 0.26, 1.2);
    this.queueClip({
      name: "Bored half-lid",
      duration: timing.total + 0.12,
      sample(elapsed) {
        let amount = 0;
        if (elapsed < timing.attack) {
          amount = easeOutCubic(elapsed / timing.attack);
        } else if (elapsed < timing.attack + timing.move + timing.hold) {
          amount = 1;
        } else if (elapsed < timing.attack + timing.move + timing.hold + timing.release) {
          const local = (elapsed - timing.attack - timing.move - timing.hold) / timing.release;
          amount = 1 - easeInOutCubic(local);
        }
        return {
          lidOverride: 0.48 * amount,
          boredIntensity: amount * 0.42,
          lookX: -0.12 * amount,
          lookY: 0.08 * amount,
          mouthSmile: -amount * 0.22,
        };
      },
    });
  }

  triggerCuriousLook(options = {}) {
    const state = this._composedState();
    this.markInteraction({ external: !options.idle });
    const first = this.random() > 0.5 ? 1 : -1;
    const timing = this.resolveBurstTiming(state, 0.10, 1.05);
    const sideIn = Math.max(0.06, timing.attack * 0.72);
    const sideHold = timing.hold * 0.95;
    const crossTime = Math.max(0.10, timing.move * 0.78);
    const otherHold = timing.hold * 0.95;
    const returnTime = Math.max(0.08, timing.release * 0.82);
    const total = sideIn + sideHold + crossTime + otherHold + returnTime;
    this.queueClip({
      name: "Curious look",
      duration: total,
      sample(elapsed) {
        let phase = 0;
        if (elapsed < sideIn) {
          phase = first * easeOutCubic(elapsed / sideIn);
        } else if (elapsed < sideIn + sideHold) {
          phase = first;
        } else if (elapsed < sideIn + sideHold + crossTime) {
          const local = (elapsed - sideIn - sideHold) / crossTime;
          phase = lerp(first, -first, easeInOutCubic(local));
        } else if (elapsed < sideIn + sideHold + crossTime + otherHold) {
          phase = -first;
        } else {
          const local = (elapsed - sideIn - sideHold - crossTime - otherHold) / returnTime;
          phase = lerp(-first, 0, easeInOutCubic(clamp(local, 0, 1)));
        }
        const amount = Math.abs(phase);
        const tilt = phase;
        const favorLeft = tilt < 0 ? 1 : 0;
        const favorRight = tilt > 0 ? 1 : 0;
        return {
          curiousIntensity: amount * 0.82,
          lookX: tilt * 0.18,
          lookY: amount * -0.34,
          motionX: tilt * 0.12,
          motionY: amount * -0.05,
          faceTilt: tilt * 0.24,
          eyeOpenBoost: amount * 0.56,
          lidAngleLeft: tilt * -0.16,
          lidAngleRight: tilt * -0.16,
          rightSizeBias: (favorRight * 0.10 * amount) + (favorLeft * -0.04 * amount),
          leftSizeBias: (favorLeft * 0.10 * amount) + (favorRight * -0.04 * amount),
          leftLidBias: favorRight * -0.05 * amount,
          rightLidBias: favorLeft * -0.05 * amount,
          mouthSmile: amount * 0.10,
          mouthWidthBias: amount * 0.08,
        };
      },
    });
    this.pushTowardBoundary(first * 0.16, -0.10, 0.72, { duration: total + 0.3, forward: 0.015 });
  }

  triggerCuteMode(options = {}) {
    const state = this._composedState();
    this.markInteraction({ external: !options.idle });
    const holdSeconds = Number(options.holdSeconds);
    const timing = Number.isFinite(holdSeconds)
      ? this.resolveBurstTiming(state, Math.max(0.2, holdSeconds), 0)
      : this.resolveBurstTiming(state, 0.20, 1.12);
    const direction = this.random() > 0.5 ? 1 : -1;
    this.queueClip({
      name: "Cute mode",
      duration: timing.total,
      sample: (elapsed) => {
        const amount = this.sampleBurst(elapsed, timing, state);
        return {
          cuteMode: amount * 0.86,
          eyeOpenBoost: amount * 0.44,
          lookX: direction * amount * 0.18,
          lookY: amount * -0.12,
          lidAngleLeft: amount * 0.34,
          lidAngleRight: amount * -0.34,
          stretchAmount: amount * 0.10,
          tearfulIntensity: amount * 0.12,
          leftSizeBias: amount * 0.08,
          rightSizeBias: amount * 0.08,
          mouthSmile: amount * 0.34,
          mouthOpen: amount * 0.10,
        };
      },
    });
  }

  triggerThinking(options = {}) {
    const state = this._composedState();
    this.markInteraction({ external: !options.idle });
    const first = this.random() > 0.5 ? 1 : -1;
    const timing = this.resolveBurstTiming(state, 0.12, 1.05);
    const sideIn = Math.max(0.06, timing.attack * 0.72);
    const configuredSideHold = Number(options.sideHoldSeconds);
    const sideHold = Number.isFinite(configuredSideHold)
      ? clamp(configuredSideHold, 0.25, 6)
      : timing.hold * 0.9;
    const crossTime = Math.max(0.10, timing.move * 0.75);
    const otherHold = sideHold;
    const returnTime = Math.max(0.08, timing.release * 0.8);
    const total = sideIn + sideHold + crossTime + otherHold + returnTime;
    this.queueClip({
      name: "Thinking",
      duration: total,
      sample(elapsed) {
        let phase = 0;
        let lookX = 0;
        if (elapsed < sideIn) {
          const t = easeOutCubic(elapsed / sideIn);
          phase = first * t;
          lookX = first * 0.16 * t;
        } else if (elapsed < sideIn + sideHold) {
          phase = first;
          lookX = first * 0.16;
        } else if (elapsed < sideIn + sideHold + crossTime) {
          const local = (elapsed - sideIn - sideHold) / crossTime;
          const t = easeInOutCubic(local);
          phase = lerp(first, -first, t);
          lookX = lerp(first * 0.16, -first * 0.16, t);
        } else if (elapsed < sideIn + sideHold + crossTime + otherHold) {
          phase = -first;
          lookX = -first * 0.16;
        } else {
          const local = (elapsed - sideIn - sideHold - crossTime - otherHold) / returnTime;
          const t = easeInOutCubic(clamp(local, 0, 1));
          phase = lerp(-first, 0, t);
          lookX = lerp(-first * 0.16, 0, t);
        }
        const amount = Math.abs(phase);
        const favorLeft = phase < 0 ? 1 : 0;
        const favorRight = phase > 0 ? 1 : 0;
        return {
          curiousIntensity: amount * 0.42,
          boredIntensity: amount * 0.18,
          lookX,
          lookY: amount * -0.42,
          lidAngleLeft: lerp(0, -0.22, favorRight) + lerp(0, 0.18, favorLeft),
          lidAngleRight: lerp(0, -0.22, favorLeft) + lerp(0, 0.18, favorRight),
          leftLidBias: (favorRight * 0.18 * amount) + (favorLeft * -0.06 * amount),
          rightLidBias: (favorLeft * 0.18 * amount) + (favorRight * -0.06 * amount),
          leftSizeBias: (favorLeft * 0.12 * amount) + (favorRight * -0.05 * amount),
          rightSizeBias: (favorRight * 0.12 * amount) + (favorLeft * -0.05 * amount),
          mouthSmile: -amount * 0.10,
          mouthOpen: amount * 0.04,
        };
      },
    });
    this.pushTowardBoundary(first * 0.12, -0.16, 0.72, { duration: total + 0.3, forward: 0.01 });
  }

  triggerAttentionMode(options = {}) {
    const state = this._composedState();
    this.markInteraction({ external: !options.idle });
    const timing = this.resolveBurstTiming(state, 0.55, 1.2);
    this.queueClip({
      name: "Attention mode",
      duration: timing.total + 0.15,
      sample: (elapsed) => {
        const amount = this.sampleBurst(elapsed, timing, state);
        return {
          eyeOpenBoost: amount * 0.70,
          boredIntensity: -amount * 0.90,
          curiousIntensity: amount * 0.12,
          leftSizeBias: amount * 0.04,
          rightSizeBias: amount * 0.04,
          lookX: 0,
          lookY: 0,
          lidAngleLeft: 0,
          lidAngleRight: 0,
          mouthSmile: amount * 0.06,
          mouthWidthBias: amount * 0.10,
          mouthStill: amount,
        };
      },
    });
  }

  triggerSurprise(options = {}) {
    const state = this._composedState();
    this.markInteraction({ external: !options.idle });
    const timing = this.resolveBurstTiming(state, 0.08, 0.55);
    this.queueClip({
      name: "Surprise",
      duration: timing.total,
      sample: (elapsed) => {
        const amount = this.sampleBurst(elapsed, timing, state);
        return {
          eyeOpenBoost: amount * 0.86,
          stretchAmount: amount * 0.62,
          motionY: amount * -0.10,
          cuteMode: amount * 0.10,
          tearfulIntensity: amount * 0.55,
          mouthOpen: amount * 0.78,
          mouthSmile: amount * 0.04,
        };
      },
    });
    this.pushTowardBoundary(0, -0.12, 0.45, { duration: timing.total + 0.2, forward: 0.065 });
  }

  triggerDeadpanStare(options = {}) {
    const state = this._composedState();
    this.markInteraction({ external: !options.idle });
    const timing = this.resolveBurstTiming(state, 0.30, 1.5);
    this.queueClip({
      name: "Deadpan stare",
      duration: timing.total + 0.18,
      sample(elapsed) {
        let amount = 0;
        if (elapsed < timing.attack) {
          amount = easeOutCubic(elapsed / timing.attack);
        } else if (elapsed < timing.attack + timing.move + timing.hold) {
          amount = 1;
        } else if (elapsed < timing.attack + timing.move + timing.hold + timing.release) {
          const local = (elapsed - timing.attack - timing.move - timing.hold) / timing.release;
          amount = 1 - easeInOutCubic(local);
        }
        return {
          boredIntensity: amount * 1.0,
          lidOverride: 0.60 * amount,
          motionX: 0,
          lookX: 0,
          lookY: 0,
          mouthSmile: -amount * 0.18,
          mouthStill: amount,
          mouthWidthBias: -amount * 0.10,
        };
      },
    });
  }

  triggerScoot(options = {}) {
    const state = this._composedState();
    this.markInteraction({ external: !options.idle });
    const holdSeconds = Number(options.holdSeconds);
    const timing = Number.isFinite(holdSeconds)
      ? this.resolveBurstTiming(state, Math.max(0.2, holdSeconds), 0)
      : this.resolveBurstTiming(state, 0.04, 0.28);
    const randomness = state.timing.randomnessAmount;
    const direction = this.random() > 0.5 ? 1 : -1;
    const vertical = (this.random() - 0.5) * lerp(0.16, 0.35, randomness);
    const lateral = direction * lerp(0.12, 0.26, randomness);
    this.queueClip({
      name: "Tiny scoot / swoosh",
      duration: timing.total,
      sample: (elapsed) => {
        const amount = this.sampleBurst(elapsed, timing, state);
        return {
          motionX: lateral * amount,
          motionY: vertical * amount * 0.18,
          squishAmount: Math.abs(amount) * 0.14,
          stretchAmount: Math.abs(amount) * 0.12,
          mouthOpen: Math.abs(amount) * 0.05,
        };
      },
    });
    this.pushTowardBoundary(direction, vertical, 0.86, { duration: timing.total + 0.28, forward: 0.008 });
  }

  triggerSleepyClose(options = {}) {
    const state = this._composedState();
    this.markInteraction({ external: !options.idle });
    const timing = this.resolveBurstTiming(state, 0.85, 1.6);
    this.queueClip({
      name: "Sleep / closed eyes",
      duration: timing.total + 0.3,
      sample: (elapsed) => {
        const amount = this.sampleBurst(elapsed, timing, state);
        return {
          lidOverride: amount * 1.08,
          boredIntensity: amount * 0.40,
          lookX: 0,
          lookY: 0,
          mouthSmile: -amount * 0.08,
          mouthOpen: amount * 0.02,
          mouthStill: amount,
          sleepLine: amount,
        };
      },
    });
  }

  triggerBoundaryPress(options = {}) {
    const state = this._composedState();
    this.markInteraction({ external: !options.idle });
    const edges = [
      { dx: -1, dy: 0, squishX: 0.22, squishY: -0.12 },
      { dx: 1, dy: 0, squishX: 0.22, squishY: -0.12 },
      { dx: 0, dy: -1, squishX: -0.12, squishY: 0.22 },
      { dx: 0, dy: 1, squishX: -0.08, squishY: 0.18 },
    ];
    const edge = edges[Math.floor(this.random() * edges.length)];
    const timing = this.resolveBurstTiming(state, 0.55, 1.3);
    this.queueClip({
      name: "Boundary press",
      duration: timing.total + 0.2,
      sample: (elapsed) => {
        const amount = this.sampleBurst(elapsed, timing, state);
        return {
          lookX: edge.dx * amount * 0.85,
          lookY: edge.dy * amount * 0.75,
          squishAmount: Math.max(0, edge.squishX) * amount,
          stretchAmount: Math.max(0, edge.squishY) * amount,
          leftSizeBias: amount * (edge.dx < 0 ? 0.08 : -0.02),
          rightSizeBias: amount * (edge.dx > 0 ? 0.08 : -0.02),
          mouthSmile: amount * 0.08,
        };
      },
    });
    this.pushTowardBoundary(edge.dx, edge.dy, 1.9, { duration: timing.total + 0.8, forward: 0.03 });
  }

  _composePresetPatch() {
    const override = this.getActiveExpressionOverride();
    if (override) {
      switch (override.name) {
        case "curious":
          return PRESETS["Curious"];
        case "thinking":
          return PRESETS["Thinking"];
        case "deadpan":
          return PRESETS["Deadpan"];
        case "sleeping":
          return PRESETS["Sleepy"];
        default:
          break;
      }
    }
    if (this.externalState.scene === "sleep") {
      return PRESETS["Sleepy"];
    }
    if (this.externalState.emotion === "thinking") {
      return PRESETS["Thinking"];
    }
    if (this.externalState.emotion === "curious") {
      return PRESETS["Curious"];
    }
    return null;
  }

  _composedState() {
    const override = this.getActiveExpressionOverride();
    const key = [
      this.externalState.scene,
      this.externalState.lifecycle,
      this.externalState.emotion,
      override ? override.name : "override-none",
      this.idlePolicy.enabled ? "idle-on" : "idle-off",
      this.idlePolicy.frequency,
      this.idlePolicy.intensity,
      this.idlePolicy.pauseRandomness,
      this.idlePolicy.secondaryMicroMotion ? "micro-on" : "micro-off",
    ].join("|");
    if (this.runtime.composedState && this.runtime.composedStateKey === key) {
      return this.runtime.composedState;
    }
    const next = deepClone(this.baseState);
    mergePatch(next, this.runtimeOverrides);
    const presetPatch = this._composePresetPatch();
    if (presetPatch) {
      mergePatch(next, presetPatch);
    }
    this.runtime.composedState = next;
    this.runtime.composedStateKey = key;
    return next;
  }

  updateIdleScheduler(now, state) {
    if (!this.idlePolicy.enabled || !state.motionModifiers.idleEnabled) {
      return;
    }
    if (this.getActiveExpressionOverride(now)) {
      return;
    }
    if (this.externalState.scene !== "face" || this.externalState.lifecycle !== "idle" || this.externalState.lifecycle === "speaking") {
      return;
    }
    if (this.runtime.activeClips.length > 0) {
      return;
    }
    if (now - this.runtime.recentInteractionAt < 2.2) {
      return;
    }
    if (now < this.runtime.nextIdleAt) {
      return;
    }
    const choices = [];
    const allow = new Set(this.idlePolicy.allowedBehaviors || []);
    if (allow.has("blink")) {
      choices.push({ weight: 0.30, run: () => this.triggerBlink(0, 1, "Idle blink", { idle: true }) });
    }
    if (allow.has("quick_glance")) {
      choices.push({ weight: 0.05, run: () => this.triggerQuickGlance({ idle: true }) });
    }
    if (allow.has("bored") && now - this.runtime.recentExternalInteractionAt > 20) {
      choices.push({ weight: 0.12, run: () => this.triggerBoredHalfLid({ idle: true, holdSeconds: 5 + (this.random() * 3) }) });
    }
    if (allow.has("cute")) {
      choices.push({ weight: 0.08, run: () => this.triggerCuteMode({ idle: true, holdSeconds: 5 + (this.random() * 3) }) });
    }
    if (allow.has("thinking")) {
      choices.push({ weight: 0.08, run: () => this.triggerThinking({ idle: true, sideHoldSeconds: 2 + (this.random() * 2) }) });
    }
    if (allow.has("scoot")) {
      choices.push({ weight: 0.05, run: () => this.triggerScoot({ idle: true, holdSeconds: 1.4 + (this.random() * 1.0) }) });
    }
    if (!choices.length) {
      return;
    }
    pickWeighted(choices, this.random).run();
    const basePause = lerp(6.8, 2.4, state.timing.idleFrequency);
    const randomPause = lerp(0.8, 3.4, state.timing.pauseRandomness) * this.random();
    this.runtime.nextIdleAt = now + basePause + randomPause;
  }

  updateSpeakingBlinkScheduler(now, state) {
    if (this.externalState.scene !== "face" || this.externalState.lifecycle !== "speaking") {
      this.runtime.nextSpeakingBlinkAt = now + 0.8 + (this.random() * 1.4);
      return;
    }
    if (now < this.runtime.nextSpeakingBlinkAt) {
      return;
    }
    const closeChance = 0.72 + (state.timing.idleIntensity * 0.18);
    if (this.random() < closeChance) {
      this.triggerBlink(0, 0.88, "Speaking blink", { idle: true });
    }
    this.runtime.nextSpeakingBlinkAt = now + 2.4 + (this.random() * 2.8);
  }

  updateMicroMotion(now, dt, state) {
    if (!state.timing.secondaryMicroMotion) {
      this.runtime.micro.x *= 0.82;
      this.runtime.micro.y *= 0.82;
      this.runtime.micro.lookX *= 0.82;
      this.runtime.micro.lookY *= 0.82;
      return;
    }
    if (now < this.runtime.suppressMicroUntil) {
      this.runtime.micro.x = lerp(this.runtime.micro.x, 0, 0.26);
      this.runtime.micro.y = lerp(this.runtime.micro.y, 0, 0.26);
      this.runtime.micro.lookX = lerp(this.runtime.micro.lookX, 0, 0.26);
      this.runtime.micro.lookY = lerp(this.runtime.micro.lookY, 0, 0.26);
      this.runtime.micro.targetX = 0;
      this.runtime.micro.targetY = 0;
      this.runtime.micro.targetLookX = 0;
      this.runtime.micro.targetLookY = 0;
      this.runtime.micro.nextTargetAt = this.runtime.suppressMicroUntil + 0.24;
      return;
    }
    if (now >= this.runtime.micro.nextTargetAt) {
      const intensity = state.motionModifiers.idleEnabled ? state.timing.idleIntensity : 0.18;
      const randomness = lerp(0.35, 1, state.timing.randomnessAmount);
      this.runtime.micro.targetX = (this.random() - 0.5) * 0.015 * intensity * randomness;
      this.runtime.micro.targetY = (this.random() - 0.5) * 0.012 * intensity * randomness;
      this.runtime.micro.targetLookX = (this.random() - 0.5) * 0.064 * intensity * randomness;
      this.runtime.micro.targetLookY = (this.random() - 0.5) * 0.056 * intensity * randomness;
      this.runtime.micro.nextTargetAt = now + lerp(2.2, 0.9, state.timing.idleFrequency) + (this.random() * 0.6);
    }
    const chase = 1 - Math.exp(-dt * 3.4);
    this.runtime.micro.x = lerp(this.runtime.micro.x, this.runtime.micro.targetX, chase);
    this.runtime.micro.y = lerp(this.runtime.micro.y, this.runtime.micro.targetY, chase);
    this.runtime.micro.lookX = lerp(this.runtime.micro.lookX, this.runtime.micro.targetLookX, chase);
    this.runtime.micro.lookY = lerp(this.runtime.micro.lookY, this.runtime.micro.targetLookY, chase);
  }

  updateTrapMotion(now, dt, state) {
    const active = state.motionModifiers.trappedMode || now < this.runtime.trap.activeUntil;
    const bounce = state.timing.bounceIntensity;
    const pull = active ? 0.22 : 0.52;
    const damping = active ? 0.88 : 0.74;
    const minDim = Math.min(this.runtime.width, this.runtime.height);
    const paddingPx = clamp(
      Number.isFinite(state.baseVisual.outerBoxPaddingPx)
        ? state.baseVisual.outerBoxPaddingPx
        : minDim * clamp(state.baseVisual.outerBoxPadding, 0.02, 0.22),
      0,
      minDim * 0.45,
    );
    const paddingRatio = minDim > 0 ? paddingPx / minDim : 0;
    const boundsX = clamp((0.18 + ((0.32 - paddingRatio) * 0.58)) * state.timing.trapRoamAmount, 0.08, 0.34);
    const boundsY = clamp((0.12 + ((0.28 - paddingRatio) * 0.46)) * state.timing.trapRoamAmount, 0.06, 0.26);
    this.runtime.trap.x += this.runtime.trap.vx * dt;
    this.runtime.trap.y += this.runtime.trap.vy * dt;
    this.runtime.trap.vx += (-this.runtime.trap.x * pull) * dt;
    this.runtime.trap.vy += (-this.runtime.trap.y * pull) * dt;
    this.runtime.trap.vx *= Math.pow(damping, dt * 60);
    this.runtime.trap.vy *= Math.pow(damping, dt * 60);
    this.runtime.trap.scale += this.runtime.trap.scaleVelocity * dt;
    this.runtime.trap.scaleVelocity += (-this.runtime.trap.scale * 10.5) * dt;
    this.runtime.trap.scaleVelocity *= Math.pow(0.72, dt * 60);
    if (this.runtime.trap.x > boundsX) {
      this.runtime.trap.x = boundsX;
      this.runtime.trap.vx = -Math.abs(this.runtime.trap.vx) * (0.45 + (bounce * 0.5));
      this.runtime.trap.impact = 1;
      this.runtime.trap.impactAxis = "x";
      this.runtime.trap.lastImpactAt = now;
      this.runtime.trap.scaleVelocity += state.baseVisual.forwardBounceScale * 2.8;
    } else if (this.runtime.trap.x < -boundsX) {
      this.runtime.trap.x = -boundsX;
      this.runtime.trap.vx = Math.abs(this.runtime.trap.vx) * (0.45 + (bounce * 0.5));
      this.runtime.trap.impact = 1;
      this.runtime.trap.impactAxis = "x";
      this.runtime.trap.lastImpactAt = now;
      this.runtime.trap.scaleVelocity += state.baseVisual.forwardBounceScale * 2.8;
    }
    if (this.runtime.trap.y > boundsY) {
      this.runtime.trap.y = boundsY;
      this.runtime.trap.vy = -Math.abs(this.runtime.trap.vy) * (0.45 + (bounce * 0.5));
      this.runtime.trap.impact = 1;
      this.runtime.trap.impactAxis = "y";
      this.runtime.trap.lastImpactAt = now;
      this.runtime.trap.scaleVelocity += state.baseVisual.forwardBounceScale * 1.6;
    } else if (this.runtime.trap.y < -boundsY) {
      this.runtime.trap.y = -boundsY;
      this.runtime.trap.vy = Math.abs(this.runtime.trap.vy) * (0.45 + (bounce * 0.5));
      this.runtime.trap.impact = 1;
      this.runtime.trap.impactAxis = "y";
      this.runtime.trap.lastImpactAt = now;
      this.runtime.trap.scaleVelocity += state.baseVisual.forwardBounceScale * 1.6;
    }
    this.runtime.trap.impact = Math.max(0, this.runtime.trap.impact - (dt * 2.6));
    this.runtime.trap.scale = clamp(this.runtime.trap.scale, -0.06, 0.12);
    if (!active && Math.abs(this.runtime.trap.x) < 0.001 && Math.abs(this.runtime.trap.y) < 0.001) {
      this.runtime.trap.x = 0;
      this.runtime.trap.y = 0;
    }
  }

  collectBehaviorOverlay(now) {
    const overlay = {
      lookX: 0,
      lookY: 0,
      motionX: 0,
      motionY: 0,
      faceTilt: 0,
      lidAmount: 0,
      lidAngleLeft: 0,
      lidAngleRight: 0,
      squishAmount: 0,
      stretchAmount: 0,
      cuteMode: 0,
      boredIntensity: 0,
      curiousIntensity: 0,
      tearfulIntensity: 0,
      eyeOpenBoost: 0,
      blinkClosedness: 0,
      sleepLine: 0,
      mouthSmile: 0,
      mouthOpen: 0,
      mouthStill: 0,
      mouthWidthBias: 0,
      lidOverride: null,
      leftSizeBias: 0,
      rightSizeBias: 0,
      leftLidBias: 0,
      rightLidBias: 0,
    };
    this.runtime.activeClips = this.runtime.activeClips.filter((clip) => {
      if (now < clip.startAt) {
        return true;
      }
      const elapsed = now - clip.startAt;
      if (elapsed > clip.duration) {
        return false;
      }
      const contribution = clip.sample(elapsed, clip.duration) || {};
      for (const key in contribution) {
        const value = contribution[key];
        if (key === "blinkClosedness") {
          overlay[key] = Math.max(overlay[key], value);
        } else if (key === "lidOverride") {
          overlay[key] = value;
        } else {
          overlay[key] += value;
        }
      }
      return true;
    });
    const override = this.getActiveExpressionOverride(now);
    if (override?.name === "cute") {
      const direction = override.direction || 1;
      overlay.cuteMode += 0.86;
      overlay.eyeOpenBoost += 0.44;
      overlay.lookX += direction * 0.18;
      overlay.lookY += -0.12;
      overlay.lidAngleLeft += 0.34;
      overlay.lidAngleRight += -0.34;
      overlay.stretchAmount += 0.10;
      overlay.tearfulIntensity += 0.12;
      overlay.leftSizeBias += 0.08;
      overlay.rightSizeBias += 0.08;
      overlay.mouthSmile += 0.34;
      overlay.mouthOpen += 0.10;
    }
    if (this.externalState.scene === "sleep" || override?.name === "sleeping") {
      overlay.sleepLine = Math.max(overlay.sleepLine, 1);
      overlay.lidOverride = Math.max(overlay.lidOverride || 0, 1.08);
      overlay.boredIntensity += 0.35;
      overlay.mouthStill += 1;
    }
    return overlay;
  }

  buildTargetPose(now, state) {
    const behavior = this.collectBehaviorOverlay(now);
    const activeOverride = this.getActiveExpressionOverride(now);
    const forceSpeakingFocus = this.externalState.lifecycle === "speaking" && !activeOverride;
    const cute = clamp(state.expressionModifiers.cuteMode + behavior.cuteMode, 0, 1);
    const bored = forceSpeakingFocus
      ? 0
      : clamp(state.expressionModifiers.boredIntensity + behavior.boredIntensity, 0, 1);
    const curious = clamp(state.expressionModifiers.curiousIntensity + behavior.curiousIntensity, 0, 1);
    const tearful = clamp(state.expressionModifiers.tearfulIntensity + behavior.tearfulIntensity, 0, 1);
    const squish = clamp(state.expressionModifiers.squishAmount + behavior.squishAmount, 0, 1);
    const stretch = clamp(state.expressionModifiers.stretchAmount + behavior.stretchAmount, 0, 1);
    const resolvedLookX = clamp(
      state.expressionModifiers.lookX + behavior.lookX + this.runtime.micro.lookX + (this.runtime.trap.vx * 0.12),
      -1,
      1,
    );
    const resolvedLookY = clamp(
      state.expressionModifiers.lookY + behavior.lookY + this.runtime.micro.lookY + (this.runtime.trap.vy * 0.12),
      -1,
      1,
    );
    const lookX = forceSpeakingFocus ? 0 : resolvedLookX;
    const lookY = forceSpeakingFocus ? 0 : resolvedLookY;
    const hasActiveClip = this.runtime.activeClips.length > 0;
    const idleLidsHidden = !hasActiveClip && this.externalState.lifecycle !== "speaking";
    const lidBase = forceSpeakingFocus
      ? 0
      : idleLidsHidden
      ? 0
      : clamp(state.expressionModifiers.lidAmount + (bored * 0.40) - (behavior.eyeOpenBoost * 0.52), 0, 1);
    const blink = clamp(behavior.blinkClosedness, 0, 1);
    let lidLeft = clamp(lidBase + behavior.leftLidBias, 0, 1.35);
    let lidRight = clamp(lidBase + behavior.rightLidBias, 0, 1.35);
    if (typeof behavior.lidOverride === "number") {
      lidLeft = behavior.lidOverride;
      lidRight = behavior.lidOverride;
    }
    const stageScale = 1.18;
    const faceScale = state.baseVisual.faceScale * stageScale * (1 + (cute * 0.06) + this.runtime.trap.scale);
    const eyeBase = state.baseVisual.eyeSize * faceScale * (1 + (cute * 0.12) + (curious * 0.03));
    const perspective = state.baseVisual.perspectiveIntensity;
    const sizeLeft = eyeBase * (1 - (state.baseVisual.asymmetry * 0.05) + behavior.leftSizeBias - (Math.max(0, -lookX) * perspective));
    const sizeRight = eyeBase * (1 + (state.baseVisual.asymmetry * 0.05) + behavior.rightSizeBias - (Math.max(0, lookX) * perspective));
    const spacing = state.baseVisual.eyeSpacing * faceScale * (1 - (cute * 0.08));
    const movementScale = state.timing.movementAmount;
    const trapTravelX = this.runtime.trap.x * lerp(0.32, 1, state.timing.trapRoamAmount);
    const trapTravelY = this.runtime.trap.y * lerp(0.28, 1, state.timing.trapRoamAmount);
    let motionEnergy = clamp(
      Math.abs(this.runtime.trap.vx) * 0.65 +
      Math.abs(this.runtime.trap.vy) * 0.65 +
      Math.abs(behavior.motionX) * 1.1 +
      Math.abs(behavior.motionY) * 1.1,
      0,
      1,
    );
    if (this.externalState.lifecycle === "speaking") {
      motionEnergy = Math.max(motionEnergy, 0.24);
    }
    const impact = this.runtime.trap.impact * state.timing.bounceIntensity;
    let impactSquashX = 0;
    let impactSquashY = 0;
    if (this.runtime.trap.impactAxis === "x") {
      impactSquashX = impact * 0.20;
      impactSquashY = -impact * 0.16;
    } else {
      impactSquashX = -impact * 0.14;
      impactSquashY = impact * 0.18;
    }
    const sleepingOverride = this.getActiveExpressionOverride(now)?.name === "sleeping";
    const sleepBreath = (this.externalState.scene === "sleep" || sleepingOverride)
      ? Math.sin(now * Math.PI * 2 * 0.22) * 0.030
      : 0;
    return {
      faceScale,
      spacing,
      faceTilt: behavior.faceTilt,
      eyeY: state.baseVisual.eyeY,
      eyeXOffset: state.baseVisual.eyeXOffset,
      motionX: (
        behavior.motionX +
        this.runtime.micro.x +
        trapTravelX +
        (lookX * state.timing.lookTravelAmount * 0.28)
      ) * movementScale,
      motionY: (
        behavior.motionY +
        this.runtime.micro.y +
        trapTravelY +
        (lookY * state.timing.lookTravelAmount * 0.18) +
        sleepBreath
      ) * movementScale,
      lookX,
      lookY,
      lidLeft: clamp(lidLeft + blink, 0, 1.35),
      lidRight: clamp(lidRight + blink, 0, 1.35),
      lidAngleLeft: state.expressionModifiers.lidAngleLeft + behavior.lidAngleLeft - (bored * 0.06),
      lidAngleRight: state.expressionModifiers.lidAngleRight + behavior.lidAngleRight + (bored * 0.06),
      lidsEnabled: Boolean(state.expressionModifiers.lidsEnabled),
      lidLift: state.expressionModifiers.lidLift,
      lidInset: state.expressionModifiers.lidInset,
      lidSoftness: state.expressionModifiers.lidSoftness,
      cute,
      bored,
      curious,
      tearful,
      squish,
      stretch,
      mouthSmile: clamp(state.baseVisual.mouthCurveBias + behavior.mouthSmile, -1, 1),
      mouthOpen: clamp(state.baseVisual.mouthOpenBias + behavior.mouthOpen, 0, 1),
      mouthWidthBias: behavior.mouthWidthBias,
      mouthTalk: state.timing.mouthAnimationAmount,
      mouthStill: behavior.mouthStill > 0.25,
      motionEnergy,
      eyeSizeLeft: sizeLeft,
      eyeSizeRight: sizeRight,
      roundness: state.baseVisual.roundness,
      ringShiftIntensity: state.baseVisual.ringShiftIntensity,
      sleepLine: behavior.sleepLine,
      impactSquashX,
      impactSquashY,
      lifecycle: this.externalState.lifecycle,
    };
  }

  smoothPose(target, dt, state) {
    if (!this.runtime.renderPose) {
      this.runtime.renderPose = deepClone(target);
      return this.runtime.renderPose;
    }
    const speed = lerp(18, 5, state.timing.motionSmoothing);
    const chase = 1 - Math.exp(-dt * speed);
    Object.keys(target).forEach((key) => {
      this.runtime.renderPose[key] = typeof target[key] === "number"
        ? lerp(this.runtime.renderPose[key], target[key], chase)
        : target[key];
    });
    return this.runtime.renderPose;
  }

  getBoxRect(state) {
    const key = [
      this.runtime.width,
      this.runtime.height,
      state.baseVisual.outerBoxPaddingPx,
      state.baseVisual.outerBoxPadding,
      state.baseVisual.outerBoxWidth,
      state.baseVisual.outerBoxRadius,
    ].join("|");
    if (this.runtime.geometry.boxRect && this.runtime.geometry.boxRectKey === key) {
      return this.runtime.geometry.boxRect;
    }
    const minDim = Math.min(this.runtime.width, this.runtime.height);
    const outerPaddingPx = clamp(
      Number.isFinite(state.baseVisual.outerBoxPaddingPx)
        ? state.baseVisual.outerBoxPaddingPx
        : minDim * clamp(state.baseVisual.outerBoxPadding, 0.02, 0.22),
      0,
      minDim * 0.45,
    );
    const strokeInset = Math.max(0, state.baseVisual.outerBoxWidth * 0.5);
    const paddingPx = outerPaddingPx + strokeInset;
    const rect = {
      x: paddingPx,
      y: paddingPx,
      width: Math.max(0, this.runtime.width - (paddingPx * 2)),
      height: Math.max(0, this.runtime.height - (paddingPx * 2)),
      radius: state.baseVisual.outerBoxRadius,
    };
    this.runtime.geometry.boxRect = rect;
    this.runtime.geometry.boxRectKey = key;
    return rect;
  }

  _createCanvas(width, height) {
    if (typeof OffscreenCanvas === "function") {
      return new OffscreenCanvas(width, height);
    }
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    return canvas;
  }

  _getGlowSprite(rx, ry, color, strength) {
    const dpr = this.runtime.dpr;
    const quantizedRx = Math.max(4, Math.round(rx / 4) * 4);
    const quantizedRy = Math.max(4, Math.round(ry / 4) * 4);
    const quantizedStrength = Math.round(strength * 20) / 20;
    const key = `${quantizedRx}|${quantizedRy}|${color}|${quantizedStrength}|${dpr}`;
    const cached = this._glowSpriteCache.get(key);
    if (cached) {
      return cached;
    }
    const margin = quantizedRx * (2.65 + quantizedStrength);
    const cssWidth = (quantizedRx * 2) + (margin * 2);
    const cssHeight = (quantizedRy * 2) + (margin * 2);
    const canvas = this._createCanvas(Math.ceil(cssWidth * dpr), Math.ceil(cssHeight * dpr));
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.translate(margin + quantizedRx, margin + quantizedRy);
    ctx.scale(1, quantizedRy / Math.max(quantizedRx, 1));
    ctx.beginPath();
    ctx.arc(0, 0, quantizedRx * 1.55, 0, Math.PI * 2);
    ctx.clip();
    ctx.shadowBlur = quantizedRx * (0.60 + (quantizedStrength * 1.10));
    ctx.shadowColor = rgba(color, 0.30 * quantizedStrength);
    ctx.strokeStyle = rgba(color, 0.12 * quantizedStrength);
    ctx.lineWidth = quantizedRx * 0.92;
    ctx.beginPath();
    ctx.arc(0, 0, quantizedRx * 0.96, 0, Math.PI * 2);
    ctx.stroke();
    ctx.shadowBlur = quantizedRx * (1.10 + (quantizedStrength * 1.30));
    ctx.shadowColor = rgba(color, 0.14 * quantizedStrength);
    ctx.strokeStyle = rgba(color, 0.06 * quantizedStrength);
    ctx.lineWidth = quantizedRx * 1.40;
    ctx.beginPath();
    ctx.arc(0, 0, quantizedRx * 1.06, 0, Math.PI * 2);
    ctx.stroke();
    const sprite = { canvas, margin, rx: quantizedRx, ry: quantizedRy, cssWidth, cssHeight };
    this._glowSpriteCache.set(key, sprite);
    if (this._glowSpriteCache.size > this._maxGlowSpriteCacheEntries) {
      this._glowSpriteCache.delete(this._glowSpriteCache.keys().next().value);
    }
    return sprite;
  }

  drawGlow(x, y, rx, ry, color, strength) {
    const ctx = this.ctx;
    const sprite = this._getGlowSprite(rx, ry, color, strength);
    ctx.save();
    ctx.globalCompositeOperation = "screen";
    ctx.drawImage(
      sprite.canvas,
      x - sprite.rx - sprite.margin,
      y - sprite.ry - sprite.margin,
      sprite.cssWidth,
      sprite.cssHeight,
    );
    ctx.restore();
  }

  updateBorderLevel() {
    const border = this.runtime.border;
    border.currentLevel = lerp(border.currentLevel, border.targetLevel, 0.1);
    border.currentLevel = Math.max(border.targetLevel, border.currentLevel * 0.92);
    return border.currentLevel;
  }

  createBorderGradient(box, now, alpha = 1) {
    const ctx = this.ctx;
    const phase = (now % 9) / 9;
    const angle = phase * Math.PI * 2;
    const centerX = box.x + (box.width / 2);
    const centerY = box.y + (box.height / 2);
    const length = Math.hypot(box.width, box.height) * 0.58;
    const gradient = ctx.createLinearGradient(
      centerX + Math.cos(angle) * length,
      centerY + Math.sin(angle) * length,
      centerX - Math.cos(angle) * length,
      centerY - Math.sin(angle) * length,
    );
    gradient.addColorStop(0.00, rgba("#48f8ff", alpha));
    gradient.addColorStop(0.48, rgba("#287cff", alpha));
    gradient.addColorStop(1.00, rgba("#9b6dff", alpha * 0.82));
    return gradient;
  }

  drawOuterBox(box, state, now) {
    if (!state.baseVisual.outerBoxEnabled) {
      return;
    }
    const ctx = this.ctx;
    const width = state.baseVisual.outerBoxWidth;
    const smoothedLevel = this.updateBorderLevel();
    const wobble = Math.sin(now * 6) * 0.05;
    const finalLevel = clamp(smoothedLevel + wobble, 0, 1);
    const listening = this.externalState.lifecycle === "listening";
    const turnActive = this.externalState.scene === "face" && this.externalState.lifecycle !== "idle";
    const recentMicLevel = now - this.runtime.border.lastLevelAt < 0.45;
    const micVoiceActive = recentMicLevel && Math.max(this.runtime.border.targetLevel, smoothedLevel) > 0.10;
    const reactive = turnActive && (listening || micVoiceActive);
    const breath = (Math.sin(now * 1.5) * 0.5) + 0.5;
    const visualLevel = reactive ? finalLevel : 0.08 + (breath * 0.07);
    const activeBase = listening ? 0.34 : 0.24;
    const brightness = reactive ? clamp(activeBase + (visualLevel * 0.66), 0, 1) : visualLevel;
    const shimmer = 1 + (Math.sin(now * 2) * 0.035);
    const sharpWidth = clamp(width, 4.5, 6.0) + (reactive ? visualLevel * 1.35 : breath * 0.32);
    const glowWidth = sharpWidth + lerp(7, 10, brightness);
    const glowBlur = lerp(6, 12, brightness);
    const gradient = this.createBorderGradient(box, now, 1);
    const highlightGradient = this.createBorderGradient(box, now + 2.8, 1);
    ctx.save();
    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    ctx.globalCompositeOperation = "screen";
    ctx.strokeStyle = gradient;
    ctx.lineWidth = glowWidth;
    ctx.globalAlpha = clamp((0.24 + (brightness * 0.38)) * shimmer, 0, 0.72);
    ctx.shadowBlur = glowBlur;
    ctx.shadowColor = rgba("#48f8ff", 0.18 + (brightness * 0.24));
    roundedRectPath(ctx, box.x, box.y, box.width, box.height, box.radius);
    ctx.stroke();

    ctx.lineWidth = sharpWidth + 3.5;
    ctx.globalAlpha = clamp((0.12 + (brightness * 0.22)) * shimmer, 0, 0.42);
    ctx.shadowBlur = Math.max(0, glowBlur * 0.45);
    roundedRectPath(ctx, box.x, box.y, box.width, box.height, box.radius);
    ctx.stroke();

    ctx.shadowBlur = 0;
    ctx.globalCompositeOperation = "source-over";
    ctx.strokeStyle = gradient;
    ctx.lineWidth = sharpWidth;
    ctx.globalAlpha = clamp((reactive ? 0.74 : 0.56) + (brightness * 0.16), 0, 0.95);
    roundedRectPath(ctx, box.x, box.y, box.width, box.height, box.radius);
    ctx.stroke();

    ctx.globalCompositeOperation = "screen";
    ctx.strokeStyle = highlightGradient;
    ctx.lineWidth = Math.max(2.2, sharpWidth * 0.42);
    ctx.globalAlpha = clamp((0.14 + (brightness * 0.16)) * shimmer, 0, 0.32);
    roundedRectPath(ctx, box.x, box.y, box.width, box.height, box.radius);
    ctx.stroke();
    ctx.restore();
  }

  drawEyeReflection(eye, clipRx, clipRy, mood, state) {
    const reflectionActive = state.baseVisual.eyeReflectionEnabled || mood.tearful > 0.06;
    if (!reflectionActive) {
      return;
    }
    const ctx = this.ctx;
    const cuteFrontBias = clamp(mood.cute * 1.2, 0, 1);
    const alpha = clamp(state.baseVisual.eyeReflectionOpacity + (mood.tearful * 0.22), 0, 1);
    const size = Math.max(2, Math.min(clipRx, clipRy) * state.baseVisual.eyeReflectionSize * (1 + (mood.tearful * 0.4)));
    const facingOffsetX = clipRx * -0.06;
    const facingOffsetY = clipRy * -0.22;
    const trackedOffsetX = (state.baseVisual.eyeReflectionOffsetX * clipRx * 0.6) - (eye.lookX * clipRx * 0.16);
    const trackedOffsetY = (state.baseVisual.eyeReflectionOffsetY * clipRy * 0.6) - (eye.lookY * clipRy * 0.10);
    const offsetX = lerp(trackedOffsetX, facingOffsetX, cuteFrontBias);
    const offsetY = lerp(trackedOffsetY, facingOffsetY, cuteFrontBias);
    ctx.fillStyle = rgba("#ffffff", alpha);
    ctx.beginPath();
    ctx.arc(eye.x + offsetX, eye.y + offsetY, size, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = rgba("#ffffff", alpha * 0.55);
    ctx.beginPath();
    ctx.arc(eye.x + offsetX + (size * 0.9), eye.y + offsetY + (size * 0.75), Math.max(1, size * 0.42), 0, Math.PI * 2);
    ctx.fill();
  }

  drawLidOverlay(eye, clipRx, clipRy, mood, state) {
    const shouldShowLids = eye.cover > 0.01 && (mood.lidsEnabled || eye.cover > 0.92);
    if (!shouldShowLids) {
      return;
    }
    const ctx = this.ctx;
    const lidColor = state.baseVisual.lidColor || state.baseVisual.eyeColor;
    const inset = clamp(mood.lidInset, 0, 1);
    const softness = clamp(mood.lidSoftness, 0, 1);
    const sideInset = clipRx * (0.03 + (inset * 0.18));
    const topBase = eye.y - clipRy + (eye.cover * clipRy * 2.04) + (mood.lidLift * clipRy * 0.10);
    const angleShift = eye.lidAngle * clipRy * 0.18;
    const leftX = eye.x - clipRx + sideInset;
    const rightX = eye.x + clipRx - sideInset;
    const crest = clipRy * (0.06 + (softness * 0.10));
    ctx.save();
    ellipsePath(ctx, eye.x, eye.y, clipRx, clipRy);
    ctx.clip();
    if (softness > 0.01 && eye.cover < 0.97) {
      ctx.strokeStyle = rgba(lidColor, 0.08 + (softness * 0.08));
      ctx.lineWidth = clipRy * (0.26 + (softness * 0.16));
      ctx.beginPath();
      ctx.moveTo(leftX, topBase + angleShift);
      ctx.quadraticCurveTo(eye.x, topBase - crest, rightX, topBase - angleShift);
      ctx.stroke();
    }
    ctx.beginPath();
    ctx.moveTo(eye.x - clipRx * 1.6, eye.y - clipRy * 1.6);
    ctx.lineTo(eye.x + clipRx * 1.6, eye.y - clipRy * 1.6);
    ctx.lineTo(eye.x + clipRx * 1.6, topBase - angleShift);
    ctx.quadraticCurveTo(eye.x, topBase - crest, eye.x - clipRx * 1.6, topBase + angleShift);
    ctx.closePath();
    if (state.baseVisual.lidGradientEnabled) {
      const gradient = ctx.createLinearGradient(eye.x, eye.y - clipRy, eye.x, eye.y + clipRy);
      gradient.addColorStop(0, rgba(lidColor, 0.98));
      gradient.addColorStop(1, rgba(lidColor, 0.68 + (state.baseVisual.gradientStrength * 0.24)));
      ctx.fillStyle = gradient;
    } else {
      ctx.fillStyle = lidColor;
    }
    ctx.fill();
    ctx.restore();
    if (eye.cover > 0.94) {
      ctx.strokeStyle = lidColor;
      ctx.lineWidth = Math.max(3, state.baseVisual.outlineThickness * 0.40);
      ctx.lineCap = "round";
      ctx.beginPath();
      ctx.moveTo(eye.x - clipRx * 0.88, eye.y);
      ctx.lineTo(eye.x + clipRx * 0.88, eye.y);
      ctx.stroke();
    }
  }

  drawEye(eye, state) {
    const ctx = this.ctx;
    const color = state.baseVisual.eyeColor;
    const background = state.baseVisual.backgroundColor;
    const thickness = state.baseVisual.outlineThickness;
    const fillMode = state.baseVisual.fillMode;
    if (state.baseVisual.glowEnabled) {
      this.drawGlow(eye.x, eye.y, eye.rx, eye.ry, color, state.baseVisual.glowIntensity);
    }
    if (fillMode === "filled") {
      if (state.baseVisual.eyeGradientEnabled) {
        const gradient = ctx.createLinearGradient(eye.x - eye.rx, eye.y - eye.ry, eye.x + eye.rx, eye.y + eye.ry);
        gradient.addColorStop(0, rgba(color, 0.95));
        gradient.addColorStop(1, rgba(color, 0.70 + (state.baseVisual.gradientStrength * 0.20)));
        ctx.fillStyle = gradient;
      } else {
        ctx.fillStyle = color;
      }
      ellipsePath(ctx, eye.x, eye.y, eye.rx, eye.ry);
      ctx.fill();
    } else {
      ctx.fillStyle = color;
      ellipsePath(ctx, eye.x, eye.y, eye.rx, eye.ry);
      ctx.fill();
    }
    const ringInset = fillMode === "outlined"
      ? Math.max(4, thickness)
      : Math.max(4, thickness * 0.42);
    const clipRx = Math.max(4, eye.rx - ringInset);
    const clipRy = Math.max(4, eye.ry - ringInset);
    const shift = state.baseVisual.ringShiftIntensity;
    const maxOffsetX = Math.max(0, clipRx * 0.34);
    const maxOffsetY = Math.max(0, clipRy * 0.26);
    const offsetX = eye.lookX * maxOffsetX * shift;
    const offsetY = eye.lookY * maxOffsetY * shift;
    ctx.save();
    ellipsePath(ctx, eye.x, eye.y, clipRx, clipRy);
    ctx.clip();
    ctx.fillStyle = background;
    ellipsePath(ctx, eye.x + offsetX, eye.y + offsetY, clipRx, clipRy);
    ctx.fill();
    this.drawEyeReflection(eye, clipRx, clipRy, eye.mood, state);
    ctx.restore();
    if (fillMode === "outlined") {
      ctx.strokeStyle = state.baseVisual.eyeGradientEnabled
        ? (() => {
          const gradient = ctx.createLinearGradient(eye.x - eye.rx, eye.y - eye.ry, eye.x + eye.rx, eye.y + eye.ry);
          gradient.addColorStop(0, rgba(color, 0.92));
          gradient.addColorStop(1, rgba(color, 0.72 + (state.baseVisual.gradientStrength * 0.22)));
          return gradient;
        })()
        : color;
      ctx.lineWidth = Math.max(2, thickness);
      ellipsePath(ctx, eye.x, eye.y, eye.rx - (ctx.lineWidth / 2), eye.ry - (ctx.lineWidth / 2));
      ctx.stroke();
    }
    const lidInset = fillMode === "outlined" ? Math.max(4, thickness) : 3;
    this.drawLidOverlay(
      eye,
      Math.max(4, eye.rx - lidInset),
      Math.max(4, eye.ry - lidInset),
      eye.mood,
      state,
    );
    if (eye.mood.sleepLine > 0.82) {
      ctx.strokeStyle = state.baseVisual.backgroundColor;
      ctx.lineWidth = Math.max(3, thickness * 0.34);
      ctx.lineCap = "round";
      ctx.beginPath();
      ctx.moveTo(eye.x - clipRx * 0.88, eye.y);
      ctx.lineTo(eye.x + clipRx * 0.88, eye.y);
      ctx.stroke();
    }
  }

  drawSleepingMouth(centerX, eyeCenterY, mood, now, state) {
    const ctx = this.ctx;
    const color = state.baseVisual.eyeColor;
    const faceScale = mood.faceScale;
    const width = this.runtime.width * state.baseVisual.mouthWidth * 0.58 * faceScale;
    const y = eyeCenterY + (this.runtime.height * state.baseVisual.mouthY * (0.8 + (faceScale * 0.2)));
    const thickness = Math.max(3, state.baseVisual.mouthThickness * 0.72 * faceScale);
    const breathPeriod = 7.5;
    const phase = (now % breathPeriod) / breathPeriod;
    const ease = easeInOutCubic(phase < 0.5 ? phase * 2 : (1 - phase) * 2);
    const wavePhase = now * 0.72;
    const amplitude = (2.6 + (ease * 0.9)) * faceScale;
    const waveWidth = width * 1.52;
    ctx.save();
    ctx.strokeStyle = color;
    ctx.lineWidth = thickness;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    if (state.baseVisual.glowEnabled) {
      ctx.strokeStyle = rgba(color, state.baseVisual.glowIntensity * 0.18);
      ctx.lineWidth = thickness + 10;
      ctx.beginPath();
      for (let step = 0; step <= 28; step += 1) {
        const t = step / 28;
        const x = centerX - (waveWidth / 2) + (t * waveWidth);
        const yOffset = Math.sin((t * Math.PI * 2) + wavePhase) * amplitude * (0.36 + (ease * 0.10));
        if (step === 0) {
          ctx.moveTo(x, y + yOffset);
        } else {
          ctx.lineTo(x, y + yOffset);
        }
      }
      ctx.stroke();
    }
    ctx.strokeStyle = color;
    ctx.lineWidth = thickness;
    ctx.beginPath();
    for (let step = 0; step <= 28; step += 1) {
      const t = step / 28;
      const x = centerX - (waveWidth / 2) + (t * waveWidth);
      const yOffset = Math.sin((t * Math.PI * 2) + wavePhase) * amplitude * (0.36 + (ease * 0.10));
      if (step === 0) {
        ctx.moveTo(x, y + yOffset);
      } else {
        ctx.lineTo(x, y + yOffset);
      }
    }
    ctx.stroke();
    ctx.restore();
  }

  drawMouth(centerX, eyeCenterY, mood, now, state) {
    if (!state.baseVisual.mouthEnabled || state.baseVisual.mouthStyle === "none") {
      return;
    }
    if (this.externalState.scene === "sleep" || mood.sleepLine > 0.72) {
      this.drawSleepingMouth(centerX, eyeCenterY, mood, now, state);
      return;
    }
    const ctx = this.ctx;
    const color = state.baseVisual.eyeColor;
    const faceScale = mood.faceScale;
    const width = this.runtime.width * state.baseVisual.mouthWidth * 0.5 * faceScale;
    const y = eyeCenterY + (this.runtime.height * state.baseVisual.mouthY * (0.8 + (faceScale * 0.2)));
    const thickness = state.baseVisual.mouthThickness * faceScale;
    const smileBias = clamp(
      state.baseVisual.mouthCurveBias +
      (mood.cute * 0.62) -
      (mood.bored * 0.72) +
      (mood.curious * 0.10) +
      mood.mouthSmile,
      -1,
      1,
    );
    const expressiveEnergy = clamp(
      ((this.runtime.activeClips.length || this.externalState.lifecycle === "speaking") ? 0.36 : 0.10) + (mood.motionEnergy * 0.84),
      0,
      1,
    );
    const talkingEnergy = clamp(state.timing.mouthAnimationAmount * expressiveEnergy, 0, 1);
    const slowPhase = now * lerp(1.3, 2.4, state.timing.idleIntensity);
    const speakingSpeed = this.externalState.lifecycle === "speaking" ? 1.9 : 1;
    const fastPhase = now * lerp(4.2, 7.5, talkingEnergy) * speakingSpeed;
    const slowWave = (Math.sin(slowPhase) * 0.5) + 0.5;
    const fastWave = (Math.sin(fastPhase) * 0.5) + 0.5;
    const waveMix = Math.max(slowWave * 0.18, fastWave * talkingEnergy * 0.62);
    const speakingWobble = this.externalState.lifecycle === "speaking"
      ? Math.sin(now * 10.8) * 0.5
      : 0;
    const wobbleOpen = Math.abs(speakingWobble) * talkingEnergy * 0.08;
    const openAmount = clamp(state.baseVisual.mouthOpenBias + mood.mouthOpen + waveMix + wobbleOpen, 0, 1);
    const widthScale = clamp(1 - (mood.bored * 0.22) + (mood.cute * 0.08) + (mood.curious * 0.06) + (openAmount * 0.18) + (mood.mouthWidthBias || 0) + (speakingWobble * talkingEnergy * 0.09), 0.55, 1.45);
    const mouthWidth = width * widthScale;
    const animateMouth = (this.runtime.activeClips.length > 0 || this.externalState.lifecycle === "speaking") && !mood.mouthStill;
    ctx.strokeStyle = color;
    ctx.fillStyle = color;
    ctx.lineWidth = thickness;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    if (state.baseVisual.glowEnabled) {
      ctx.strokeStyle = rgba(color, state.baseVisual.glowIntensity * 0.16);
      ctx.lineWidth = thickness + 12;
      ctx.beginPath();
      ctx.moveTo(centerX - mouthWidth, y);
      ctx.lineTo(centerX + mouthWidth, y);
      ctx.stroke();
      ctx.strokeStyle = color;
      ctx.lineWidth = thickness;
    }
    if (state.baseVisual.mouthMotionStyle === "sine-wave" && animateMouth) {
      const waveWidth = mouthWidth * 1.65;
      const amplitude = Math.max(2, (3 + (slowWave * 3) + (talkingEnergy * 8) + (openAmount * 8 * faceScale)));
      ctx.beginPath();
      for (let step = 0; step <= 24; step += 1) {
        const t = step / 24;
        const x = centerX - (waveWidth / 2) + (t * waveWidth);
        const activePhase = talkingEnergy > 0.14 ? fastPhase : slowPhase;
        const smileArc = smileBias > 0
          ? (1 - Math.pow((t * 2) - 1, 2)) * smileBias * 5.0 * faceScale
          : 0;
        const yOffset = (Math.sin((t * Math.PI * 2) + activePhase) * amplitude * 0.42) - smileArc;
        if (step === 0) {
          ctx.moveTo(x, y + yOffset);
        } else {
          ctx.lineTo(x, y + yOffset);
        }
      }
      ctx.stroke();
      return;
    }
    if (state.baseVisual.mouthStyle === "flat-line") {
      const allowFlatOpenBox = this.externalState.lifecycle !== "speaking";
      if (allowFlatOpenBox && openAmount > 0.12) {
        const height = Math.max(thickness * 0.9, 10 + (openAmount * 24 * faceScale));
        roundedRectPath(ctx, centerX - mouthWidth, y - (height / 2), mouthWidth * 2, height, height / 2);
        ctx.stroke();
        return;
      }
      ctx.beginPath();
      ctx.moveTo(centerX - mouthWidth, y);
      ctx.lineTo(centerX + mouthWidth, y);
      ctx.stroke();
      return;
    }
    if (state.baseVisual.mouthStyle === "tiny-dash") {
      const dashWidth = Math.max(8, mouthWidth * 0.58);
      if (animateMouth && openAmount > 0.10) {
        const height = Math.max(thickness * 0.8, 8 + (openAmount * 20 * faceScale));
        roundedRectPath(ctx, centerX - dashWidth, y - (height / 2), dashWidth * 2, height, height / 2);
        ctx.stroke();
        return;
      }
      ctx.beginPath();
      ctx.moveTo(centerX - dashWidth, y);
      ctx.lineTo(centerX + dashWidth, y);
      ctx.stroke();
      return;
    }
    const radius = Math.max(12, mouthWidth * 0.58);
    const arcHeight = Math.max(8, 20 + (Math.abs(smileBias) * 14) + (openAmount * 18));
    ctx.beginPath();
    if (smileBias >= 0) {
      ctx.arc(centerX, y - arcHeight * 0.12, radius, 0.18 * Math.PI, 0.82 * Math.PI, false);
    } else {
      ctx.arc(centerX, y + arcHeight * 0.48, radius, 1.18 * Math.PI, 1.82 * Math.PI, false);
    }
    ctx.stroke();
  }

  drawSparkle(anchorX, anchorY, mood, now, state) {
    const sparkleActive = state.baseVisual.sparkleEnabled || mood.tearful > 0.05;
    if (!sparkleActive) {
      return;
    }
    const ctx = this.ctx;
    const color = state.baseVisual.eyeColor;
    const size = this.runtime.width * state.baseVisual.sparkleSize * (1 + (mood.tearful * 0.25));
    const x = anchorX + (this.runtime.width * state.baseVisual.sparkleOffsetX);
    const y = anchorY + (this.runtime.height * state.baseVisual.sparkleOffsetY);
    const twinkle = 0.55 + (Math.sin(now * 7.2) * 0.18) + (mood.tearful * 0.18);
    ctx.save();
    ctx.translate(x, y);
    ctx.rotate(Math.sin(now * 1.6) * 0.12);
    ctx.strokeStyle = rgba(color, clamp(twinkle, 0.18, 1));
    ctx.lineWidth = 3;
    if (state.baseVisual.glowEnabled) {
      ctx.strokeStyle = rgba(color, 0.24 * (1 + mood.tearful));
      ctx.lineWidth = 10;
      ctx.beginPath();
      ctx.moveTo(-size, 0);
      ctx.lineTo(size, 0);
      ctx.moveTo(0, -size);
      ctx.lineTo(0, size);
      ctx.stroke();
      ctx.strokeStyle = rgba(color, clamp(twinkle, 0.18, 1));
      ctx.lineWidth = 3;
    }
    ctx.beginPath();
    ctx.moveTo(-size, 0);
    ctx.lineTo(size, 0);
    ctx.moveTo(0, -size);
    ctx.lineTo(0, size);
    ctx.stroke();
    ctx.fillStyle = rgba(color, 0.88);
    ctx.beginPath();
    ctx.arc(size * 0.82, size * 0.55, Math.max(2, size * 0.18), 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
  }

  drawSleepBackgroundSparkles(now, state) {
    const sleepActive = this.externalState.scene === "sleep" || this.getActiveExpressionOverride(now)?.name === "sleeping";
    if (!sleepActive) {
      this.runtime.sleepSparkles = [];
      this.runtime.nextSleepSparkleAt = now + 1.0;
      return;
    }
    if (now >= this.runtime.nextSleepSparkleAt && this.runtime.sleepSparkles.length < 3) {
      const marginX = this.runtime.width * 0.14;
      const marginY = this.runtime.height * 0.14;
      const burstCount = this.random() > 0.70 ? 2 : 1;
      for (let index = 0; index < burstCount && this.runtime.sleepSparkles.length < 3; index += 1) {
        this.runtime.sleepSparkles.push({
          x: marginX + (this.random() * Math.max(1, this.runtime.width - (marginX * 2))),
          y: marginY + (this.random() * Math.max(1, this.runtime.height - (marginY * 2))),
          startAt: now + (index * 0.12),
          duration: 1.05 + (this.random() * 0.60),
          size: this.runtime.width * (0.011 + (this.random() * 0.012)),
          spin: (this.random() > 0.5 ? 1 : -1) * (0.9 + (this.random() * 0.8)),
        });
      }
      this.runtime.nextSleepSparkleAt = now + 1.4 + (this.random() * 2.4);
    }
    const ctx = this.ctx;
    const color = state.baseVisual.eyeColor;
    this.runtime.sleepSparkles = this.runtime.sleepSparkles.filter((sparkle) => {
      const age = now - sparkle.startAt;
      if (age < 0 || age > sparkle.duration) {
        return false;
      }
      const t = clamp(age / sparkle.duration, 0, 1);
      const alpha = Math.sin(t * Math.PI);
      const size = sparkle.size * (0.55 + (easeOutCubic(t) * 0.72));
      ctx.save();
      ctx.translate(sparkle.x, sparkle.y);
      ctx.rotate((now - sparkle.startAt) * sparkle.spin * Math.PI);
      ctx.globalCompositeOperation = "screen";
      ctx.strokeStyle = rgba(color, 0.16 * alpha);
      ctx.lineWidth = Math.max(4, size * 0.42);
      ctx.beginPath();
      ctx.moveTo(-size * 1.5, 0);
      ctx.lineTo(size * 1.5, 0);
      ctx.moveTo(0, -size * 1.5);
      ctx.lineTo(0, size * 1.5);
      ctx.stroke();
      ctx.strokeStyle = rgba(color, 0.56 * alpha);
      ctx.lineWidth = Math.max(2, size * 0.16);
      ctx.beginPath();
      ctx.moveTo(-size, 0);
      ctx.lineTo(size, 0);
      ctx.moveTo(0, -size);
      ctx.lineTo(0, size);
      ctx.stroke();
      ctx.restore();
      return true;
    });
  }

  drawSleepAccents(centerX, centerY, pose, now, state) {
    if (pose.sleepLine < 0.72) {
      return;
    }
    const ctx = this.ctx;
    const color = state.baseVisual.eyeColor;
    const sleepAlpha = clamp((pose.sleepLine - 0.72) / 0.28, 0, 1);
    const zBaseX = centerX + (this.runtime.width * 0.10);
    const zBaseY = centerY - (this.runtime.height * 0.06);
    ctx.save();
    const specs = [
      { glyph: "z", size: 0.030, x: 0, offset: 0.00 },
      { glyph: "z", size: 0.038, x: 18, offset: 0.34 },
      { glyph: "Z", size: 0.050, x: 40, offset: 0.68 },
    ];
    for (const spec of specs) {
      const phase = (now * 0.18 + spec.offset) % 1;
      const alpha = Math.sin(phase * Math.PI) * sleepAlpha;
      const y = zBaseY - (phase * this.runtime.height * 0.19);
      ctx.globalAlpha = clamp(alpha * 0.92, 0, 0.92);
      ctx.fillStyle = rgba(color, 0.78);
      ctx.font = `${Math.round(this.runtime.width * spec.size)}px ui-rounded, "Trebuchet MS", sans-serif`;
      ctx.fillText(spec.glyph, zBaseX + spec.x, y);
    }
    ctx.globalAlpha = 1;
    ctx.restore();
  }

  render(pose, now, state) {
    const ctx = this.ctx;
    ctx.clearRect(0, 0, this.runtime.width, this.runtime.height);
    ctx.fillStyle = state.baseVisual.backgroundColor;
    ctx.fillRect(0, 0, this.runtime.width, this.runtime.height);
    this.drawSleepBackgroundSparkles(now, state);
    const box = this.getBoxRect(state);
    this.drawOuterBox(box, state, now);
    const minDim = Math.min(this.runtime.width, this.runtime.height);
    let centerX = (this.runtime.width / 2) + (state.baseVisual.eyeXOffset * minDim * 0.35) + (pose.motionX * minDim * 0.24);
    let centerY = (this.runtime.height * 0.47) + (pose.eyeY * this.runtime.height) + (pose.motionY * minDim * 0.20);
    const baseRadiusLeft = minDim * pose.eyeSizeLeft;
    const baseRadiusRight = minDim * pose.eyeSizeRight;
    const leftRx = baseRadiusLeft * (1 + (pose.squish * 0.22) - (pose.stretch * 0.08) - pose.impactSquashX);
    const leftRy = (baseRadiusLeft * pose.roundness) * (1 - (pose.squish * 0.26) + (pose.stretch * 0.20) - pose.impactSquashY);
    const rightRx = baseRadiusRight * (1 + (pose.squish * 0.22) - (pose.stretch * 0.08) - pose.impactSquashX);
    const rightRy = (baseRadiusRight * pose.roundness) * (1 - (pose.squish * 0.26) + (pose.stretch * 0.20) - pose.impactSquashY);
    const spacingPx = minDim * pose.spacing;
    const lookOffsetXLeft = pose.lookX * leftRx * 0.52;
    const lookOffsetXRight = pose.lookX * rightRx * 0.52;
    const lookOffsetYLeft = pose.lookY * leftRy * 0.48;
    const lookOffsetYRight = pose.lookY * rightRy * 0.48;
    const eyeExtentX = Math.max(leftRx, rightRx) + (spacingPx / 2) + 18;
    const mouthGuard = state.baseVisual.mouthEnabled
      ? (this.runtime.height * state.baseVisual.mouthY * pose.faceScale) + 26
      : 18;
    const topGuard = Math.max(leftRy, rightRy) + 20;
    centerX = clamp(centerX, box.x + eyeExtentX, box.x + box.width - eyeExtentX);
    centerY = clamp(centerY, box.y + topGuard, box.y + box.height - mouthGuard);
    const leftEye = {
      x: centerX - (spacingPx / 2) + lookOffsetXLeft,
      y: centerY + lookOffsetYLeft,
      rx: Math.max(10, leftRx),
      ry: Math.max(10, leftRy),
      cover: clamp(pose.lidLeft, 0, 1.25),
      lidAngle: pose.lidAngleLeft,
      lookX: pose.lookX,
      lookY: pose.lookY,
      mood: pose,
    };
    const rightEye = {
      x: centerX + (spacingPx / 2) + lookOffsetXRight,
      y: centerY + lookOffsetYRight,
      rx: Math.max(10, rightRx),
      ry: Math.max(10, rightRy),
      cover: clamp(pose.lidRight, 0, 1.25),
      lidAngle: pose.lidAngleRight,
      lookX: pose.lookX,
      lookY: pose.lookY,
      mood: pose,
    };
    ctx.save();
    if (Math.abs(pose.faceTilt) > 0.0001) {
      ctx.translate(centerX, centerY);
      ctx.rotate(pose.faceTilt);
      ctx.translate(-centerX, -centerY);
    }
    this.drawEye(leftEye, state);
    this.drawEye(rightEye, state);
    this.drawMouth(centerX, centerY, pose, now, state);
    this.drawSparkle(rightEye.x, rightEye.y, pose, now, state);
    this.drawSleepAccents(centerX, centerY, pose, now, state);
    ctx.restore();
  }

  _tick(nowMs) {
    const now = nowMs / 1000;
    const dt = clamp(now - this.runtime.lastTime, 1 / 240, 0.05);
    this.runtime.lastTime = now;
    const state = this._composedState();
    this.updateIdleScheduler(now, state);
    this.updateSpeakingBlinkScheduler(now, state);
    this.updateMicroMotion(now, dt, state);
    this.updateTrapMotion(now, dt, state);
    const targetPose = this.buildTargetPose(now, state);
    const pose = this.smoothPose(targetPose, dt, state);
    this.render(pose, now, state);
    this._rafId = window.requestAnimationFrame(this._tick);
  }
}
