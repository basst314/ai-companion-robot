import { RobotFaceEngine } from "./engine.js";

const DOM = {
  canvas: document.getElementById("faceCanvas"),
  status: document.getElementById("bridgeStatus"),
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

function connect() {
  const wsParam = new URLSearchParams(window.location.search).get("ws");
  const wsPort = Number.parseInt(wsParam || "", 10);
  const port = Number.isFinite(wsPort) ? wsPort : (Number.parseInt(window.location.port || "80", 10) + 1);
  const socket = new WebSocket(`ws://${window.location.hostname}:${port}`);
  socket.addEventListener("open", () => {
    DOM.status.textContent = "Connected";
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
        speechActive: Boolean(payload.speechActive),
        previewText: payload.previewText || "",
      });
      overlayState.previewText = String(payload.previewText || "");
      overlayState.blanked = Boolean(payload.displaySleepRequested);
      renderOverlays();
      return;
    case "transient_trigger":
      engine.triggerNamedBehavior(payload.name, payload);
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

function renderOverlays() {
  document.body.classList.toggle("display-blanked", overlayState.blanked);
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

window.addEventListener("resize", () => engine.resize());
engine.start();
connect();
window.robotFaceRuntime = { engine };
