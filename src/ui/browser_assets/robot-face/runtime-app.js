import { RobotFaceEngine } from "./engine.js";

const DOM = {
  canvas: document.getElementById("faceCanvas"),
  status: document.getElementById("bridgeStatus"),
  animationStatus: document.getElementById("animationStatus"),
  textOverlay: document.getElementById("textOverlay"),
  contentOverlay: document.getElementById("contentOverlay"),
  contentTitle: document.getElementById("contentTitle"),
  contentBody: document.getElementById("contentBody"),
  iconStrip: document.getElementById("iconStrip"),
  root: document.documentElement,
};

const engine = new RobotFaceEngine({ canvas: DOM.canvas });
const overlayState = {
  text: "",
  contentMode: "face",
  contentPayload: {},
  previewText: "",
  blanked: false,
};

function statusLabel(lifecycle) {
  switch (String(lifecycle || "idle").toLowerCase()) {
    case "listening":
      return "Listening";
    case "speaking":
      return "Speaking";
    case "idle":
    default:
      return "Idle";
  }
}

function connect() {
  const wsParam = new URLSearchParams(window.location.search).get("ws");
  const wsPort = Number.parseInt(wsParam || "", 10);
  const port = Number.isFinite(wsPort) ? wsPort : (Number.parseInt(window.location.port || "80", 10) + 1);
  const socket = new WebSocket(`ws://${window.location.hostname}:${port}`);
  socket.addEventListener("open", () => {
    socket.send(JSON.stringify({ type: "hello", payload: { userAgent: navigator.userAgent } }));
  });
  socket.addEventListener("message", (event) => {
    try {
      const message = JSON.parse(event.data);
      handleMessage(message);
    } catch (error) {
      console.warn("Could not parse browser bridge message", error);
    }
  });
  socket.addEventListener("close", () => {
    DOM.status.textContent = "Reconnecting";
    window.setTimeout(connect, 1500);
  });
  socket.addEventListener("error", () => {
    socket.close();
  });
}

function handleMessage(message) {
  const payload = message && typeof message === "object" ? (message.payload || {}) : {};
  switch (message.type) {
    case "renderer_config":
      engine.setRendererConfig(payload);
      return;
    case "renderer_state":
      engine.setExternalState({
        scene: payload.scene || "face",
        lifecycle: payload.lifecycle || "idle",
        emotion: payload.emotion || "neutral",
        previewText: payload.previewText || "",
      });
      DOM.status.textContent = statusLabel(payload.lifecycle);
      overlayState.previewText = String(payload.previewText || "");
      overlayState.blanked = Boolean(payload.displaySleepRequested);
      renderOverlays();
      return;
    case "mic_level":
      engine.onMicLevel(payload.level);
      return;
    case "transient_trigger":
      triggerBehavior(payload);
      renderAnimationStatus();
      return;
    case "expression_override":
      engine.setExpressionOverride(payload);
      renderAnimationStatus();
      return;
    case "overlay_update":
      overlayState.text = String(payload.text || "");
      overlayState.contentMode = String(payload.contentMode || "face");
      overlayState.contentPayload = payload.contentPayload || {};
      renderOverlays();
      return;
    default:
      return;
  }
}

function triggerBehavior(payload) {
  const delayMs = Math.max(0, Number(payload.delaySeconds || 0) * 1000);
  if (delayMs > 0) {
    window.setTimeout(() => {
      engine.triggerNamedBehavior(payload.name, payload);
      renderAnimationStatus();
    }, delayMs);
    return;
  }
  engine.triggerNamedBehavior(payload.name, payload);
}

function renderOverlays() {
  document.body.classList.toggle("display-blanked", overlayState.blanked);
  renderAnimationStatus();
  const text = overlayState.text || overlayState.previewText || "";
  DOM.textOverlay.textContent = text;
  DOM.textOverlay.hidden = !text || overlayState.blanked;

  const payload = overlayState.contentPayload || {};
  const hasContent = overlayState.contentMode !== "face";
  DOM.contentOverlay.hidden = !hasContent || overlayState.blanked;
  if (!hasContent) {
    DOM.iconStrip.innerHTML = "";
    DOM.contentTitle.textContent = "";
    DOM.contentBody.textContent = "";
    return;
  }

  DOM.contentTitle.textContent = String(payload.title || `${overlayState.contentMode} mode`);
  if (payload.body != null) {
    DOM.contentBody.textContent = String(payload.body);
  } else if (payload.text != null) {
    DOM.contentBody.textContent = String(payload.text);
  } else {
    DOM.contentBody.textContent = JSON.stringify(payload, null, 2);
  }

  DOM.iconStrip.innerHTML = "";
  const icons = Array.isArray(payload.icons) ? payload.icons : (payload.icon ? [payload.icon] : []);
  for (const icon of icons) {
    const span = document.createElement("span");
    span.className = "icon-pill";
    span.textContent = String(icon);
    DOM.iconStrip.append(span);
  }
}

function renderAnimationStatus() {
  const label = engine.getActiveBehaviorLabel();
  DOM.animationStatus.textContent = label || "";
  DOM.animationStatus.hidden = !label || overlayState.blanked;
}

window.addEventListener("resize", () => engine.resize());
window.setInterval(renderAnimationStatus, 120);
engine.start();
connect();
window.robotFaceRuntime = {
  engine,
  onMicLevel: (level) => engine.onMicLevel(level),
};
